import os
from flask import Flask, redirect, request, jsonify, render_template_string
from kiteconnect import KiteConnect

app = Flask(__name__)

API_KEY = os.environ.get("KITE_API_KEY")
API_SECRET = os.environ.get("KITE_API_SECRET")

kite = KiteConnect(api_key=API_KEY) if API_KEY else None
ACCESS_TOKEN = None


@app.route("/")
def home():
    if not kite:
        return "KITE_API_KEY missing"

    if not ACCESS_TOKEN:
        return '<h2>Trading AI Agent</h2><a href="/login">Login with Zerodha</a>'

    return render_template_string("""
    <html>
    <head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Trading AI Dashboard</title>
    <style>
    body {font-family:Arial;background:#101827;color:white;padding:20px}
    .card {background:#243247;padding:20px;margin:15px 0;border-radius:12px}
    h1 {color:#42d392}
    .price {font-size:28px;font-weight:bold}
    </style>
    </head>
    <body>
    <h1>Trading AI Dashboard</h1>
    <p>Zerodha Connected</p>

    <div class="card">
    <h2>NIFTY 50</h2>
    <div id="nifty" class="price">Loading...</div>
    </div>

    <div class="card">
    <h2>SENSEX</h2>
    <div id="sensex" class="price">Loading...</div>
    </div>

    <div class="card">
    <h3>Market Scanner</h3>
    <p id="status">Fetching market data...</p>
    </div>

    <p>Auto refresh: 20 seconds</p>

    <script>
    async function updateMarket() {
        try {
            const response = await fetch('/api/market');
            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.error || 'Market data unavailable');
            }

            for (const name of ['NIFTY 50', 'SENSEX']) {
                const item = data[name];
                const id = name === 'NIFTY 50' ? 'nifty' : 'sensex';
                const change = item.price - item.close;
                const percent = item.close ?
                    (change / item.close * 100).toFixed(2) : '0.00';

                document.getElementById(id).innerHTML =
                    item.price.toLocaleString('en-IN', {
                        maximumFractionDigits: 2
                    }) +
                    '<br><small>Change: ' +
                    change.toFixed(2) + ' (' + percent + '%)</small>';
            }

            document.getElementById('status').innerText =
                'Market data updated successfully';

        } catch (error) {
            document.getElementById('status').innerText =
                error.message;
        }
    }

    updateMarket();
    setInterval(updateMarket, 20000);
    </script>
    </body>
    </html>
    """)


@app.route("/login")
def login():
    return redirect(kite.login_url())


@app.route("/kite/callback")
def callback():
    global ACCESS_TOKEN

    request_token = request.args.get("request_token")

    if not request_token or not API_SECRET:
        return "Login failed. Please try again."

    try:
        data = kite.generate_session(
            request_token,
            api_secret=API_SECRET
        )

        ACCESS_TOKEN = data["access_token"]
        kite.set_access_token(ACCESS_TOKEN)

        return redirect("/")

    except Exception:
        return "Authentication failed. Please login again."


@app.route("/api/market")
def market():
    if not ACCESS_TOKEN:
        return jsonify({"error": "Please login with Zerodha"}), 401

    try:
        quotes = kite.ohlc([
            "NSE:NIFTY 50",
            "BSE:SENSEX"
        ])

        result = {}

        for name in ["NSE:NIFTY 50", "BSE:SENSEX"]:
            if name not in quotes:
                continue

            item = quotes[name]

            label = "NIFTY 50" if "NIFTY" in name else "SENSEX"

            result[label] = {
                "price": item["last_price"],
                "close": item["ohlc"]["close"]
            }

        return jsonify(result)

    except Exception:
        return jsonify({
            "error": "Market data unavailable. Please check login."
        }), 502


@app.route("/health")
def health():
    return "Trading AI Agent Running"


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
