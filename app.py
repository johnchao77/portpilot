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

KV_MY_CONTAINERS = "my-containers"

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
    # 你原本的 pps_rows（保留，不破壞相容）
    db.execute("""
        CREATE TABLE IF NOT EXISTS pps_rows (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          data TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
    """)
    # ➕ 新增一張 KV 表，讓 /my-containers 用 JSON 整批存取
    db.execute("""
        CREATE TABLE IF NOT EXISTS kv (
          k TEXT PRIMARY KEY,
          v TEXT NOT NULL
        )
    """)
    # 可選：提升 SQLite 併發穩定性
    db.execute("PRAGMA journal_mode=WAL")
    db.commit()

def kv_get(key: str):
    db = get_db()
    row = db.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
    return json.loads(row["v"]) if row else None

def kv_set(key: str, value):
    db = get_db()
    db.execute(
        "INSERT INTO kv(k, v) VALUES(?, ?) "
        "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (key, json.dumps(value, ensure_ascii=False)),
    )
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

# === 5) API：讀取資料（my-containers 走 KV；首次可用 SEED_ROWS 初始化） ===
@app.get("/my-containers")
@app.get("/pps")  # 保留相容性
def pps_list():
    rows = kv_get(KV_MY_CONTAINERS)
    if rows is None:
        # 如果你有定義 SEED_ROWS（Excel 轉出的初始資料），就寫入；沒有就用空陣列
        rows = SEED_ROWS if 'SEED_ROWS' in globals() else []
        kv_set(KV_MY_CONTAINERS, rows)
    return jsonify({"ok": True, "rows": rows})

# === 6) API：整批儲存（覆蓋；前端送上 rows:list[dict]） ===
@app.put("/my-containers")
@app.put("/pps")  # 保留相容性
def pps_save_all():
    payload = request.get_json(silent=True) or {}
    rows = payload.get("rows", [])
    if not isinstance(rows, list):
        return jsonify({"ok": False, "error": "Invalid payload: rows must be a list"}), 400
    kv_set(KV_MY_CONTAINERS, rows)
    return jsonify({"ok": True, "count": len(rows)})

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

