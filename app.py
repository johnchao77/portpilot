# app.py  (PortPilot API)
# ─────────────────────────────────────────────────────────────────────
# - CORS：允許 portpilot.co、www 與本機 3000
# - SQLite：資料表 pps_rows，欄位 data(JSON)、created_at、updated_at
# - 種子資料：啟動時若空表，從 seed/my_containers.json 載入
# - API：
#     GET  /my-containers   (兼容 /pps)   → {"ok":true,"rows":[...]}
#     PUT  /my-containers   (兼容 /pps)   → 覆蓋全部資料
#     POST /login                         → 簡單帳密驗證
#     GET  /health
# - 日期處理：支援 mm/dd/yy、mm/dd/yyyy；若只填 mm/dd 會自動補今年
#   日期時間支援 mm/dd/yy hh:mm am/pm、mm/dd yyyy 同樣補年
# ─────────────────────────────────────────────────────────────────────

from flask import Flask, request, jsonify, make_response, g
from flask_cors import CORS
from dotenv import load_dotenv

import os
import re
import json
import sqlite3
from datetime import datetime, timezone
import requests  # 如未使用 reCAPTCHA 可保留不呼叫

# ── 基本設定 ─────────────────────────────────────────────────────────
load_dotenv()
app = Flask(__name__)

ALLOWED_ORIGINS = {
    "https://portpilot.co",
    "https://www.portpilot.co",
    "http://localhost:3000",
}
CORS(
    app,
    resources={r"/*": {"origins": list(ALLOWED_ORIGINS)}},
    methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    supports_credentials=False,
)

def _is_allowed_origin(origin: str) -> bool:
    return origin in ALLOWED_ORIGINS

@app.before_request
def _handle_preflight():
    """統一處理所有路由的預檢請求，並回應動態的 Access-Control-Allow-Origin。"""
    if request.method == "OPTIONS":
        origin = request.headers.get("Origin", "")
        if _is_allowed_origin(origin):
            resp = make_response("", 204)
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Headers"] = request.headers.get(
                "Access-Control-Request-Headers", "Content-Type, Authorization"
            )
            resp.headers["Access-Control-Allow-Methods"] = request.headers.get(
                "Access-Control-Request-Method", "GET, POST, PUT, OPTIONS"
            )
            resp.headers["Access-Control-Max-Age"] = "86400"
            return resp
        return ("", 403)

# 可選：reCAPTCHA（目前登入流程先不強制）
RECAPTCHA_SECRET = os.getenv("RECAPTCHA_SECRET")
def verify_recaptcha(token, remote_ip=None):
    if not RECAPTCHA_SECRET:
        return True, {"skipped": True}
    url = "https://www.google.com/recaptcha/api/siteverify"
    payload = {"secret": RECAPTCHA_SECRET, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip
    r = requests.post(url, data=payload, timeout=5)
    data = r.json()
    return data.get("success", False), data

# ── 路徑/環境設定 ────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(__file__)
SEED_PATH = os.path.join(BASE_DIR, "seed", "my_containers.json")  # 種子資料 JSON
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.getcwd(), "portpilot.sqlite"))

# ── 日期與格式化工具（放在最前以供初始化使用） ─────────────────────────
DATE_FIELDS = ["ETD", "ETA", "Arrived", "LFD", "Appt Date", "LRD", "Returned Date"]
DATETIME_FIELDS = ["Delivered DateTime", "Emptied DateTime"]

def _utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def _ensure_year_for_date(s: str) -> str:
    """若只輸入 'M/D'，自動補上今年：'M/D' → 'M/D/THIS_YEAR'。"""
    if not s:
        return s
    if re.fullmatch(r"\s*\d{1,2}/\d{1,2}\s*", s):
        return f"{s.strip()}/{datetime.now().year}"
    return s

def _ensure_year_for_datetime(s: str) -> str:
    """若輸入 'M/D hh:mm am/pm'，自動補上今年。"""
    if not s:
        return s
    if re.fullmatch(r"\s*\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}\s*(am|pm|AM|PM)\s*", s):
        return f"{s.strip()} {datetime.now().year}"
    return s

def _parse_date(s: str) -> str:
    if not s:
        return ""
    s = _ensure_year_for_date(s)
    for fmt in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    return ""

