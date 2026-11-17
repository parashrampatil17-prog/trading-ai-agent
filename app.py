import os
import requests
from datetime import datetime
from flask import Flask, redirect, request, jsonify, render_template_string
from kiteconnect import KiteConnect
from options_engine import get_option_analysis

app = Flask(__name__)

API_KEY = os.environ.get("KITE_API_KEY")
API_SECRET = os.environ.get("KITE_API_SECRET")

kite = KiteConnect(api_key=API_KEY) if API_KEY else None
ACCESS_TOKEN = None
TG_TOKEN = os.environ.get("TG_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")


def send_telegram(message):
    if not TG_TOKEN or not TG_CHAT_ID:
        return False

    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"

        response = requests.post(
            url,
            data={
                "chat_id": TG_CHAT_ID,
                "text": message
            },
            timeout=10
        )

        response.raise_for_status()
        return True

    except Exception as e:
        print("Telegram Error:", e)
        return False
STOCKS = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN",
    "INFY", "TCS", "LT", "AXISBANK", "BHARTIARTL",
    "ITC", "TATAMOTORS", "MARUTI", "SUNPHARMA",
    "NTPC", "POWERGRID"
]


# ---------------- STOCK DATA ----------------

def get_stock_data(symbol):
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{symbol}.NS?range=3mo&interval=1d"
    )

    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15
    )

    response.raise_for_status()

    result = response.json()["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]

    closes = quote["close"]
    highs = quote["high"]
    lows = quote["low"]

    rows = [
        (c, h, l)
        for c, h, l in zip(closes, highs, lows)
        if c is not None and h is not None and l is not None
    ]

    return rows


# ---------------- EMA ----------------

def ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)
    result = sum(values[:period]) / period

    for value in values[period:]:
        result = (value - result) * multiplier + result

    return result


# ---------------- RSI ----------------