SEED_ROWS = [
  {"order_no":"20250815-昇达信-91708-A5","status":"Planning","drayage":null,"warehouse":null,"mbl_no":null,"container":null,"etd":null,"eta":null,"pod":null,"arrived":null,"lfd":null,"appt_date":null,"lrd":null,"delivered_dt":null,"emptied_dt":null,"returned_date":null},
  {"order_no":"20250818-昇达信-91708-A6","status":"Planning","drayage":null,"warehouse":null,"mbl_no":null,"container":null,"etd":null,"eta":null,"pod":null,"arrived":null,"lfd":null,"appt_date":null,"lrd":null,"delivered_dt":null,"emptied_dt":null,"returned_date":null},
  {"order_no":"20250611-融达通-91708-A1","status":"Offshore","drayage":null,"warehouse":null,"mbl_no":"COSU6419881880","container":"BEAU6205263","etd":"2025-06-17","eta":"2025-07-01","pod":"Los Angeles,CA","arrived":null,"lfd":null,"appt_date":null,"lrd":null,"delivered_dt":null,"emptied_dt":null,"returned_date":null},
  {"order_no":"20250708-昇达信-91730-A1","status":"Offshore","drayage":null,"warehouse":null,"mbl_no":"COSU6423819390","container":"TCKU7814545","etd":"2025-07-15","eta":"2025-08-01","pod":"Long Beach,CA","arrived":null,"lfd":null,"appt_date":null,"lrd":null,"delivered_dt":null,"emptied_dt":null,"returned_date":null},
  {"order_no":"20241120-融达通-91708-2","status":"Offshore","drayage":null,"warehouse":null,"mbl_no":"ZIMUNGB20496768","container":"JXLU6235954","etd":"2024-11-25","eta":"2024-12-17","pod":"Los Angeles,CA","arrived":null,"lfd":null,"appt_date":null,"lrd":null,"delivered_dt":null,"emptied_dt":null,"returned_date":null},
  {"order_no":"20250718-昇达信-91730-A2","status":"Offshore","drayage":null,"warehouse":null,"mbl_no":"COSU6424102500","container":"HJCU2721785","etd":"2025-07-26","eta":"2025-08-11","pod":"Long Beach,CA","arrived":null,"lfd":null,"appt_date":null,"lrd":null,"delivered_dt":null,"emptied_dt":null,"returned_date":null},
  {"order_no":"20250731-昇达信-91730-A3","status":"Offshore","drayage":null,"warehouse":null,"mbl_no":"COSU6424780250","container":"DFSZ6100186","etd":"2025-08-06","eta":"2025-08-23","pod":"Long Beach,CA","arrived":null,"lfd":null,"appt_date":null,"lrd":null,"delivered_dt":null,"emptied_dt":null,"returned_date":null},
  {"order_no":"20250805-昇达信-91730-A4","status":"Offshore","drayage":null,"warehouse":null,"mbl_no":"COSU6425026900","container":"XINU1305302","etd":"2025-08-12","eta":"2025-08-29","pod":"Long Beach,CA","arrived":null,"lfd":null,"appt_date":null,"lrd":null,"delivered_dt":null,"emptied_dt":null,"returned_date":null},
  {"order_no":"20250808-昇达信-91730-A5","status":"Offshore","drayage":null,"warehouse":null,"mbl_no":"COSU6425173490","container":"TGBU7996270","etd":"2025-08-15","eta":"2025-09-01","pod":"Long Beach,CA","arrived":null,"lfd":null,"appt_date":null,"lrd":null,"delivered_dt":null,"emptied_dt":null,"returned_date":null},
  {"order_no":"20250810-昇达信-91730-A6","status":"Offshore","drayage":null,"warehouse":null,"mbl_no":"COSU6425248910","container":"TRHU7596807","etd":"2025-08-17","eta":"2025-09-03","pod":"Long Beach,CA","arrived":null,"lfd":null,"appt_date":null,"lrd":null,"delivered_dt":null,"emptied_dt":null,"returned_date":null},
  {"order_no":"20250904-昇达信-91730-A7","status":"Offshore","drayage":null,"warehouse":null,"mbl_no":"COSU6426604790","container":"OOLU0849154","etd":"2025-09-09","eta":"2025-09-26","pod":"Long Beach,CA","arrived":null,"lfd":null,"appt_date":null,"lrd":null,"delivered_dt":null,"emptied_dt":null,"returned_date":null},
  {"order_no":"20250730-FarStep-A1","status":"Offshore","drayage":"Carrier","warehouse":"WHSE 2","mbl_no":2147711080,"container":"YMMU9748108","etd":"2025-08-04","eta":"2025-08-23","pod":"Los Angeles,CA","arrived":null,"lfd":null,"appt_date":null,"lrd":null,"delivered_dt":null,"emptied_dt":null,"returned_date":null},
  {"order_no":"20250611-昇达信-91708-A3","status":"Port Hold","drayage":"Carrier","warehouse":"WHSE 1","mbl_no":2150799750,"container":"WHLU5271737","etd":"2025-06-24","eta":"2025-07-09","pod":"Los Angeles,CA","arrived":"2025-07-09","lfd":"2025-07-13","appt_date":"2025-07-15","lrd":"2025-07-16","delivered_dt":"2025-07-15 10:30","emptied_dt":"2025-07-16 14:00","returned_date":"2025-07-17"},
  {"order_no":"20250708-昇达信-91708-A4","status":"Customs Hold","drayage":"Carrier","warehouse":"WHSE 1","mbl_no":2152142700,"container":"SEGU6986261","etd":"2025-07-18","eta":"2025-08-05","pod":"Los Angeles,CA","arrived":"2025-08-05","lfd":"2025-08-09","appt_date":"2025-08-08","lrd":"2025-08-11","delivered_dt":"2025-08-08 12:15","emptied_dt":"2025-08-09 09:40","returned_date":"2025-08-10"},
  {"order_no":"20240815-昇达信-91708-1","status":"Customs Hold","drayage":"Carrier","warehouse":"WHSE 1","mbl_no":"OOLU2778385341","container":"OOLU9734588","etd":"2024-08-19","eta":"2024-09-07","pod":"Los Angeles,CA","arrived":"2024-09-07","lfd":"2024-09-11","appt_date":"2024-09-10","lrd":"2024-09-13","delivered_dt":"2024-09-10 15:20","emptied_dt":"2024-09-11 08:30","returned_date":"2024-09-12"},
  {"order_no":"20240910-昇达信-91708-2","status":"Customs Hold","drayage":"Carrier","warehouse":"WHSE 1","mbl_no":"OOLU2783407620","container":"OOLU9832741","etd":"2024-09-14","eta":"2024-10-03","pod":"Los Angeles,CA","arrived":"2024-10-03","lfd":"2024-10-07","appt_date":"2024-10-06","lrd":"2024-10-08","delivered_dt":"2024-10-06 10:00","emptied_dt":"2024-10-07 10:45","returned_date":"2024-10-08"},
  {"order_no":"20241005-昇达信-91708-3","status":"Customs Hold","drayage":"Carrier","warehouse":"WHSE 1","mbl_no":"OOLU2787450020","container":"OOLU9923456","etd":"2024-10-10","eta":"2024-10-29","pod":"Los Angeles,CA","arrived":"2024-10-29","lfd":"2024-11-02","appt_date":"2024-11-01","lrd":"2024-11-03","delivered_dt":"2024-11-01 11:30","emptied_dt":"2024-11-02 09:10","returned_date":"2024-11-03"},
  {"order_no":"20241101-昇达信-91708-4","status":"Customs Hold","drayage":"Carrier","warehouse":"WHSE 1","mbl_no":"OOLU2791501210","container":"OOLU9012345","etd":"2024-11-05","eta":"2024-11-24","pod":"Los Angeles,CA","arrived":"2024-11-24","lfd":"2024-11-28","appt_date":"2024-11-27","lrd":"2024-11-29","delivered_dt":"2024-11-27 14:20","emptied_dt":"2024-11-28 10:00","returned_date":"2024-11-29"},
  {"order_no":"20241203-昇达信-91708-5","status":"Customs Hold","drayage":"Carrier","warehouse":"WHSE 1","mbl_no":"OOLU2795506750","container":"OOLU9123456","etd":"2024-12-07","eta":"2024-12-26","pod":"Los Angeles,CA","arrived":"2024-12-26","lfd":"2024-12-30","appt_date":"2024-12-29","lrd":"2024-12-31","delivered_dt":"2024-12-29 16:00","emptied_dt":"2024-12-30 09:30","returned_date":"2024-12-31"},
  {"order_no":"20250107-昇达信-91708-6","status":"Customs Hold","drayage":"Carrier","warehouse":"WHSE 1","mbl_no":"OOLU2799520110","container":"OOLU9234567","etd":"2025-01-11","eta":"2025-01-30","pod":"Los Angeles,CA","arrived":"2025-01-30","lfd":"2025-02-03","appt_date":"2025-02-02","lrd":"2025-02-04","delivered_dt":"2025-02-02 13:10","emptied_dt":"2025-02-03 10:20","returned_date":"2025-02-04"},
  {"order_no":"20250210-昇达信-91708-7","status":"Customs Hold","drayage":"Carrier","warehouse":"WHSE 1","mbl_no":"OOLU2803604100","container":"OOLU9345678","etd":"2025-02-14","eta":"2025-03-05","pod":"Los Angeles,CA","arrived":"2025-03-05","lfd":"2025-03-09","appt_date":"2025-03-08","lrd":"2025-03-10","delivered_dt":"2025-03-08 10:30","emptied_dt":"2025-03-09 08:50","returned_date":"2025-03-10"},
  {"order_no":"20250314-昇达信-91708-8","status":"Customs Hold","drayage":"Carrier","warehouse":"WHSE 1","mbl_no":"OOLU2807702010","container":"OOLU9456789","etd":"2025-03-18","eta":"2025-04-06","pod":"Los Angeles,CA","arrived":"2025-04-06","lfd":"2025-04-10","appt_date":"2025-04-09","lrd":"2025-04-11","delivered_dt":"2025-04-09 09:40","emptied_dt":"2025-04-10 09:20","returned_date":"2025-04-11"},
  {"order_no":"20250415-昇达信-91708-9","status":"Customs Hold","drayage":"Carrier","warehouse":"WHSE 1","mbl_no":"OOLU2811800150","container":"OOLU9567890","etd":"2025-04-19","eta":"2025-05-08","pod":"Los Angeles,CA","arrived":"2025-05-08","lfd":"2025-05-12","appt_date":"2025-05-11","lrd":"2025-05-13","delivered_dt":"2025-05-11 11:50","emptied_dt":"2025-05-12 10:30","returned_date":"2025-05-13"},
  {"order_no":"20250516-昇达信-91708-10","status":"Customs Hold","drayage":"Carrier","warehouse":"WHSE 1","mbl_no":"OOLU2815855000","container":"OOLU9678901","etd":"2025-05-20","eta":"2025-06-08","pod":"Los Angeles,CA","arrived":"2025-06-08","lfd":"2025-06-12","appt_date":"2025-06-11","lrd":"2025-06-13","delivered_dt":"2025-06-11 14:10","emptied_dt":"2025-06-12 09:20","returned_date":"2025-06-13"},
  {"order_no":"20250801-昇达信-91708-A8","status":"Offshore","drayage":"Carrier","warehouse":"WHSE 1","mbl_no":2154302820,"container":"OOCU8646981","etd":"2024-12-09","eta":"2024-12-27","pod":"Los Angeles,CA","arrived":"2025-08-10","lfd":"2025-08-15","appt_date":"2025-08-13","lrd":"2025-08-17","delivered_dt":"2025-08-13 16:10","emptied_dt":"2025-08-14 09:00","returned_date":"2025-08-18"}
]

if __name__ == "__main__":
    # 本機啟動
    app.run(host="0.0.0.0", port=5000, debug=True)
