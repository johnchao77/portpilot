# app.py  (PortPilot 後端)
from flask import Flask, request, jsonify, make_response
from flask_cors import CORS
import os, requests
from dotenv import load_dotenv
import os, json, sqlite3
from datetime import datetime
from flask import g, request, jsonify



load_dotenv()
app = Flask(__name__)

# ====== CORS 設定 ======
ALLOWED_ORIGINS = {
    "https://portpilot.co",
    "https://www.portpilot.co",
    "http://localhost:3000",
}

# 讓所有路由都有 CORS header（POST/GET/OPTIONS 都能附上）
CORS(
    app,
    resources={r"/*": {"origins": list(ALLOWED_ORIGINS)}},
    methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    supports_credentials=False,
)

# === 2) config：從環境變數讀 DB_PATH（預設為專案目錄檔案，以防本機測試用） ===
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.getcwd(), "portpilot.sqlite"))

# 要正規化的欄位（和你 Excel / JSON 對齊）
DATE_FIELDS = ["ETD","ETA","Arrived","LFD","Appt Date","LRD","Returned Date"]
DATETIME_FIELDS = ["Delivered DateTime","Emptied DateTime"]


def _is_allowed_origin(origin: str) -> bool:
    return origin in ALLOWED_ORIGINS

@app.before_request
def handle_preflight():
    """
    讓所有路由的 OPTIONS 預檢請求直接通過（204），
    並且動態回覆 Access-Control-Allow-Origin，避免固定寫死造成不匹配。
    """
    if request.method == "OPTIONS":
        origin = request.headers.get("Origin", "")
        # 如果是允許的來源，就回 204 + CORS headers
        if _is_allowed_origin(origin):
            resp = make_response("", 204)
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
            # 照實回傳瀏覽器帶來的預檢需求
            req_headers = request.headers.get("Access-Control-Request-Headers", "Content-Type, Authorization")
            req_method = request.headers.get("Access-Control-Request-Method", "GET, POST, OPTIONS")
            resp.headers["Access-Control-Allow-Headers"] = req_headers
            resp.headers["Access-Control-Allow-Methods"] = req_method
            resp.headers["Access-Control-Max-Age"] = "86400"
            return resp
        # 不是允許來源就回 403（也可改成 204，但為安全性這裡回 403）
        return ("", 403)

# ====== reCAPTCHA（目前可先不啟用，先把登入流程通了再開） ======
RECAPTCHA_SECRET = os.getenv("RECAPTCHA_SECRET")  # 後端用 secret key（不是 site key）

def verify_recaptcha(token, remote_ip=None):
    url = "https://www.google.com/recaptcha/api/siteverify"
    payload = {"secret": RECAPTCHA_SECRET, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip
    r = requests.post(url, data=payload, timeout=5)
    data = r.json()
    return data.get("success", False), data

# === 3) SQLite helpers ===
def get_db():
    if "_db" not in g:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        g._db = conn
    return g._db

@app.teardown_appcontext
def close_db(_=None):
    db = g.pop("_db", None)
    if db is not None:
        db.close()

def init_db():
    db = get_db()
    # 最簡 schema：每列資料以 JSON 儲存，日後欄位變動也不怕
    db.execute("""
        CREATE TABLE IF NOT EXISTS pps_rows (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          data TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
    """)
    db.commit()

# ✅ Flask 3：啟動時在應用程式 context 中初始化 DB
try:
    with app.app_context():
        init_db()
except Exception as e:
    app.logger.warning(f"DB init skipped/failed: {e}")

# === 4) 日期正規化：前端送來 mm/dd/yy 或 mm/dd/yy hh:mm am/pm，這裡轉成 ISO 存 DB ===
def _parse_date(s: str):
    if not s: return ""
    for fmt in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    return ""  # 任何解析失敗就存空字串

def _parse_datetime(s: str):
    if not s: return ""
    for fmt in ("%m/%d/%y %I:%M %p", "%m/%d/%Y %I:%M %p"):
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    return ""

def normalize_row(row: dict):
    # 複製一份避免原資料被就地修改
    out = dict(row or {})
    for k in DATE_FIELDS:
        if k in out:
            out[k] = _parse_date(out.get(k, ""))
    for k in DATETIME_FIELDS:
        if k in out:
            out[k] = _parse_datetime(out.get(k, ""))
    return out

# === 5) API：讀取資料（登入驗證你已在前端做 ProtectedRoute；後端先不額外擋） ===
@app.get("/my-containers")
@app.get("/pps") # 保留相容性
def pps_list():
    db = get_db()
    cur = db.execute("SELECT data FROM pps_rows ORDER BY id")
    rows = [json.loads(r["data"]) for r in cur.fetchall()]
    return jsonify({"ok": True, "rows": rows})

# === 6) API：整批儲存（最簡 MVP：覆蓋全部資料）===
@app.put("/my-containers")
@app.put("/pps") # 保留相容性
def pps_save_all():
    payload = request.get_json(silent=True) or {}
    rows = payload.get("rows", [])
    if not isinstance(rows, list):
        return jsonify({"ok": False, "error": "Invalid payload: rows must be a list"}), 400

    normed = [normalize_row(r) for r in rows]

    db = get_db()
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with db:
        db.execute("DELETE FROM pps_rows")
        db.executemany(
            "INSERT INTO pps_rows (data, created_at, updated_at) VALUES (?, ?, ?)",
            [(json.dumps(r, ensure_ascii=False), now, now) for r in normed],
        )
    return jsonify({"ok": True, "count": len(normed)})

# ====== 健康檢查 ======
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "service": "portpilot-api"}), 200

# ====== 登入 API（/login 以及 /api/login 兩個路徑都支援） ======
@app.route("/login", methods=["POST", "OPTIONS"])
@app.route("/api/login", methods=["POST", "OPTIONS"])
def login():
    # OPTIONS 會在 before_request 已處理掉，這裡保險起見仍回 204
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    # recaptcha_token = data.get("recaptcha_token") or data.get("recaptchaToken")

    # 簡單驗證（先讓流程通）
    if email == "admin@test.com" and password == "pp1234":
        return jsonify({"ok": True, "user": {"email": email, "role": "admin"}}), 200

    return jsonify({"ok": False, "error": "invalid_credentials"}), 401


if __name__ == "__main__":
    # 本機啟動
    app.run(host="0.0.0.0", port=5000, debug=True)
