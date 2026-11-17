import os
import requests
from datetime import datetime
from flask import Flask, redirect, request, jsonify, render_template_string
from kiteconnect import KiteConnect
from options_engine import get_option_analysis
from hilega_milega_scanner import scan_all as scan_hilega_all

app = Flask(__name__)
API_KEY = os.environ.get("KITE_API_KEY")
API_SECRET = os.environ.get("KITE_API_SECRET")
kite = KiteConnect(api_key=API_KEY) if API_KEY else None
ACCESS_TOKEN = None
TG_TOKEN = os.environ.get("TG_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")

STOCKS = [
    "RELIANCE","HDFCBANK","ICICIBANK","SBIN","INFY","TCS","LT",
    "AXISBANK","BHARTIARTL","ITC","TATAMOTORS","MARUTI","SUNPHARMA",
    "NTPC","POWERGRID"
]

def send_telegram(message):
    if not TG_TOKEN or not TG_CHAT_ID:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT_ID, "text": message}, timeout=10
        )
        r.raise_for_status()
        return True
    except Exception as exc:
        print("Telegram Error:", exc)
        return False

def get_stock_data(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.NS?range=3mo&interval=1d"
    r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=15)
    r.raise_for_status()
    results = r.json()["chart"]["result"]
    if not results:
        raise ValueError("No Yahoo Finance data")
    q = results[0]["indicators"]["quote"][0]
    return [(c,h,l) for c,h,l in zip(q["close"],q["high"],q["low"])
            if c is not None and h is not None and l is not None]

def ema(values, period):
    if len(values) < period:
        return None
    mult = 2/(period+1)
    value = sum(values[:period])/period
    for x in values[period:]:
        value = (x-value)*mult+value
    return value

def calculate_rsi(values, period=14):
    if len(values) <= period:
        return None
    changes=[values[i]-values[i-1] for i in range(1,len(values))]
    gains=[max(x,0) for x in changes]
    losses=[max(-x,0) for x in changes]
    ag=sum(gains[:period])/period
    al=sum(losses[:period])/period
    for g,l in zip(gains[period:],losses[period:]):
        ag=(ag*(period-1)+g)/period
        al=(al*(period-1)+l)/period
    if al == 0:
        return 100
    rs=ag/al
    return 100-100/(1+rs)

def scan_stocks():
    results=[]
    for stock in STOCKS:
        try:
            rows=get_stock_data(stock)
            if len(rows)<50:
                continue
            closes=[x[0] for x in rows]
            highs=[x[1] for x in rows]
            lows=[x[2] for x in rows]
            e20,e50=ema(closes,20),ema(closes,50)
            strength=calculate_rsi(closes)
            if e20 is None or e50 is None or strength is None:
                continue
            signal="NEUTRAL"
            if e20>e50 and strength>50:
                signal="BULLISH WATCH"
            elif e20<e50 and strength<50:
                signal="BEARISH WATCH"
            results.append({
                "stock":stock,"price":round(closes[-1],2),
                "ema20":round(e20,2),"ema50":round(e50,2),
                "rsi":round(strength,2),"support":round(min(lows[-20:]),2),
                "resistance":round(max(highs[-20:]),2),"signal":signal
            })
        except Exception as exc:
            print("Scanner error:",stock,exc)
    return results

def market_summary_data():
    rows=scan_stocks()
    bull=sum(x["signal"]=="BULLISH WATCH" for x in rows)
    bear=sum(x["signal"]=="BEARISH WATCH" for x in rows)
    return {"total_stocks":len(rows),"bullish":bull,"bearish":bear,
            "neutral":len(rows)-bull-bear}

def logged_in():
    return bool(kite and ACCESS_TOKEN)

