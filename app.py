# app.py  (PortPilot 後端)
from flask import Flask, request, jsonify
from flask_cors import CORS
import os, requests
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
# 允許本機 React 來源呼叫
CORS(app, resources={r"/*": {"origins": ["http://localhost:3000", "http://127.0.0.1:3000"]}})

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

@app.route("/api/health", methods=["GET"])
def health():
    return {"ok": True}, 200

@app.route("/login", methods=["POST"])
def login():
    body = request.get_json(force=True)
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")
    token = body.get("recaptcha_token")

    if not token:
        return jsonify({"message": "Missing reCAPTCHA token"}), 400

    ok, detail = verify_recaptcha(token, request.remote_addr)
    if not ok:
        return jsonify({"message": "reCAPTCHA failed", "detail": detail}), 400

    # ---- 硬碼帳密（測試用）----
    if email == "admin@test.com" and password == "pp1234":
        # 未來可在這裡產生 JWT 與回傳權限群組
        return jsonify({
            "message": "Login OK",
            "user": {"email": email, "group": "admin"}
        }), 200
    else:
        return jsonify({"message": "帳號或密碼不正確"}), 401

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