def _parse_datetime(s: str) -> str:
    if not s:
        return ""
    s = _ensure_year_for_datetime(s)
    for fmt in ("%m/%d/%y %I:%M %p", "%m/%d/%Y %I:%M %p"):
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    return ""

def normalize_row(row: dict) -> dict:
    """把日期/日期時間欄位正規化為 ISO 格式（字串）。"""
    out = dict(row or {})
    for k in DATE_FIELDS:
        if k in out:
            out[k] = _parse_date(out.get(k, ""))
    for k in DATETIME_FIELDS:
        if k in out:
            out[k] = _parse_datetime(out.get(k, ""))
    return out

# ── SQLite helpers ─────────────────────────────────────────────────
def get_db():
    if "_db" not in g:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        # 提升併發穩定性
        conn.execute("PRAGMA journal_mode=WAL")
        g._db = conn
    return g._db

@app.teardown_appcontext
def _close_db(_=None):
    db = g.pop("_db", None)
    if db is not None:
        db.close()

def load_seed_rows() -> list:
    """讀 seed/my_containers.json；不可用/找不到則回空陣列。"""
    try:
        if os.path.exists(SEED_PATH):
            with open(SEED_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                app.logger.warning("Seed JSON should be a list of rows. Skipped.")
    except Exception as e:
        app.logger.warning(f"Seed load failed: {e}")
    return []

def init_db_and_seed_if_empty():
    """建立資料表；若空表則匯入種子資料（並做日期正規化）。"""
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS pps_rows (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          data TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    # 若空表 → 匯入種子資料
    cnt = db.execute("SELECT COUNT(*) FROM pps_rows").fetchone()[0]
    if cnt == 0:
        seed_rows = load_seed_rows()
        if seed_rows:
            now = _utc_now_str()
            normed = [normalize_row(r) for r in seed_rows]
            db.executemany(
                "INSERT INTO pps_rows (data, created_at, updated_at) VALUES (?, ?, ?)",
                [(json.dumps(r, ensure_ascii=False), now, now) for r in normed],
            )
            db.commit()
            app.logger.info(f"Seeded {len(normed)} rows from {SEED_PATH}")

# 在所有函式定義完成後，才執行初始化（避免 NameError）
try:
    with app.app_context():
        init_db_and_seed_if_empty()
except Exception as e:
    app.logger.warning(f"DB init skipped/failed: {e}")

# ── API：My Containers（兼容 /pps） ────────────────────────────────
@app.get("/my-containers")
@app.get("/pps")   # 兼容舊路徑
def api_list():
    db = get_db()
    cur = db.execute("SELECT data FROM pps_rows ORDER BY id")
    rows = [json.loads(r["data"]) for r in cur.fetchall()]
    return jsonify({"ok": True, "rows": rows})

@app.put("/my-containers")
@app.put("/pps")   # 兼容舊路徑
def api_save_all():
    payload = request.get_json(silent=True) or {}
    rows = payload.get("rows", [])
    if not isinstance(rows, list):
        return jsonify({"ok": False, "error": "Invalid payload: rows must be a list"}), 400

    normed = [normalize_row(r) for r in rows]
    db = get_db()
    now = _utc_now_str()
    with db:
        db.execute("DELETE FROM pps_rows")
        db.executemany(
            "INSERT INTO pps_rows (data, created_at, updated_at) VALUES (?, ?, ?)",
            [(json.dumps(r, ensure_ascii=False), now, now) for r in normed],
        )
    return jsonify({"ok": True, "count": len(normed)})

# ── 健康檢查 ───────────────────────────────────────────────────────
@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "portpilot-api"}), 200

# ── 登入（簡化版） ─────────────────────────────────────────────────
@app.route("/login", methods=["POST", "OPTIONS"])
@app.route("/api/login", methods=["POST", "OPTIONS"])
def login():
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    # 若要啟用 reCAPTCHA，可從 data 取 token，呼叫 verify_recaptcha()

    if email == "admin@test.com" and password == "pp1234":
        return jsonify({"ok": True, "user": {"email": email, "role": "admin"}}), 200

    return jsonify({"ok": False, "error": "invalid_credentials"}), 401

# ── 本機執行 ───────────────────────────────────────────────────────
if __name__ == "__main__":
    # 開發模式
    app.run(host="0.0.0.0", port=5000, debug=True)