@app.route("/")
def home():
    if not kite:
        return "KITE_API_KEY missing. Check Render Environment Variables."
    if not ACCESS_TOKEN:
        return '<h2>Trading AI Dashboard</h2><a href="/login">Login with Zerodha</a>'
    return render_template_string(r"""
<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trading AI Dashboard</title><style>
body{font-family:Arial;background:#101827;color:white;padding:15px}h1{color:#42d392}
.card{background:#243247;padding:18px;margin:12px 0;border-radius:12px}
.price{font-size:27px;font-weight:bold}.green{color:#42d392}.red{color:#ff6565}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{padding:9px 5px;border-bottom:1px solid #45546b;text-align:left}
small{color:#b7c5d8}button{padding:12px;background:#42d392;border:0;border-radius:8px;font-weight:bold;margin:5px 0}
select,input{padding:10px;border-radius:6px;max-width:100%}.summary-number{font-size:25px;font-weight:bold}
</style></head><body>
<h1>🚀 Trading AI Dashboard</h1><p class="green">Zerodha Connected</p>
<div class="card"><h2>NIFTY 50</h2><div id="nifty" class="price">Loading...</div></div>
<div class="card"><h2>SENSEX</h2><div id="sensex" class="price">Loading...</div></div>
<div class="card"><h2>📊 Unified Market Summary</h2>
<p>Total Stocks: <span id="totalStocks" class="summary-number">--</span></p>
<p class="green">Bullish: <span id="bullishCount" class="summary-number">--</span></p>
<p class="red">Bearish: <span id="bearishCount" class="summary-number">--</span></p>
<p>Neutral: <span id="neutralCount" class="summary-number">--</span></p>
<button onclick="loadSummary()">Refresh Market Summary</button><p id="summaryStatus">Waiting...</p></div>
<div class="card"><h2>📈 Market Scanner</h2><p id="marketStatus">Fetching market data...</p></div>
<div class="card"><h2>📊 Index Option Chain</h2>
<select id="underlying"><option>NIFTY</option><option>SENSEX</option></select>
<button onclick="loadOptions()">Analyze Option Chain</button><p id="optionStatus">Waiting...</p><div id="optionResult"></div></div>
<div class="card"><h2>🔍 Swing Stock Scanner</h2><p>EMA 20 | EMA 50 | RSI 14</p>
<button onclick="scanSwing()">Refresh Scanner</button><p id="scanStatus">Waiting...</p>
<div style="overflow-x:auto"><table><thead><tr><th>Stock</th><th>Price</th><th>RSI</th><th>Support</th><th>Resistance</th><th>Signal</th></tr></thead><tbody id="scanner"></tbody></table></div></div>
<div class="card"><h2>🎯 Hilega Milega — NIFTY 50</h2>
<p>Daily RSI(9), weekly/monthly RSI-WMA and EMA/WMA. Close below ₹100 filter removed.</p>
<button onclick="loadHilega()">Run Hilega Milega Scan</button>
<button onclick="sendHilegaAlerts()">Send BUY Alerts to Telegram</button><p id="hilegaStatus">Waiting...</p>
<div style="overflow-x:auto"><table><thead><tr><th>Stock</th><th>Price</th><th>RSI 9</th><th>Entry ref.</th><th>SL ref.</th><th>T1</th><th>T2</th><th>Signal</th></tr></thead><tbody id="hilegaRows"></tbody></table></div>
<p><small>Approximation of the Chartink conditions shared earlier. Confirm signals in Chartink. Entry/SL/targets are indicative levels, not recommendations.</small></p></div>
<div class="card"><h2>📘 Stock Option Chain (NSE F&O)</h2>
<p>Enter a NIFTY 50 stock symbol that has listed F&O contracts.</p>
<input id="stockOptionSymbol" placeholder="e.g. RELIANCE" maxlength="20">
<button onclick="loadStockOptions()">Analyze Stock Options</button><p id="stockOptionStatus">Waiting...</p><div id="stockOptionResult"></div></div>
<div class="card"><h3>⚠️ Risk Management</h3><p>Signals are for research only. No automatic orders are placed.</p>
<p>Market Summary and Swing Scanner use daily candles. Option-chain figures are snapshots.</p></div>
<p><small>Market refresh: 20 seconds</small></p><p id="updated"></p>
<script>
async function updateMarket(){let s=document.getElementById("marketStatus");try{
let r=await fetch("/api/market"),d=await r.json();if(!r.ok)throw Error(d.error||"Market API Error");
for(let n of ["NIFTY 50","SENSEX"]){let x=d[n],id=n==="NIFTY 50"?"nifty":"sensex";if(!x)continue;
let ch=x.price-x.close,p=x.close?ch/x.close*100:0;
document.getElementById(id).innerHTML=x.price.toLocaleString("en-IN")+"<br><small>"+ch.toFixed(2)+" ("+p.toFixed(2)+"%)</small>";}
s.innerText="Market data updated successfully";document.getElementById("updated").innerText="Last updated: "+new Date().toLocaleTimeString();
}catch(e){s.innerText=e.message;}}
async function loadSummary(){let s=document.getElementById("summaryStatus");s.innerText="Analyzing market...";try{
let r=await fetch("/api/market-summary"),d=await r.json();if(!r.ok||d.status!=="success")throw Error(d.error||"Summary API Error");
let x=d.market_summary;for(let [id,k] of [["totalStocks","total_stocks"],["bullishCount","bullish"],["bearishCount","bearish"],["neutralCount","neutral"]])document.getElementById(id).innerText=x[k];
s.innerText="Updated: "+new Date(d.updated).toLocaleString();}catch(e){s.innerText="Error: "+e.message;}}
async function scanSwing(){let s=document.getElementById("scanStatus"),tb=document.getElementById("scanner");s.innerText="Scanning...";try{
let r=await fetch("/api/scanner"),d=await r.json();if(!r.ok)throw Error(d.error||"Scanner API Error");tb.innerHTML="";
d.results.forEach(x=>{let c=x.signal==="BULLISH WATCH"?"green":x.signal==="BEARISH WATCH"?"red":"";
tb.innerHTML+=`<tr><td>${x.stock}</td><td>${x.price}</td><td>${x.rsi}</td><td>${x.support}</td><td>${x.resistance}</td><td class="${c}">${x.signal}</td></tr>`;});
s.innerText="Scanner completed. Stocks found: "+d.results.length;}catch(e){s.innerText=e.message;}}
function chainHtml(d){return `<h3>Spot: ${d.spot_price}</h3><h3>ATM: ${d.atm_strike}</h3><p>Expiry: ${d.expiry}</p>
<p>PCR: ${d.pcr} | Max Pain: ${d.max_pain}</p><p>Support (highest PE OI): ${d.support??"-"} | Resistance (highest CE OI): ${d.resistance??"-"}</p>
<p>Total CE OI: ${d.total_ce_oi} | Total PE OI: ${d.total_pe_oi}</p><div style="overflow-x:auto"><table>
<thead><tr><th>CE OI</th><th>CE LTP</th><th>Strike</th><th>PE LTP</th><th>PE OI</th></tr></thead><tbody>
${d.option_chain.map(x=>`<tr><td>${x.CE?.oi??"-"}</td><td>${x.CE?.ltp??"-"}</td><td><b>${x.strike}</b></td><td>${x.PE?.ltp??"-"}</td><td>${x.PE?.oi??"-"}</td></tr>`).join("")}</tbody></table></div>`;}
async function loadOptions(){let u=document.getElementById("underlying").value,s=document.getElementById("optionStatus"),o=document.getElementById("optionResult");s.innerText="Loading...";o.innerHTML="";
try{let r=await fetch("/api/options/"+u),d=await r.json();if(!r.ok||d.error)throw Error(d.error||"API Error");s.innerText=u+" option chain";o.innerHTML=chainHtml(d);}catch(e){s.innerText="Error: "+e.message;}}
async function loadHilega(){let s=document.getElementById("hilegaStatus"),tb=document.getElementById("hilegaRows");s.innerText="Scanning NIFTY 50...";
try{let r=await fetch("/api/hilega"),d=await r.json();if(!r.ok)throw Error(d.error||"Scan failed");tb.innerHTML="";
d.results.forEach(x=>{tb.innerHTML+=`<tr><td>${x.symbol}</td><td>${x.close}</td><td>${x.rsi}</td><td>${x.entry}</td><td>${x.stop_loss}</td><td>${x.target1}</td><td>${x.target2}</td><td class="green">${x.signal}</td></tr>`;});
s.innerText=`Scan complete: ${d.scanned} checked, ${d.results.length} BUY candidates. ${d.updated}`;}catch(e){s.innerText="Error: "+e.message;}}
async function sendHilegaAlerts(){let s=document.getElementById("hilegaStatus");s.innerText="Sending Telegram alert...";
try{let r=await fetch("/api/hilega/telegram",{method:"POST"}),d=await r.json();if(!r.ok)throw Error(d.error||"Telegram failed");s.innerText=d.message;}catch(e){s.innerText="Error: "+e.message;}}
async function loadStockOptions(){let sym=document.getElementById("stockOptionSymbol").value.trim().toUpperCase(),s=document.getElementById("stockOptionStatus"),o=document.getElementById("stockOptionResult");
if(!sym){s.innerText="Enter a stock symbol.";return;}s.innerText="Loading "+sym+" options...";o.innerHTML="";
try{let r=await fetch("/api/stock-options/"+encodeURIComponent(sym)),d=await r.json();if(!r.ok||d.error)throw Error(d.error||"API Error");s.innerText=sym+" option chain";o.innerHTML=chainHtml(d);}catch(e){s.innerText="Error: "+e.message;}}
updateMarket();scanSwing();loadSummary();setInterval(updateMarket,20000);
</script></body></html>""")