def calculate_rsi(values, period=14):
    if len(values) <= period:
        return None

    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    gains = [max(x, 0) for x in changes]
    losses = [max(-x, 0) for x in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


# ---------------- SWING SCANNER ----------------

def scan_stocks():
    results = []

    for stock in STOCKS:
        try:
            rows = get_stock_data(stock)

            if len(rows) < 50:
                continue

            closes = [r[0] for r in rows]
            highs = [r[1] for r in rows]
            lows = [r[2] for r in rows]

            e20 = ema(closes, 20)
            e50 = ema(closes, 50)
            strength = calculate_rsi(closes)

            if e20 is None or e50 is None or strength is None:
                continue

            signal = "NEUTRAL"

            if e20 > e50 and strength > 50:
                signal = "BULLISH WATCH"

            elif e20 < e50 and strength < 50:
                signal = "BEARISH WATCH"

            results.append({
                "stock": stock,
                "price": round(closes[-1], 2),
                "ema20": round(e20, 2),
                "ema50": round(e50, 2),
                "rsi": round(strength, 2),
                "support": round(min(lows[-20:]), 2),
                "resistance": round(max(highs[-20:]), 2),
                "signal": signal
            })

        except Exception:
            continue

    return results


# ---------------- DASHBOARD ----------------

@app.route("/")
def home():

    if not kite:
        return "KITE_API_KEY missing. Check Render Environment Variables."

    if not ACCESS_TOKEN:
        return '''
        <h2>Trading AI Dashboard</h2>
        <a href="/login">Login with Zerodha</a>
        '''

    return render_template_string("""
<!DOCTYPE html>
<html>

<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trading AI Dashboard</title>

<style>

body {
    font-family: Arial;
    background: #101827;
    color: white;
    padding: 15px;
}

h1 {
    color: #42d392;
}

.card {
    background: #243247;
    padding: 18px;
    margin: 12px 0;
    border-radius: 12px;
}

.price {
    font-size: 27px;
    font-weight: bold;
}

.green {
    color: #42d392;
}

.red {
    color: #ff6565;
}

table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
}

td, th {
    padding: 9px 5px;
    border-bottom: 1px solid #45546b;
    text-align: left;
}

small {
    color: #b7c5d8;
}

button {
    padding: 12px;
    background: #42d392;
    border: 0;
    border-radius: 8px;
    font-weight: bold;
    margin: 5px 0;
}

select {
    padding: 10px;
    border-radius: 6px;
}

</style>
</head>

<body>

<h1>🚀 Trading AI Dashboard</h1>

<p class="green">Zerodha Connected</p>


<!-- MARKET DATA -->

<div class="card">

<h2>NIFTY 50</h2>

<div id="nifty" class="price">Loading...</div>

</div>


<div class="card">

<h2>SENSEX</h2>

<div id="sensex" class="price">Loading...</div>

</div>


<!-- MARKET SCANNER -->

<div class="card">

<h2>📊 Market Scanner</h2>

<p id="marketStatus">Fetching market data...</p>

</div>


<!-- OPTION CHAIN -->

<div class="card">

<h2>📊 Option Chain Analysis</h2>

<select id="underlying">

<option value="NIFTY">NIFTY</option>

<option value="SENSEX">SENSEX</option>

</select>

<button onclick="loadOptions()">
Analyze Option Chain
</button>

<p id="optionStatus">Waiting...</p>

<div id="optionResult"></div>

</div>


<!-- SWING SCANNER -->

<div class="card">

<h2>🔍 Swing Stock Scanner</h2>

<p>EMA 20 | EMA 50 | RSI 14</p>

<button onclick="scan()">
Refresh Scanner
</button>

<p id="scanStatus">Waiting...</p>

<div style="overflow-x:auto">

<table>

<thead>

<tr>
<th>Stock</th>
<th>Price</th>
<th>RSI</th>
<th>Support</th>
<th>Resistance</th>
<th>Signal</th>
</tr>

</thead>

<tbody id="scanner"></tbody>

</table>

</div>

</div>


<!-- RISK MANAGEMENT -->

<div class="card">

<h3>⚠️ Risk Management</h3>

<p>Signals are for research only.</p>

<p>No automatic orders are placed.</p>

</div>

<p><small>Market refresh: 20 seconds</small></p>

<p id="updated"></p>


<script>


// ---------------- MARKET UPDATE ----------------

async function updateMarket() {

    const status = document.getElementById("marketStatus");

    try {

        const response = await fetch("/api/market");

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || "Market API Error");
        }

        for (const name of ["NIFTY 50", "SENSEX"]) {

            const item = data[name];

            const id = name === "NIFTY 50" ? "nifty" : "sensex";

            if (!item) continue;

            const change = item.price - item.close;

            const percent = item.close
                ? change / item.close * 100
                : 0;

            document.getElementById(id).innerHTML =
                item.price.toLocaleString("en-IN") +
                "<br><small>" +
                change.toFixed(2) +
                " (" +
                percent.toFixed(2) +
                "%)</small>";

        }

        status.innerText = "Market data updated successfully";

        document.getElementById("updated").innerText =
            "Last updated: " + new Date().toLocaleTimeString();

    }

    catch (error) {

        status.innerText = error.message || "Market data unavailable";

    }

}


// ---------------- SWING SCANNER ----------------

async function scan() {

    const status = document.getElementById("scanStatus");

    const tbody = document.getElementById("scanner");

    status.innerText = "Scanning stocks...";

    try {

        const response = await fetch("/api/scanner");

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || "Scanner API Error");
        }

        tbody.innerHTML = "";

        data.results.forEach(stock => {

            const color =
                stock.signal === "BULLISH WATCH"
                ? "green"
                : stock.signal === "BEARISH WATCH"
                ? "red"
                : "";

            tbody.innerHTML += `
            <tr>

                <td>${stock.stock}</td>

                <td>${stock.price}</td>

                <td>${stock.rsi}</td>

                <td>${stock.support}</td>

                <td>${stock.resistance}</td>

                <td class="${color}">${stock.signal}</td>

            </tr>
            `;

        });

        status.innerText =
            "Scanner completed. Stocks found: " +
            data.results.length;

    }

    catch (error) {

        status.innerText = error.message;

    }

}


// ---------------- OPTION CHAIN ----------------

async function loadOptions() {

    const underlying =
        document.getElementById("underlying").value;

    const status =
        document.getElementById("optionStatus");

    const resultDiv =
        document.getElementById("optionResult");

    status.innerText = "Loading Option Chain...";

    resultDiv.innerHTML = "";

    try {

        const response = await fetch(
            "/api/options/" + underlying
        );

        const data = await response.json();

        if (!response.ok || data.error) {

            throw new Error(data.error || "API Error");

        }

        status.innerText =
            underlying + " | Expiry: " + data.expiry;

        resultDiv.innerHTML = `

            <h3>Spot: ${data.spot_price}</h3>

            <h3>ATM: ${data.atm_strike}</h3>

            <p>PCR: ${data.pcr}</p>

            <p>Max Pain: ${data.max_pain}</p>

            <p>Support: ${data.support}</p>

            <p>Resistance: ${data.resistance}</p>

            <p>Total CE OI: ${data.total_ce_oi}</p>

            <p>Total PE OI: ${data.total_pe_oi}</p>

            <div style="overflow-x:auto">

            <table>

                <thead>

                    <tr>
                        <th>CE OI</th>
                        <th>CE LTP</th>
                        <th>STRIKE</th>
                        <th>PE LTP</th>
                        <th>PE OI</th>
                    </tr>

                </thead>

                <tbody>

                    ${data.option_chain.map(row => `

                        <tr>

                            <td>${row.CE?.oi ?? "-"}</td>

                            <td>${row.CE?.ltp ?? "-"}</td>

                            <td><b>${row.strike}</b></td>

                            <td>${row.PE?.ltp ?? "-"}</td>

                            <td>${row.PE?.oi ?? "-"}</td>

                        </tr>

                    `).join("")}

                </tbody>

            </table>

            </div>

        `;

    }

    catch (error) {

        status.innerText = "Error: " + error.message;

    }

}


// ---------------- AUTO START ----------------

updateMarket();

scan();

setInterval(updateMarket, 20000);


</script>

</body>
</html>
""")


# ---------------- ZERODHA LOGIN ----------------

@app.route("/login")
def login():

    if not kite:
        return "KITE_API_KEY missing", 500

    return redirect(kite.login_url())


@app.route("/kite/callback")
def callback():

    global ACCESS_TOKEN

    request_token = request.args.get("request_token")

    if not request_token or not API_SECRET:
        return "Login failed. Check credentials."

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


# ---------------- MARKET API ----------------

@app.route("/api/market")
def market():

    if not ACCESS_TOKEN:
        return jsonify({
            "error": "Please login with Zerodha"
        }), 401

    try:

        quotes = kite.ohlc([
            "NSE:NIFTY 50",
            "BSE:SENSEX"
        ])

        result = {}

        for name, label in [
            ("NSE:NIFTY 50", "NIFTY 50"),
            ("BSE:SENSEX", "SENSEX")
        ]:

            if name in quotes:

                item = quotes[name]

                result[label] = {
                    "price": item["last_price"],
                    "close": item["ohlc"]["close"]
                }

        return jsonify(result)

    except Exception:

        return jsonify({
            "error": "Market data unavailable. Check Zerodha login."
        }), 502


# ---------------- SWING SCANNER API ----------------

@app.route("/api/scanner")
def scanner():

    try:

        results = scan_stocks()

        return jsonify({
            "results": results,
            "updated": datetime.now().isoformat()
        })

    except Exception:

        return jsonify({
            "error": "Scanner unavailable"
        }), 502


# ---------------- OPTION CHAIN API ----------------

@app.route("/api/options/<underlying>")
def options_api(underlying):

    if not ACCESS_TOKEN:
        return jsonify({
            "error": "Please login with Zerodha"
        }), 401

    if underlying not in ["NIFTY", "SENSEX"]:
        return jsonify({
            "error": "Invalid underlying"
        }), 400

    try:

        symbols = {
            "NIFTY": "NSE:NIFTY 50",
            "SENSEX": "BSE:SENSEX"
        }

        spot_data = kite.ltp(symbols[underlying])

        spot_price = spot_data[
            symbols[underlying]
        ]["last_price"]

        result = get_option_analysis(
            kite,
            underlying,
            spot_price
        )

        return jsonify(result)

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 502


# ---------------- HEALTH CHECK ----------------
@app.route("/test-telegram")
def test_telegram():

    if not ACCESS_TOKEN:
        return "Please login with Zerodha first", 401

    success = send_telegram(
        "✅ Trading AI Dashboard: Telegram connection successful!"
    )

    if success:
        return "Telegram Test Alert Sent Successfully!"

    return "Telegram Alert Failed. Check Render Environment Variables.", 500
@app.route("/health")
def health():

    return "Trading AI Agent Running"


# ---------------- APP START ----------------

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
