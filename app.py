from flask import Flask

app = Flask(__name__)

@app.route("/")
def home():
    return "Hello PortPilot.co 🎉"

if __name__ == "__main__":
    # 本機測試用，Render 會用 gunicorn 啟動
    app.run(host="0.0.0.0", port=5000)
