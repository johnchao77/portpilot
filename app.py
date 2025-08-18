# app.py  (PortPilot 後端)
from flask import Flask, request, jsonify, make_response
from flask_cors import CORS
import os, requests
from dotenv import load_dotenv

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
