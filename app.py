# app.py  (PortPilot 後端)
from flask import Flask, request, jsonify
from flask_cors import CORS
import os, requests
from dotenv import load_dotenv
from flask import make_response

load_dotenv()
app = Flask(__name__)
# 只允許你的前端網域呼叫（本機開發也保留）
CORS(
    app,
    resources={r"/*": {"origins": ["https://portpilot.co", "http://localhost:3000"]}},
    methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    supports_credentials=False,
)

RECAPTCHA_SECRET = os.getenv("RECAPTCHA_SECRET")  # 後端用 secret key（不是 site key）

def verify_recaptcha(token, remote_ip=None):
    """Google reCAPTCHA v2 驗證"""
    url = "https://www.google.com/recaptcha/api/siteverify"
    payload = {"secret": RECAPTCHA_SECRET, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip
    r = requests.post(url, data=payload, timeout=5)
    data = r.json()
    return data.get("success", False), data

@app.before_request
def handle_preflight():
    # 讓所有路由的 OPTIONS 預檢請求直接通過
    if request.method == "OPTIONS":
        resp = make_response("", 204)
        # 這三個 header Flask-CORS 也會補，但有些環境/狀態碼時手動寫最穩
        resp.headers["Access-Control-Allow-Origin"] = "https://portpilot.co"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return resp

# 健康檢查（方便你測試 API 有沒有活著）
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "service": "portpilot-api"}), 200

# 統一處理登入（同時支援 /login 和 /api/login，避免前端路徑不一致）
@app.route("/login", methods=["POST", "OPTIONS"])
@app.route("/api/login", methods=["POST", "OPTIONS"])
def login():
    if request.method == "OPTIONS":
        # 真正處理在 before_request；這裡仍回 204 以避免進入後續驗證邏輯
        return ("", 204)

    data = request.get_json(silent=True) or {}
    email = data.get("email", "").strip()
    password = data.get("password", "")
    # recaptcha_token = data.get("recaptcha_token") or data.get("recaptchaToken")

    if email == "admin@test.com" and password == "pp1234":
        return jsonify({"ok": True, "user": {"email": email, "role": "admin"}}), 200

    return jsonify({"ok": False, "error": "invalid_credentials"}), 401

if __name__ == "__main__":
    # 本機啟動
    app.run(host="0.0.0.0", port=5000, debug=True)
