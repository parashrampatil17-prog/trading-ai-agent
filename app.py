import os
import gc
import requests
from datetime import datetime
from flask import Flask, redirect, request, jsonify, render_template_string
from kiteconnect import KiteConnect
from options_engine import get_option_analysis
from hilega_milega_scanner import NIFTY50, scan as scan_hilega_stock
from signal_engine import build_signal

app = Flask(__name__)

API_KEY = os.environ.get("KITE_API_KEY")
API_SECRET = os.environ.get("KITE_API_SECRET")
kite = KiteConnect(api_key=API_KEY) if API_KEY else None
ACCESS_TOKEN = None
TG_TOKEN = os.environ.get("TG_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")

STOCKS = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "INFY", "TCS", "LT",
    "AXISBANK", "BHARTIARTL", "ITC", "TATAMOTORS", "MARUTI", "SUNPHARMA",
    "NTPC", "POWERGRID"
]


def send_telegram(message):
    if not TG_TOKEN or not TG_CHAT_ID:
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT_ID, "text": message},
            timeout=10
        )
        response.raise_for_status()
        return True
    except Exception as exc:
        print("Telegram Error:", repr(exc))
        return False


def get_stock_data(symbol):
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{symbol}.NS?range=3mo&interval=1d"
    )
    response = requests.get(
        url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15
    )
    response.raise_for_status()
    results = response.json()["chart"]["result"]
    if not results:
        raise ValueError("No Yahoo Finance data")
    quote = results[0]["indicators"]["quote"][0]
    return [
        (close, high, low)
        for close, high, low in zip(
            quote["close"], quote["high"], quote["low"]
        )
        if close is not None and high is not None and low is not None
    ]


def ema(values, period):
    if len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    value = sum(values[:period]) / period
    for price in values[period:]:
        value = (price - value) * multiplier + value
    return value