@app.route("/login")
def login():
    if not kite:
        return "KITE_API_KEY missing", 500
    return redirect(kite.login_url())

@app.route("/kite/callback")
def callback():
    global ACCESS_TOKEN
    token=request.args.get("request_token")
    if not token or not API_SECRET or not kite:
        return "Login failed. Check credentials."
    try:
        data=kite.generate_session(token,api_secret=API_SECRET)
        ACCESS_TOKEN=data["access_token"]
        kite.set_access_token(ACCESS_TOKEN)
        return redirect("/")
    except Exception as exc:
        print("Authentication error:",exc)
        return "Authentication failed. Please login again."

@app.route("/api/market")
def market():
    if not logged_in():
        return jsonify({"error":"Please login with Zerodha"}),401
    try:
        quotes=kite.ohlc(["NSE:NIFTY 50","BSE:SENSEX"])
        result={}
        for key,label in [("NSE:NIFTY 50","NIFTY 50"),("BSE:SENSEX","SENSEX")]:
            if key in quotes:
                item=quotes[key]
                result[label]={"price":item["last_price"],"close":item["ohlc"]["close"]}
        return jsonify(result)
    except Exception as exc:
        print("Market API error:",exc)
        return jsonify({"error":"Market data unavailable. Check Zerodha login."}),502

