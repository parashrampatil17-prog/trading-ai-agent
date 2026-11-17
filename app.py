import os
from flask import Flask, redirect, request
from kiteconnect import KiteConnect

app = Flask(__name__)

API_KEY = os.environ.get("KITE_API_KEY")
API_SECRET = os.environ.get("KITE_API_SECRET")

@app.route("/")
def home():
    if not API_KEY:
        return "Kite API Key missing"
    kite = KiteConnect(api_key=API_KEY)
    return f'<h2>Trading AI Agent</h2><a href="{kite.login_url()}">Login with Zerodha</a>'

@app.route("/kite/callback")
def callback():
    request_token = request.args.get("request_token")

    if not request_token:
        return "Login failed. Please try again."

    try:
        kite = KiteConnect(api_key=API_KEY)
        data = kite.generate_session(
            request_token,
            api_secret=API_SECRET
        )

        return "Zerodha Login Successful! Trading AI Agent Connected."

    except Exception:
        return "Authentication failed. Please login again."

@app.route("/health")
def health():
    return "Trading AI Agent Running"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