def calculate_rsi(values, period=14):
    if len(values) <= period:
        return None
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(change, 0) for change in changes]
    losses = [max(-change, 0) for change in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100
    relative_strength = avg_gain / avg_loss
    return 100 - 100 / (1 + relative_strength)


def scan_stocks():
    results = []
    for stock in STOCKS:
        try:
            rows = get_stock_data(stock)
            if len(rows) < 50:
                continue
            closes = [row[0] for row in rows]
            highs = [row[1] for row in rows]
            lows = [row[2] for row in rows]
            ema20 = ema(closes, 20)
            ema50 = ema(closes, 50)
            strength = calculate_rsi(closes)
            if ema20 is None or ema50 is None or strength is None:
                continue

            signal = "NEUTRAL"
            if ema20 > ema50 and strength > 50:
                signal = "BULLISH WATCH"
            elif ema20 < ema50 and strength < 50:
                signal = "BEARISH WATCH"

            results.append({
                "stock": stock,
                "price": round(closes[-1], 2),
                "ema20": round(ema20, 2),
                "ema50": round(ema50, 2),
                "rsi": round(strength, 2),
                "support": round(min(lows[-20:]), 2),
                "resistance": round(max(highs[-20:]), 2),
                "signal": signal
            })
        except Exception as exc:
            print("Scanner error:", stock, repr(exc))
    return results


def market_summary_data():
    rows = scan_stocks()
    bullish = sum(row["signal"] == "BULLISH WATCH" for row in rows)
    bearish = sum(row["signal"] == "BEARISH WATCH" for row in rows)
    return {
        "total_stocks": len(rows),
        "bullish": bullish,
        "bearish": bearish,
        "neutral": len(rows) - bullish - bearish
    }


def logged_in():
    return bool(kite and ACCESS_TOKEN)


@app.route("/")
def home():
    if not kite:
        return "KITE_API_KEY missing. Check Render Environment Variables."
    if not ACCESS_TOKEN:
        return (
            '<h2>Trading AI Dashboard</h2>'
            '<a href="/login">Login with Zerodha</a>'
        )

    return render_template_string(r"""
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trading AI Dashboard</title>
<style>
body{font-family:Arial;background:#101827;color:white;padding:15px}
h1{color:#42d392}.card{background:#243247;padding:18px;margin:12px 0;border-radius:12px}
.price{font-size:27px;font-weight:bold}.green{color:#42d392}.red{color:#ff6565}
table{width:100%;border-collapse:collapse;font-size:13px}
td,th{padding:9px 5px;border-bottom:1px solid #45546b;text-align:left}
small{color:#b7c5d8}button{padding:12px;background:#42d392;border:0;border-radius:8px;font-weight:bold;margin:5px 0}
select,input{padding:10px;border-radius:6px;max-width:100%}
.summary-number{font-size:25px;font-weight:bold}
</style>
</head>
<body>
<h1>🚀 Trading AI Dashboard</h1>
<p class="green">Zerodha Connected</p>

<div class="card"><h2>NIFTY 50</h2><div id="nifty" class="price">Loading...</div></div>
<div class="card"><h2>SENSEX</h2><div id="sensex" class="price">Loading...</div></div>

<div class="card">
<h2>📊 Unified Market Summary</h2>
<p>Total Stocks: <span id="totalStocks" class="summary-number">--</span></p>
<p class="green">Bullish: <span id="bullishCount" class="summary-number">--</span></p>
<p class="red">Bearish: <span id="bearishCount" class="summary-number">--</span></p>
<p>Neutral: <span id="neutralCount" class="summary-number">--</span></p>
<button onclick="loadSummary()">Refresh Market Summary</button>
<p id="summaryStatus">Waiting...</p>
</div>

<div class="card"><h2>📈 Market Scanner</h2><p id="marketStatus">Fetching market data...</p></div>

<div class="card">
<h2>🎯 CE / PE Signal Engine — Paper Mode</h2>
<p>Technical trend + RSI + EMA + Option Chain PCR + Hilega Milega confirmation.</p>
<select id="signalUnderlying">
<option value="NIFTY">NIFTY</option>
<option value="SENSEX">SENSEX</option>
<option value="RELIANCE">RELIANCE</option>
<option value="HDFCBANK">HDFCBANK</option>
<option value="ICICIBANK">ICICIBANK</option>
<option value="SBIN">SBIN</option>
<option value="INFY">INFY</option>
<option value="TCS">TCS</option>
<option value="LT">LT</option>
<option value="AXISBANK">AXISBANK</option>
<option value="BHARTIARTL">BHARTIARTL</option>
<option value="ITC">ITC</option>
<option value="TATAMOTORS">TATAMOTORS</option>
<option value="MARUTI">MARUTI</option>
<option value="SUNPHARMA">SUNPHARMA</option>
<option value="NTPC">NTPC</option>
<option value="POWERGRID">POWERGRID</option>
</select>
<button onclick="runAISignal()">Generate CE / PE Signal</button>
<p id="aiSignalStatus">Waiting...</p>
<div id="aiSignalResult"></div>
<p><small>Research / paper mode only. WATCH is not a buy recommendation. No orders are placed.</small></p>
</div>

<div class="card">
<h2>📊 Index Option Chain</h2>
<select id="underlying"><option>NIFTY</option><option>SENSEX</option></select>
<button onclick="loadOptions()">Analyze Option Chain</button>
<p id="optionStatus">Waiting...</p><div id="optionResult"></div>
</div>

<div class="card">
<h2>🔍 Swing Stock Scanner</h2><p>EMA 20 | EMA 50 | RSI 14</p>
<button onclick="scanSwing()">Refresh Scanner</button><p id="scanStatus">Waiting...</p>
<div style="overflow-x:auto"><table>
<thead><tr><th>Stock</th><th>Price</th><th>RSI</th><th>Support</th><th>Resistance</th><th>Signal</th></tr></thead>
<tbody id="scanner"></tbody></table></div>
</div>

<div class="card">
<h2>🎯 Hilega Milega — NIFTY 50</h2>
<p>Daily RSI(9), weekly/monthly RSI-WMA and EMA/WMA. Close below ₹100 filter removed.</p>
<button onclick="loadHilega()">Run Hilega Milega Scan</button>
<button onclick="sendHilegaAlerts()">Send BUY Alerts to Telegram</button>
<p id="hilegaStatus">Waiting...</p>
<div style="overflow-x:auto"><table>
<thead><tr><th>Stock</th><th>Price</th><th>RSI 9</th><th>Entry ref.</th><th>SL ref.</th><th>T1</th><th>T2</th><th>Signal</th></tr></thead>
<tbody id="hilegaRows"></tbody></table></div>
<p><small>Approximation of the Chartink conditions shared earlier. Confirm signals in Chartink. Entry/SL/targets are indicative levels, not recommendations.</small></p>
</div>

<div class="card">
<h2>📘 Stock Option Chain (NSE F&O)</h2>
<p>Enter a NIFTY 50 stock symbol that has listed F&O contracts.</p>
<input id="stockOptionSymbol" placeholder="e.g. RELIANCE" maxlength="20">
<button onclick="loadStockOptions()">Analyze Stock Options</button>
<p id="stockOptionStatus">Waiting...</p><div id="stockOptionResult"></div>
</div>

<div class="card">
<h3>⚠️ Risk Management</h3>
<p>Signals are for research only. No automatic orders are placed.</p>
<p>Market Summary and Swing Scanner use daily candles. Option-chain figures are snapshots.</p>
</div>
<p><small>Market refresh: 20 seconds</small></p><p id="updated"></p>

<script>
async function updateMarket(){
    let status=document.getElementById("marketStatus");
    try{
        let response=await fetch("/api/market");
        let data=await response.json();
        if(!response.ok) throw Error(data.error||"Market API Error");
        for(let name of ["NIFTY 50","SENSEX"]){
            let item=data[name], id=name==="NIFTY 50"?"nifty":"sensex";
            if(!item) continue;
            let change=item.price-item.close;
            let percent=item.close?change/item.close*100:0;
            document.getElementById(id).innerHTML=
                item.price.toLocaleString("en-IN")+"<br><small>"+
                change.toFixed(2)+" ("+percent.toFixed(2)+"%)</small>";
        }
        status.innerText="Market data updated successfully";
        document.getElementById("updated").innerText=
            "Last updated: "+new Date().toLocaleTimeString();
    }catch(error){status.innerText=error.message;}
}

async function loadSummary(){
    let status=document.getElementById("summaryStatus");
    status.innerText="Analyzing market...";
    try{
        let response=await fetch("/api/market-summary");
        let data=await response.json();
        if(!response.ok||data.status!=="success")
            throw Error(data.error||"Summary API Error");
        let summary=data.market_summary;
        for(let [id,key] of [
            ["totalStocks","total_stocks"],["bullishCount","bullish"],
            ["bearishCount","bearish"],["neutralCount","neutral"]
        ]) document.getElementById(id).innerText=summary[key];
        status.innerText="Updated: "+new Date(data.updated).toLocaleString();
    }catch(error){status.innerText="Error: "+error.message;}
}

async function scanSwing(){
    let status=document.getElementById("scanStatus");
    let table=document.getElementById("scanner");
    status.innerText="Scanning...";
    try{
        let response=await fetch("/api/scanner");
        let data=await response.json();
        if(!response.ok) throw Error(data.error||"Scanner API Error");
        table.innerHTML="";
        data.results.forEach(item=>{
            let color=item.signal==="BULLISH WATCH"?"green":
                item.signal==="BEARISH WATCH"?"red":"";
            table.innerHTML+=`<tr><td>${item.stock}</td><td>${item.price}</td>
            <td>${item.rsi}</td><td>${item.support}</td><td>${item.resistance}</td>
            <td class="${color}">${item.signal}</td></tr>`;
        });
        status.innerText="Scanner completed. Stocks found: "+data.results.length;
    }catch(error){status.innerText=error.message;}
}

async function runAISignal(){
    let underlying=document.getElementById("signalUnderlying").value;
    let status=document.getElementById("aiSignalStatus");
    let output=document.getElementById("aiSignalResult");
    status.innerText="Analyzing "+underlying+"...";
    output.innerHTML="";
    try{
        let response=await fetch("/api/ai-signal/"+encodeURIComponent(underlying));
        let data=await response.json();
        if(!response.ok||data.error) throw Error(data.error||"Signal API failed");
        status.innerText="Analysis complete" + (data.telegram_alert_sent ? " | Telegram alert sent" : " | Telegram alert not sent — check Render Telegram settings");
        output.innerHTML=`
        <h2>${data.signal||"WAIT"}</h2>
        <p>Spot: ${data.spot??"-"} | Expiry: ${data.expiry??"-"} |
        ATM: ${data.atm_strike??"-"}</p>
        <p>ATM CE: ${data.atm_ce_symbol??"-"} | CE LTP: ${data.atm_ce_ltp??"-"}</p>
        <p>ATM PE: ${data.atm_pe_symbol??"-"} | PE LTP: ${data.atm_pe_ltp??"-"}</p>
        <p>Signal option: ${data.option_symbol??"-"} | LTP snapshot: ${data.option_ltp??"-"}</p>
        <p>RSI 14: ${data.rsi14??"-"} | EMA 9: ${data.ema9??"-"} |
        EMA 21: ${data.ema21??"-"}</p>
        <p>PCR: ${data.pcr??"-"} | Technical: ${data.technical_bias??"-"} |
        OI: ${data.oi_bias??"-"}</p>
        <p>Support: ${data.support??"-"} | Resistance: ${data.resistance??"-"} |
        Hilega: ${data.hilega_confirmation??"-"}</p>
        <p>Bull score: ${data.bull_score??"-"} |
        Bear score: ${data.bear_score??"-"}</p>
        <hr>
        <h3>Paper Trade Plan (illustrative only)</h3>
        <p>Confidence score: ${data.confidence_score??"-"}/100</p>
        <p>Entry zone: ${data.entry_zone_low??"-"} to ${data.entry_zone_high??"-"}</p>
        <p>Stop Loss: ${data.stop_loss??"-"}</p>
        <p>Target 1: ${data.target1??"-"} | R:R: ${data.risk_reward_t1??"-"}</p>
        <p>Target 2: ${data.target2??"-"} | R:R: ${data.risk_reward_t2??"-"}</p>
        <p>${data.plan_note||"Entry, SL and targets are illustrative estimates, not validated trading levels."}</p>
        <p>${data.note||data.reason||""}</p>`;
    }catch(error){status.innerText="Error: "+error.message;}
}

function chainHtml(data){
    return `<h3>Spot: ${data.spot_price}</h3>
    <h3>ATM: ${data.atm_strike}</h3><p>Expiry: ${data.expiry}</p>
    <p>PCR: ${data.pcr} | Max Pain: ${data.max_pain}</p>
    <p>Support (highest PE OI): ${data.support??"-"} |
    Resistance (highest CE OI): ${data.resistance??"-"}</p>
    <p>Total CE OI: ${data.total_ce_oi} | Total PE OI: ${data.total_pe_oi}</p>
    <div style="overflow-x:auto"><table><thead><tr>
    <th>CE OI</th><th>CE LTP</th><th>Strike</th><th>PE LTP</th><th>PE OI</th>
    </tr></thead><tbody>
    ${data.option_chain.map(row=>`<tr>
    <td>${row.CE?.oi??"-"}</td><td>${row.CE?.ltp??"-"}</td>
    <td><b>${row.strike}</b></td><td>${row.PE?.ltp??"-"}</td>
    <td>${row.PE?.oi??"-"}</td></tr>`).join("")}
    </tbody></table></div>`;
}

async function loadOptions(){
    let underlying=document.getElementById("underlying").value;
    let status=document.getElementById("optionStatus");
    let output=document.getElementById("optionResult");
    status.innerText="Loading..."; output.innerHTML="";
    try{
        let response=await fetch("/api/options/"+underlying);
        let data=await response.json();
        if(!response.ok||data.error) throw Error(data.error||"API Error");
        status.innerText=underlying+" option chain";
        output.innerHTML=chainHtml(data);
    }catch(error){status.innerText="Error: "+error.message;}
}

let lastHilegaRows = [];

async function loadHilega(){
    let status=document.getElementById("hilegaStatus");
    let table=document.getElementById("hilegaRows");
    status.innerText="Scanning NIFTY 50 in 5 batches...";
    table.innerHTML="";
    let allRows=[], scanned=0;
    try{
        for(let batch=0;batch<5;batch++){
            status.innerText=`Scanning batch ${batch+1}/5 (10 stocks)...`;
            let response=await fetch(`/api/hilega?batch=${batch}`);
            let data=await response.json();
            if(!response.ok) throw Error(data.error||`Batch ${batch+1} failed`);
            allRows=allRows.concat(data.results||[]);
            scanned+=data.scanned||0;
        }
        lastHilegaRows = allRows;
        allRows.forEach(item=>{
            table.innerHTML+=`<tr><td>${item.symbol}</td><td>${item.close}</td>
            <td>${item.rsi}</td><td>${item.entry}</td><td>${item.stop_loss}</td>
            <td>${item.target1}</td><td>${item.target2}</td>
            <td class="green">${item.signal}</td></tr>`;
        });
        status.innerText=`Scan complete: ${scanned} checked, ${allRows.length} BUY candidates.`;
    }catch(error){status.innerText="Error: "+error.message;}
}

async function sendHilegaAlerts(){
    let status=document.getElementById("hilegaStatus");
    status.innerText="Sending Telegram alert...";
    try{
        if(!lastHilegaRows.length){
            throw Error("Please run Hilega Milega Scan first.");
        }
        let response=await fetch("/api/hilega/telegram",{
            method:"POST",
            headers:{"Content-Type":"application/json"},
            body:JSON.stringify({results:lastHilegaRows})
        });
        let raw=await response.text();
        let data;
        try{data=JSON.parse(raw);}catch(_){
            throw Error("Server returned a non-JSON page. Please retry after dashboard reload.");
        }
        if(!response.ok) throw Error(data.error||"Telegram failed");
        status.innerText=data.message;
    }catch(error){status.innerText="Error: "+error.message;}
}

async function loadStockOptions(){
    let symbol=document.getElementById("stockOptionSymbol").value.trim().toUpperCase();
    let status=document.getElementById("stockOptionStatus");
    let output=document.getElementById("stockOptionResult");
    if(!symbol){status.innerText="Enter a stock symbol.";return;}
    status.innerText="Loading "+symbol+" options..."; output.innerHTML="";
    try{
        let response=await fetch("/api/stock-options/"+encodeURIComponent(symbol));
        let data=await response.json();
        if(!response.ok||data.error) throw Error(data.error||"API Error");
        status.innerText=symbol+" option chain";
        output.innerHTML=chainHtml(data);
    }catch(error){status.innerText="Error: "+error.message;}
}

updateMarket();
scanSwing();
loadSummary();
setInterval(updateMarket,20000);
</script>
</body>
</html>
""")


@app.route("/login")
def login():
    if not kite:
        return "KITE_API_KEY missing", 500
    return redirect(kite.login_url())


@app.route("/kite/callback")
def callback():
    global ACCESS_TOKEN
    token = request.args.get("request_token")
    if not token or not API_SECRET or not kite:
        return "Login failed. Check credentials."
    try:
        data = kite.generate_session(token, api_secret=API_SECRET)
        ACCESS_TOKEN = data["access_token"]
        kite.set_access_token(ACCESS_TOKEN)
        return redirect("/")
    except Exception as exc:
        print("Authentication error:", repr(exc))
        return "Authentication failed. Please login again."


@app.route("/api/market")
def market():
    if not logged_in():
        return jsonify({"error": "Please login with Zerodha"}), 401
    try:
        quotes = kite.ohlc(["NSE:NIFTY 50", "BSE:SENSEX"])
        result = {}
        for key, label in [
            ("NSE:NIFTY 50", "NIFTY 50"), ("BSE:SENSEX", "SENSEX")
        ]:
            if key in quotes:
                item = quotes[key]
                result[label] = {
                    "price": item["last_price"],
                    "close": item["ohlc"]["close"]
                }
        return jsonify(result)
    except Exception as exc:
        print("Market API error:", repr(exc))
        return jsonify({
            "error": "Market data unavailable. Check Zerodha login."
        }), 502


@app.route("/api/market-summary")
def market_summary():
    try:
        return jsonify({
            "status": "success",
            "market_summary": market_summary_data(),
            "updated": datetime.now().isoformat()
        })
    except Exception as exc:
        return jsonify({"status": "error", "error": str(exc)}), 500


@app.route("/api/scanner")
def scanner():
    try:
        return jsonify({
            "results": scan_stocks(),
            "updated": datetime.now().isoformat()
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


def run_hilega_batches():
    all_rows = []
    for start in range(0, len(NIFTY50), 10):
        for symbol in NIFTY50[start:start + 10]:
            try:
                result = scan_hilega_stock(symbol)
                if result:
                    all_rows.append(result)
            except Exception as exc:
                print(f"Hilega scanner skipped {symbol}: {repr(exc)}")
            finally:
                gc.collect()
        gc.collect()
    return all_rows


@app.route("/api/hilega")
def hilega_api():
    try:
        batch = int(request.args.get("batch", "0"))
        total_batches = (len(NIFTY50) + 9) // 10
        if batch < 0 or batch >= total_batches:
            return jsonify({
                "error": f"batch must be between 0 and {total_batches - 1}"
            }), 400

        symbols = NIFTY50[batch * 10:(batch + 1) * 10]
        rows = []
        for symbol in symbols:
            try:
                result = scan_hilega_stock(symbol)
                if result:
                    rows.append(result)
            except Exception as exc:
                print(f"Hilega scanner skipped {symbol}: {repr(exc)}")
            finally:
                gc.collect()

        return jsonify({
            "results": rows,
            "scanned": len(symbols),
            "batch": batch + 1,
            "total_batches": total_batches,
            "updated": datetime.now().isoformat(),
            "note": "Approximation of Chartink conditions; verify signals in Chartink."
        })
    except Exception as exc:
        print("Hilega API error:", repr(exc))
        return jsonify({"error": str(exc)}), 502


@app.route("/api/hilega/telegram", methods=["POST"])
def hilega_telegram():
    try:
        payload = request.get_json(silent=True) or {}
        rows = payload.get("results", [])
        if not isinstance(rows, list):
            return jsonify({"error": "Invalid scan results. Run the scanner again."}), 400

        # Use the results already displayed on the dashboard; do not rescan 49 stocks.
        safe_rows = []
        required = ("symbol", "close", "rsi", "stop_loss", "target1", "target2")
        for item in rows[:20]:
            if isinstance(item, dict) and all(key in item for key in required):
                safe_rows.append(item)

        if not safe_rows:
            return jsonify({
                "message": "No BUY candidates in the latest scan; no Telegram alert sent."
            })

        lines = ["HILEGA MILEGA — NIFTY 50", "Research alerts only:"]
        for item in safe_rows:
            lines.append(
                f'{item["symbol"]} BUY WATCH | Close {item["close"]} | '
                f'RSI9 {item["rsi"]} | SL {item["stop_loss"]} | '
                f'T1 {item["target1"]} | T2 {item["target2"]}'
            )

        if not send_telegram("\n".join(lines)):
            return jsonify({
                "error": "Telegram send failed. Check TG_TOKEN and TG_CHAT_ID in Render Environment Variables."
            }), 500

        return jsonify({
            "message": f"Telegram alert sent for {len(safe_rows)} candidate(s)."
        })
    except Exception as exc:
        print("Hilega Telegram error:", repr(exc))
        return jsonify({"error": str(exc)}), 502


@app.route("/api/ai-signal/<underlying>")
def ai_signal_api(underlying):
    if not logged_in():
        return jsonify({"error": "Please login with Zerodha"}), 401

    underlying = underlying.upper().strip()
    try:
        if underlying == "NIFTY":
            quote_key = "NSE:NIFTY 50"
            exchange = None
        elif underlying == "SENSEX":
            quote_key = "BSE:SENSEX"
            exchange = None
        else:
            if underlying not in NIFTY50:
                return jsonify({
                    "error": "Only NIFTY 50 stocks are supported"
                }), 400
            quote_key = f"NSE:{underlying}"
            exchange = "NFO"

        spot = kite.ltp(quote_key)[quote_key]["last_price"]
        if exchange:
            chain = get_option_analysis(
                kite, underlying, spot, exchange=exchange
            )
        else:
            chain = get_option_analysis(kite, underlying, spot)

        if chain.get("error"):
            return jsonify(chain), 404

        hilega_result = None
        if underlying not in ("NIFTY", "SENSEX"):
            try:
                hilega_result = scan_hilega_stock(underlying)
            except Exception as exc:
                print("Hilega confirmation error:", repr(exc))

        result = build_signal(
            kite, underlying, spot, chain, hilega_result
        )

        # Send a Telegram snapshot only when the user manually generates analysis.
        # This is an informational alert; it never places an order.
        def display(value):
            return "-" if value is None else str(value)

        message = (
            f"TRADING AI — {underlying} PAPER MODE\n"
            f"Signal: {display(result.get('signal'))}\n"
            f"Spot: {display(result.get('spot'))} | ATM: {display(result.get('atm_strike'))}\n"
            f"Expiry: {display(result.get('expiry'))}\n"
            f"ATM CE: {display(result.get('atm_ce_symbol'))} | LTP: {display(result.get('atm_ce_ltp'))}\n"
            f"ATM PE: {display(result.get('atm_pe_symbol'))} | LTP: {display(result.get('atm_pe_ltp'))}\n"
            f"PCR: {display(result.get('pcr'))} | Technical: {display(result.get('technical_bias'))} | OI: {display(result.get('oi_bias'))}\n"
            f"5m: {display(result.get('bias_5m'))} | 15m: {display(result.get('bias_15m'))}\n"
            "Research alert only. Verify live price, spread and liquidity. No order placed."
        )
        alert_sent = send_telegram(message)
        result["telegram_alert_sent"] = alert_sent
        result["telegram_alert_status"] = (
            "Telegram alert sent" if alert_sent
            else "Telegram alert not sent; check TG_TOKEN and TG_CHAT_ID in Render"
        )
        return jsonify(result)

    except Exception as exc:
        print("AI Signal API error:", repr(exc))
        return jsonify({"error": str(exc)}), 502


@app.route("/api/options/<underlying>")
def options_api(underlying):
    if not logged_in():
        return jsonify({"error": "Please login with Zerodha"}), 401
    underlying = underlying.upper()
    if underlying not in ("NIFTY", "SENSEX"):
        return jsonify({"error": "Invalid index underlying"}), 400
    try:
        key = "NSE:NIFTY 50" if underlying == "NIFTY" else "BSE:SENSEX"
        spot = kite.ltp(key)[key]["last_price"]
        return jsonify(get_option_analysis(kite, underlying, spot))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


@app.route("/api/stock-options/<symbol>")
def stock_options_api(symbol):
    if not logged_in():
        return jsonify({"error": "Please login with Zerodha"}), 401
    symbol = symbol.upper().strip()
    if not symbol.isalnum():
        return jsonify({"error": "Invalid stock symbol"}), 400
    try:
        key = f"NSE:{symbol}"
        spot = kite.ltp(key)[key]["last_price"]
        result = get_option_analysis(kite, symbol, spot, exchange="NFO")
        if result.get("error"):
            return jsonify(result), 404
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


@app.route("/test-telegram")
def test_telegram():
    if not ACCESS_TOKEN:
        return "Please login with Zerodha first", 401
    if send_telegram("Trading AI Dashboard: Telegram connection successful!"):
        return "Telegram Test Alert Sent Successfully!"
    return "Telegram Alert Failed. Check Render Environment Variables.", 500


@app.route("/health")
def health():
    return "Trading AI Agent Running"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