@app.route("/api/market-summary")
def market_summary():
    try:
        return jsonify({"status":"success","market_summary":market_summary_data(),
                        "updated":datetime.now().isoformat()})
    except Exception as exc:
        return jsonify({"status":"error","error":str(exc)}),500

@app.route("/api/scanner")
def scanner():
    try:
        return jsonify({"results":scan_stocks(),"updated":datetime.now().isoformat()})
    except Exception as exc:
        return jsonify({"error":str(exc)}),502

@app.route("/api/hilega")
def hilega_api():
    try:
        rows=scan_hilega_all()
        return jsonify({"results":rows,"scanned":50,"updated":datetime.now().isoformat(),
                        "note":"Approximation of the Chartink conditions supplied; verify against Chartink."})
    except Exception as exc:
        return jsonify({"error":str(exc)}),502

@app.route("/api/hilega/telegram",methods=["POST"])
def hilega_telegram():
    try:
        rows=scan_hilega_all()
        if not rows:
            return jsonify({"message":"Scan complete: no BUY candidates; no Telegram alert sent."})
        lines=["HILEGA MILEGA — NIFTY 50","Research alerts only:"]
        for x in rows[:20]:
            lines.append(f'{x["symbol"]} BUY WATCH | Close {x["close"]} | RSI9 {x["rsi"]} | SL {x["stop_loss"]} | T1 {x["target1"]} | T2 {x["target2"]}')
        if not send_telegram("\\n".join(lines)):
            return jsonify({"error":"Telegram send failed. Check TG_TOKEN and TG_CHAT_ID."}),500
        return jsonify({"message":f"Telegram alert sent for {min(len(rows),20)} candidate(s)."})
    except Exception as exc:
        return jsonify({"error":str(exc)}),502

@app.route("/api/options/<underlying>")
def options_api(underlying):
    if not logged_in():
        return jsonify({"error":"Please login with Zerodha"}),401
    underlying=underlying.upper()
    if underlying not in ("NIFTY","SENSEX"):
        return jsonify({"error":"Invalid index underlying"}),400
    try:
        key="NSE:NIFTY 50" if underlying=="NIFTY" else "BSE:SENSEX"
        spot=kite.ltp(key)[key]["last_price"]
        return jsonify(get_option_analysis(kite,underlying,spot))
    except Exception as exc:
        return jsonify({"error":str(exc)}),502

@app.route("/api/stock-options/<symbol>")
def stock_options_api(symbol):
    if not logged_in():
        return jsonify({"error":"Please login with Zerodha"}),401
    symbol=symbol.upper().strip()
    if not symbol.isalnum():
        return jsonify({"error":"Invalid stock symbol"}),400
    try:
        key=f"NSE:{symbol}"
        spot=kite.ltp(key)[key]["last_price"]
        result=get_option_analysis(kite,symbol,spot,exchange="NFO")
        if result.get("error"):
            return jsonify(result),404
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error":str(exc)}),502

@app.route("/test-telegram")
def test_telegram():
    if not ACCESS_TOKEN:
        return "Please login with Zerodha first",401
    if send_telegram("Trading AI Dashboard: Telegram connection successful!"):
        return "Telegram Test Alert Sent Successfully!"
    return "Telegram Alert Failed. Check Render Environment Variables.",500

@app.route("/health")
def health():
    return "Trading AI Agent Running"

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",10000)))
