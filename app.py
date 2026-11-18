import os
import gc
import time
import requests
from datetime import datetime
from flask import Flask, redirect, request, jsonify, render_template_string
from kiteconnect import KiteConnect
from options_engine import get_option_analysis
from hilega_milega_scanner import scan as scan_hilega_stock
from signal_engine import build_signal
from advanced_scanner import scan_market

app = Flask(__name__)

API_KEY = os.environ.get("KITE_API_KEY")
API_SECRET = os.environ.get("KITE_API_SECRET")
kite = KiteConnect(api_key=API_KEY) if API_KEY else None
ACCESS_TOKEN = None
TG_TOKEN = os.environ.get("TG_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")

# NIFTY 50 constituents (17-Nov-2026 list). Review this list after NSE index changes.
STOCKS = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BSE", "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL",
    "BHARTIARTL", "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT",
    "ETERNAL", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE",
    "HINDALCO", "HINDUNILVR", "ICICIBANK", "ITC", "INFY",
    "INDIGO", "JSWSTEEL", "JIOFIN", "KOTAKBANK", "LT",
    "M&M", "MARUTI", "MAXHEALTH", "NTPC", "NESTLEIND",
    "ONGC", "POWERGRID", "RELIANCE", "SBILIFE", "SHRIRAMFIN",
    "SBIN", "SUNPHARMA", "TCS", "TATACONSUM", "TMPV",
    "TATASTEEL", "TECHM", "TITAN", "TRENT", "ULTRACEMCO"
]

PAPER_CAPITAL = 10000
PAPER_RISK_PER_TRADE = 100
PAPER_DAILY_LOSS_LIMIT = 200
SUMMARY_CACHE = {"time": 0, "data": None}


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


def yahoo_symbol(symbol):
    # Yahoo Finance symbol spellings for NSE tickers containing punctuation.
    return {"M&M": "M%26M", "BAJAJ-AUTO": "BAJAJ-AUTO"}.get(symbol, symbol)


def get_stock_data(symbol):
    ticker = yahoo_symbol(symbol)
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{ticker}.NS?range=3mo&interval=1d"
    )
    response = requests.get(
        url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15
    )
    response.raise_for_status()
    payload = response.json()["chart"]
    if payload.get("error") or not payload.get("result"):
        raise ValueError("No Yahoo Finance data")
    quote = payload["result"][0]["indicators"]["quote"][0]
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
    results, errors = [], []
    for stock in STOCKS:
        try:
            rows = get_stock_data(stock)
            if len(rows) < 50:
                errors.append({"symbol": stock, "error": "Insufficient daily candles"})
                continue
            closes = [row[0] for row in rows]
            highs = [row[1] for row in rows]
            lows = [row[2] for row in rows]
            ema20, ema50 = ema(closes, 20), ema(closes, 50)
            strength = calculate_rsi(closes)
            if ema20 is None or ema50 is None or strength is None:
                continue
            signal = "NEUTRAL"
            if ema20 > ema50 and strength > 50:
                signal = "BULLISH WATCH"
            elif ema20 < ema50 and strength < 50:
                signal = "BEARISH WATCH"
            results.append({
                "stock": stock, "price": round(closes[-1], 2),
                "ema20": round(ema20, 2), "ema50": round(ema50, 2),
                "rsi": round(strength, 2),
                "support": round(min(lows[-20:]), 2),
                "resistance": round(max(highs[-20:]), 2), "signal": signal
            })
        except Exception as exc:
            errors.append({"symbol": stock, "error": str(exc)})
            print("Scanner error:", stock, repr(exc))
    return results, errors


def market_summary_data():
    now = time.time()
    if SUMMARY_CACHE["data"] and now - SUMMARY_CACHE["time"] < 300:
        return SUMMARY_CACHE["data"]
    rows, errors = scan_stocks()
    bullish = sum(row["signal"] == "BULLISH WATCH" for row in rows)
    bearish = sum(row["signal"] == "BEARISH WATCH" for row in rows)
    data = {
        "total_stocks": len(rows), "requested_stocks": len(STOCKS),
        "bullish": bullish, "bearish": bearish,
        "neutral": len(rows) - bullish - bearish, "errors": errors
    }
    SUMMARY_CACHE.update({"time": now, "data": data})
    return data


def logged_in():
    return bool(kite and ACCESS_TOKEN)


@app.route("/")
def home():
    if not kite:
        return "KITE_API_KEY missing. Check Render Environment Variables."
    if not ACCESS_TOKEN:
        return '<h2>Trading AI Dashboard</h2><a href="/login">Login with Zerodha</a>'

    stock_options = "".join(
        f'<option value="{symbol}">{symbol}</option>' for symbol in STOCKS
    )
    return render_template_string(r"""
<!DOCTYPE html>
<html><head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trading AI Dashboard</title>
<style>
body{font-family:Arial;background:#101827;color:white;padding:15px}
h1{color:#42d392}.card{background:#243247;padding:18px;margin:12px 0;border-radius:12px}
.price{font-size:27px;font-weight:bold}.green{color:#42d392}.red{color:#ff6565}
table{width:100%;border-collapse:collapse;font-size:13px}
td,th{padding:9px 5px;border-bottom:1px solid #45546b;text-align:left;white-space:nowrap}
small{color:#b7c5d8}button{padding:12px;background:#42d392;border:0;border-radius:8px;font-weight:bold;margin:5px 0}
select,input{padding:10px;border-radius:6px;max-width:100%}
.summary-number{font-size:25px;font-weight:bold}
</style></head><body>
<h1>🚀 Trading AI Dashboard</h1>
<p class="green">Zerodha Connected | Paper Mode</p>
<div class="card"><h2>NIFTY 50</h2><div id="nifty" class="price">Loading...</div></div>
<div class="card"><h2>SENSEX</h2><div id="sensex" class="price">Loading...</div></div>

<div class="card"><h2>📊 Unified NIFTY 50 Summary</h2>
<p>Stocks scanned: <span id="totalStocks" class="summary-number">--</span> / 50</p>
<p class="green">Bullish: <span id="bullishCount" class="summary-number">--</span></p>
<p class="red">Bearish: <span id="bearishCount" class="summary-number">--</span></p>
<p>Neutral: <span id="neutralCount" class="summary-number">--</span></p>
<button onclick="loadSummary()">Refresh Market Summary</button><p id="summaryStatus">Waiting...</p></div>

<div class="card"><h2>📈 Advanced NIFTY 50 Scanner — Paper Mode</h2>
<p>All 50 constituents | EMA20/50 | RSI14 | ADX14 | +DI/-DI | Daily breakout | Volume expansion | 5-minute VWAP.</p>
<p><small>Manual scan uses Zerodha historical data. Fifty symbols may take time and can hit broker API limits; errors are shown separately.</small></p>
<button onclick="runAdvancedScanner()">Run Full NIFTY 50 Scanner</button>
<p id="advancedStatus">Waiting...</p>
<div style="overflow-x:auto"><table><thead><tr>
<th>Stock</th><th>Price</th><th>RSI</th><th>ADX</th><th>+DI</th><th>-DI</th>
<th>Vol x</th><th>VWAP</th><th>Bull</th><th>Bear</th><th>Signal</th>
</tr></thead><tbody id="advancedRows"></tbody></table></div>
<p id="advancedErrors"></p>
<h3 class="green">Top 5 Bullish</h3><div id="topBullish"></div>
<h3 class="red">Top 5 Bearish</h3><div id="topBearish"></div>
<p><small>Scores are rule counts, not win probabilities. Research watch only; no orders are placed.</small></p></div>

<div class="card"><h2>🧠 CE / PE Signal Engine — Paper Mode</h2>
<p>Technical trend + RSI + EMA + Option Chain PCR + Hilega confirmation.</p>
<select id="signalUnderlying"><option value="NIFTY">NIFTY</option><option value="SENSEX">SENSEX</option>
{{ stock_options|safe }}</select>
<button onclick="runAISignal()">Generate CE / PE Signal</button>
<p id="aiSignalStatus">Waiting...</p><div id="aiSignalResult"></div>
<p><small>WATCH is not a buy recommendation. No orders are placed.</small></p></div>

<div class="card"><h2>📊 Index Option Chain</h2>
<select id="underlying"><option>NIFTY</option><option>SENSEX</option></select>
<button onclick="loadOptions()">Analyze Option Chain</button><p id="optionStatus">Waiting...</p><div id="optionResult"></div></div>

<div class="card"><h2>🔍 NIFTY 50 Swing Scanner</h2><p>Daily EMA20 | EMA50 | RSI14</p>
<button onclick="scanSwing()">Refresh Scanner</button><p id="scanStatus">Waiting...</p>
<div style="overflow-x:auto"><table><thead><tr><th>Stock</th><th>Price</th><th>RSI</th><th>Support</th><th>Resistance</th><th>Signal</th></tr></thead>
<tbody id="scanner"></tbody></table></div></div>

<div class="card"><h2>🎯 Hilega Milega — NIFTY 50</h2>
<p>Daily RSI(9), weekly/monthly RSI-WMA and EMA/WMA. Approximation of shared Chartink conditions.</p>
<button onclick="loadHilega()">Run Hilega Milega Scan</button>
<button onclick="sendHilegaAlerts()">Send BUY WATCH Alerts to Telegram</button>
<p id="hilegaStatus">Waiting...</p><div style="overflow-x:auto"><table><thead><tr>
<th>Stock</th><th>Price</th><th>RSI 9</th><th>Entry ref.</th><th>SL ref.</th><th>T1</th><th>T2</th><th>Signal</th>
</tr></thead><tbody id="hilegaRows"></tbody></table></div>
<p><small>Verify signals in Chartink. Entry/SL/targets are indicative, not recommendations.</small></p></div>

<div class="card"><h2>📘 Stock Option Chain (NSE F&O)</h2>
<p>Enter a NIFTY 50 stock symbol with listed F&O contracts.</p>
<input id="stockOptionSymbol" placeholder="e.g. RELIANCE" maxlength="20">
<button onclick="loadStockOptions()">Analyze Stock Options</button>
<p id="stockOptionStatus">Waiting...</p><div id="stockOptionResult"></div></div>


<div class="card"><h2>📒 Paper Trading Journal — Options + Swing</h2>
<p><small>Manual paper entries only. Data is saved in this browser's local storage on this device; it is not synced to a server or other device.</small></p>
<div style="display:grid;gap:8px">
<label>Trade Type
<select id="jType"><option value="OPTIONS">Options</option><option value="SWING">Swing Stock</option></select></label>
<label>Symbol / Underlying <input id="jSymbol" placeholder="e.g. NIFTY, SBIN, RELIANCE"></label>
<label>Position
<select id="jSide"><option value="BUY">BUY (Long)</option><option value="SELL">SELL (Short)</option></select></label>
<label id="jOptionWrap">Option Type
<select id="jOption"><option value="CE">CE</option><option value="PE">PE</option></select></label>
<label id="jExpiryWrap">Expiry Date <input id="jExpiry" type="date"></label>
<label id="jStrikeWrap">Strike Price <input id="jStrike" type="number" step="0.05" min="0" placeholder="e.g. 26000"></label>
<label>Entry Price <input id="jEntry" type="number" step="0.01" min="0"></label>
<label>Stop Loss <input id="jSL" type="number" step="0.01" min="0"></label>
<label>Target 1 <input id="jT1" type="number" step="0.01" min="0"></label>
<label>Target 2 <input id="jT2" type="number" step="0.01" min="0"></label>
<label id="jLotSizeWrap">Lot Size (units per lot) <input id="jLotSize" type="number" step="1" min="1" value="1"></label>
<label><span id="jQtyLabel">Number of Lots</span> <input id="jQty" type="number" step="1" min="1" value="1"></label>
<button onclick="addPaperTrade()">Add Open Paper Trade</button>
</div>
<p id="journalStatus">No journal action yet.</p>
<div style="overflow-x:auto"><table><thead><tr>
<th>Date</th><th>Type</th><th>Symbol</th><th>Option</th><th>Expiry</th><th>Strike</th><th>Side</th><th>Entry</th><th>SL</th><th>T1</th><th>T2</th><th>Lots/Qty</th><th>Lot Size</th><th>Status</th><th>Exit</th><th>P&amp;L</th><th>Action</th>
</tr></thead><tbody id="journalRows"></tbody></table></div>
<p id="journalSummary"></p>
<button onclick="downloadJournalCSV()">Download Journal CSV</button>
<button onclick="clearPaperJournal()">Clear Journal</button>
</div>

<div class="card"><h3>⚠️ Paper Risk Management</h3>
<p>Capital: ₹{{ capital }} | Illustrative risk/trade: ₹{{ risk }} | Daily loss limit: ₹{{ daily_limit }}</p>
<p>These are display settings only. The app does not track realised daily P&amp;L or enforce a live risk lock.</p>
<p>Signals are research-only. No automatic orders are placed.</p></div>
<p><small>Market refresh: 20 seconds</small></p><p id="updated"></p>

<script>
const STOCKS = {{ stocks_json|safe }};
async function updateMarket(){
 try{
  let r=await fetch("/api/market"),d=await r.json();if(!r.ok)throw Error(d.error||"Market API Error");
  for(let name of ["NIFTY 50","SENSEX"]){let x=d[name],id=name==="NIFTY 50"?"nifty":"sensex";if(!x)continue;
   let ch=x.price-x.close,p=x.close?ch/x.close*100:0;
   document.getElementById(id).innerHTML=x.price.toLocaleString("en-IN")+"<br><small>"+ch.toFixed(2)+" ("+p.toFixed(2)+"%)</small>";}
  document.getElementById("updated").innerText="Last updated: "+new Date().toLocaleTimeString();
 }catch(e){console.log(e.message);}
}
async function loadSummary(){
 let s=document.getElementById("summaryStatus");s.innerText="Analyzing NIFTY 50...";
 try{let r=await fetch("/api/market-summary"),d=await r.json();if(!r.ok||d.status!=="success")throw Error(d.error||"Summary failed");
 let x=d.market_summary;document.getElementById("totalStocks").innerText=x.total_stocks;
 document.getElementById("bullishCount").innerText=x.bullish;document.getElementById("bearishCount").innerText=x.bearish;
 document.getElementById("neutralCount").innerText=x.neutral;
 s.innerText=`Updated ${new Date(d.updated).toLocaleString()} | Errors: ${(x.errors||[]).length}`;
 }catch(e){s.innerText="Error: "+e.message;}}
async function scanSwing(){
 let s=document.getElementById("scanStatus"),t=document.getElementById("scanner");s.innerText="Scanning 50 stocks...";
 try{let r=await fetch("/api/scanner"),d=await r.json();if(!r.ok)throw Error(d.error||"Scanner failed");t.innerHTML="";
 (d.results||[]).forEach(x=>{let c=x.signal==="BULLISH WATCH"?"green":x.signal==="BEARISH WATCH"?"red":"";
 t.innerHTML+=`<tr><td>${x.stock}</td><td>${x.price}</td><td>${x.rsi}</td><td>${x.support}</td><td>${x.resistance}</td><td class="${c}">${x.signal}</td></tr>`;});
 s.innerText=`Completed: ${d.results.length}/50 | Errors: ${(d.errors||[]).length}`;
 }catch(e){s.innerText=e.message;}}
function miniTable(rows){if(!rows.length)return "<p>No qualifying rows.</p>";
 return `<div style="overflow-x:auto"><table><tr><th>Stock</th><th>Price</th><th>ADX</th><th>+DI</th><th>-DI</th><th>Bull</th><th>Bear</th><th>Signal</th></tr>`+
 rows.map(x=>`<tr><td>${x.symbol}</td><td>${x.price??"-"}</td><td>${x.adx14??"-"}</td><td>${x.plus_di14??"-"}</td><td>${x.minus_di14??"-"}</td><td>${x.bull_score??"-"}</td><td>${x.bear_score??"-"}</td><td>${x.signal||"-"}</td></tr>`).join("")+"</table></div>";}
async function runAdvancedScanner(){
 let s=document.getElementById("advancedStatus"),t=document.getElementById("advancedRows"),e=document.getElementById("advancedErrors");
 s.innerText="Scanning all 50 NIFTY stocks... please wait.";t.innerHTML="";e.innerText="";
 document.getElementById("topBullish").innerHTML="";document.getElementById("topBearish").innerHTML="";
 try{let r=await fetch("/api/advanced-scan"),d=await r.json();if(!r.ok)throw Error(d.error||"Advanced scan failed");
 let rows=d.results||[];rows.forEach(x=>{let c=(x.signal||"").includes("BULLISH")?"green":(x.signal||"").includes("BEARISH")?"red":"";
 t.innerHTML+=`<tr><td>${x.symbol||"-"}</td><td>${x.intraday_price_5m??x.price??"-"}</td><td>${x.rsi14??"-"}</td>
 <td>${x.adx14??"-"}</td><td>${x.plus_di14??"-"}</td><td>${x.minus_di14??"-"}</td>
 <td>${x.volume_ratio??"-"}</td><td>${x.vwap_5m??"-"}</td><td>${x.bull_score??"-"}</td><td>${x.bear_score??"-"}</td>
 <td class="${c}">${x.signal||x.status||"-"}</td></tr>`;});
 let bulls=rows.filter(x=>(x.signal||"").includes("BULLISH")).sort((a,b)=>(b.bull_score||0)-(a.bull_score||0)).slice(0,5);
 let bears=rows.filter(x=>(x.signal||"").includes("BEARISH")).sort((a,b)=>(b.bear_score||0)-(a.bear_score||0)).slice(0,5);
 document.getElementById("topBullish").innerHTML=miniTable(bulls);document.getElementById("topBearish").innerHTML=miniTable(bears);
 let errs=d.errors||[];s.innerText=`Scan complete: ${rows.length}/50 results; ${errs.length} errors. Updated ${d.updated_at||""}`;
 if(errs.length)e.innerText=errs.map(x=>x.symbol+": "+x.error).join(" | ");
 }catch(e2){s.innerText="Error: "+e2.message;}}
async function runAISignal(){
 let u=document.getElementById("signalUnderlying").value,s=document.getElementById("aiSignalStatus"),o=document.getElementById("aiSignalResult");
 s.innerText="Analyzing "+u+"...";o.innerHTML="";
 try{let r=await fetch("/api/ai-signal/"+encodeURIComponent(u)),d=await r.json();if(!r.ok||d.error)throw Error(d.error||"Signal failed");
 s.innerText="Analysis complete"+(d.telegram_alert_sent?" | Telegram alert sent":" | Telegram alert not sent");
 o.innerHTML=`<h2>${d.signal||"WAIT"}</h2><p>Spot: ${d.spot??"-"} | Expiry: ${d.expiry??"-"} | ATM: ${d.atm_strike??"-"}</p>
 <p>ATM CE: ${d.atm_ce_symbol??"-"} (${d.atm_ce_ltp??"-"}) | ATM PE: ${d.atm_pe_symbol??"-"} (${d.atm_pe_ltp??"-"})</p>
 <p>Signal option: ${d.option_symbol??"-"} | LTP: ${d.option_ltp??"-"}</p>
 <p>RSI: ${d.rsi14??"-"} | EMA9: ${d.ema9??"-"} | EMA21: ${d.ema21??"-"} | PCR: ${d.pcr??"-"}</p>
 <p>Technical: ${d.technical_bias??"-"} | OI: ${d.oi_bias??"-"} | Support: ${d.support??"-"} | Resistance: ${d.resistance??"-"}</p>
 <p>Hilega: ${d.hilega_confirmation??"-"} | Bull: ${d.bull_score??"-"} | Bear: ${d.bear_score??"-"}</p><hr>
 <h3>Paper Plan (illustrative)</h3><p>Confidence score: ${d.confidence_score??"-"}/100</p>
 <p>Entry: ${d.entry_zone_low??"-"} to ${d.entry_zone_high??"-"} | SL: ${d.stop_loss??"-"}</p>
 <p>T1: ${d.target1??"-"} | T2: ${d.target2??"-"}</p><p>${d.plan_note||""}</p><p>${d.note||d.reason||""}</p>`;
 }catch(e){s.innerText="Error: "+e.message;}}
function chainHtml(d){return `<h3>Spot: ${d.spot_price}</h3><h3>ATM: ${d.atm_strike}</h3><p>Expiry: ${d.expiry}</p>
 <p>PCR: ${d.pcr} | Max Pain: ${d.max_pain}</p><p>Support: ${d.support??"-"} | Resistance: ${d.resistance??"-"}</p>
 <p>Total CE OI: ${d.total_ce_oi} | Total PE OI: ${d.total_pe_oi}</p><div style="overflow-x:auto"><table><tr><th>CE OI</th><th>CE LTP</th><th>Strike</th><th>PE LTP</th><th>PE OI</th></tr>
 ${(d.option_chain||[]).map(x=>`<tr><td>${x.CE?.oi??"-"}</td><td>${x.CE?.ltp??"-"}</td><td>${x.strike}</td><td>${x.PE?.ltp??"-"}</td><td>${x.PE?.oi??"-"}</td></tr>`).join("")}</table></div>`;}
async function loadOptions(){let u=document.getElementById("underlying").value,s=document.getElementById("optionStatus"),o=document.getElementById("optionResult");
 s.innerText="Loading...";try{let r=await fetch("/api/options/"+u),d=await r.json();if(!r.ok||d.error)throw Error(d.error||"API error");s.innerText=u+" option chain";o.innerHTML=chainHtml(d);}catch(e){s.innerText="Error: "+e.message;}}
let lastHilegaRows=[];
async function loadHilega(){let s=document.getElementById("hilegaStatus"),t=document.getElementById("hilegaRows");s.innerText="Scanning NIFTY 50 in 5 batches...";t.innerHTML="";
 let all=[],count=0;try{for(let b=0;b<5;b++){s.innerText=`Scanning batch ${b+1}/5...`;let r=await fetch(`/api/hilega?batch=${b}`),d=await r.json();if(!r.ok)throw Error(d.error||"Hilega batch failed");all=all.concat(d.results||[]);count+=d.scanned||0;}
 lastHilegaRows=all;all.forEach(x=>{t.innerHTML+=`<tr><td>${x.symbol}</td><td>${x.close}</td><td>${x.rsi}</td><td>${x.entry}</td><td>${x.stop_loss}</td><td>${x.target1}</td><td>${x.target2}</td><td class="green">${x.signal}</td></tr>`;});
 s.innerText=`Complete: ${count} checked, ${all.length} BUY WATCH candidates.`;}catch(e){s.innerText="Error: "+e.message;}}
async function sendHilegaAlerts(){let s=document.getElementById("hilegaStatus");try{if(!lastHilegaRows.length)throw Error("Run Hilega scan first.");
 let r=await fetch("/api/hilega/telegram",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({results:lastHilegaRows})});
 let d=await r.json();if(!r.ok)throw Error(d.error||"Telegram failed");s.innerText=d.message;}catch(e){s.innerText="Error: "+e.message;}}
async function loadStockOptions(){let sym=document.getElementById("stockOptionSymbol").value.trim().toUpperCase(),s=document.getElementById("stockOptionStatus"),o=document.getElementById("stockOptionResult");
 if(!sym){s.innerText="Enter a symbol.";return;}s.innerText="Loading "+sym+"...";try{let r=await fetch("/api/stock-options/"+encodeURIComponent(sym)),d=await r.json();if(!r.ok||d.error)throw Error(d.error||"API error");s.innerText=sym+" option chain";o.innerHTML=chainHtml(d);}catch(e){s.innerText="Error: "+e.message;}}

const JOURNAL_KEY = "trading_ai_paper_journal_v1";
function getJournal(){try{return JSON.parse(localStorage.getItem(JOURNAL_KEY)||"[]");}catch(e){return [];}}
function saveJournal(rows){localStorage.setItem(JOURNAL_KEY,JSON.stringify(rows));renderJournal();}
function updateJournalFields(){
 const isOptions=document.getElementById("jType").value==="OPTIONS";
 for(const id of ["jOptionWrap","jExpiryWrap","jStrikeWrap","jLotSizeWrap"])
  document.getElementById(id).style.display=isOptions?"block":"none";
 document.getElementById("jQtyLabel").innerText=isOptions?"Number of Lots":"Quantity";
}
function addPaperTrade(){
 const type=document.getElementById("jType").value;
 const symbol=document.getElementById("jSymbol").value.trim().toUpperCase();
 const side=document.getElementById("jSide").value;
 const option=document.getElementById("jOption").value;
 const expiry=document.getElementById("jExpiry").value;
 const strike=Number(document.getElementById("jStrike").value);
 const lotSize=type==="OPTIONS"?Number(document.getElementById("jLotSize").value):1;
 const entry=Number(document.getElementById("jEntry").value);
 const sl=Number(document.getElementById("jSL").value);
 const t1=Number(document.getElementById("jT1").value);
 const t2=Number(document.getElementById("jT2").value);
 const qty=Number(document.getElementById("jQty").value);
 const status=document.getElementById("journalStatus");
 if(!symbol||![entry,sl,t1,t2,qty,lotSize].every(Number.isFinite)||entry<=0||sl<=0||t1<=0||t2<=0||qty<=0||lotSize<=0){
  status.innerText="Enter valid symbol, Entry, SL, Targets, Quantity/Lots and Lot Size.";return;
 }
 if(type==="OPTIONS"&&(!expiry||!Number.isFinite(strike)||strike<=0)){
  status.innerText="For Options, enter Expiry Date and Strike Price.";return;
 }
 const rows=getJournal();
 rows.unshift({id:Date.now(),date:new Date().toLocaleString(),type,symbol,side,
  option:type==="OPTIONS"?option:"-",expiry:type==="OPTIONS"?expiry:"-",
  strike:type==="OPTIONS"?strike:null,lotSize:type==="OPTIONS"?lotSize:1,
  entry,sl,t1,t2,qty,state:"OPEN",exit:null,pnl:null});
 saveJournal(rows);status.innerText="Paper trade added. No broker order was placed.";
 document.getElementById("jSymbol").value="";
}
function closePaperTrade(id){
 const rows=getJournal(),trade=rows.find(x=>x.id===id);
 if(!trade)return;
 const raw=prompt("Enter paper exit price:");
 if(raw===null)return;
 const exit=Number(raw);
 if(!Number.isFinite(exit)||exit<=0){alert("Enter a valid positive exit price.");return;}
 const direction=trade.side==="BUY"?1:-1;
 const multiplier=trade.type==="OPTIONS"?(Number(trade.lotSize)||1):1;
 trade.exit=exit;trade.pnl=Number(((exit-trade.entry)*trade.qty*multiplier*direction).toFixed(2));trade.state="CLOSED";
 saveJournal(rows);
}
function renderJournal(){
 const rows=getJournal(),body=document.getElementById("journalRows");
 if(!body)return;
 body.innerHTML="";
 rows.forEach(x=>{
  const pnl=x.pnl===null?"-":("₹"+Number(x.pnl).toFixed(2));
  body.innerHTML+=`<tr><td>${x.date}</td><td>${x.type}</td><td>${x.symbol}</td><td>${x.option??"-"}</td>
  <td>${x.expiry??"-"}</td><td>${x.strike??"-"}</td><td>${x.side}</td><td>${x.entry}</td><td>${x.sl}</td>
  <td>${x.t1}</td><td>${x.t2}</td><td>${x.qty}</td><td>${x.type==="OPTIONS"?(x.lotSize??1):"-"}</td>
  <td>${x.state}</td><td>${x.exit??"-"}</td><td>${pnl}</td>
  <td>${x.state==="OPEN"?`<button onclick="closePaperTrade(${x.id})">Close</button>`:"-"}</td></tr>`;
 });
 const closed=rows.filter(x=>x.state==="CLOSED"),total=closed.reduce((s,x)=>s+(Number(x.pnl)||0),0);
 const wins=closed.filter(x=>x.pnl>0).length;
 document.getElementById("journalSummary").innerText=
  `Trades: ${rows.length} | Open: ${rows.length-closed.length} | Closed: ${closed.length} | Wins: ${wins} | Closed P&L: ₹${total.toFixed(2)} | Win rate: ${closed.length?(wins/closed.length*100).toFixed(1):"0.0"}%`;
}
function downloadJournalCSV(){
 const rows=getJournal();if(!rows.length){alert("Journal is empty.");return;}
 const cols=["date","type","symbol","option","expiry","strike","side","entry","sl","t1","t2","qty","lotSize","state","exit","pnl"];
 const esc=v=>'"'+String(v??"").replace(/"/g,'""')+'"';
 const csv=[cols.join(","),...rows.map(x=>cols.map(k=>esc(x[k])).join(","))].join("\r\n");
 const blob=new Blob([csv],{type:"text/csv;charset=utf-8;"});
 const url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download="paper_trading_journal.csv";a.click();URL.revokeObjectURL(url);
}
function clearPaperJournal(){
 if(!confirm("Delete all paper journal entries from this browser? Download CSV first if needed."))return;
 localStorage.removeItem(JOURNAL_KEY);renderJournal();document.getElementById("journalStatus").innerText="Journal cleared from this browser.";
}

updateJournalFields();
document.getElementById("jType").addEventListener("change",updateJournalFields);
renderJournal();
updateMarket();setInterval(updateMarket,20000);
loadSummary();
</script></body></html>
""", stock_options=stock_options, stocks_json=__import__("json").dumps(STOCKS),
       capital=PAPER_CAPITAL, risk=PAPER_RISK_PER_TRADE,
       daily_limit=PAPER_DAILY_LOSS_LIMIT)


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
        for key, label in [("NSE:NIFTY 50", "NIFTY 50"), ("BSE:SENSEX", "SENSEX")]:
            if key in quotes:
                item = quotes[key]
                result[label] = {"price": item["last_price"], "close": item["ohlc"]["close"]}
        return jsonify(result)
    except Exception as exc:
        print("Market API error:", repr(exc))
        return jsonify({"error": "Market data unavailable. Check Zerodha login."}), 502


@app.route("/api/market-summary")
def market_summary():
    try:
        return jsonify({"status": "success", "market_summary": market_summary_data(),
                        "updated": datetime.now().isoformat()})
    except Exception as exc:
        return jsonify({"status": "error", "error": str(exc)}), 500


@app.route("/api/scanner")
def scanner():
    try:
        rows, errors = scan_stocks()
        return jsonify({"results": rows, "errors": errors, "updated": datetime.now().isoformat()})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


@app.route("/api/advanced-scan")
def advanced_scan_api():
    if not logged_in():
        return jsonify({"error": "Please login with Zerodha"}), 401
    try:
        # 50 symbols, manually triggered to control Zerodha historical-data API usage.
        result = scan_market(kite, STOCKS)
        return jsonify(result)
    except Exception as exc:
        print("Advanced scanner error:", repr(exc))
        return jsonify({"error": "Advanced scan failed: " + str(exc)}), 502


@app.route("/api/hilega")
def hilega_api():
    try:
        batch = int(request.args.get("batch", "0"))
        total_batches = (len(STOCKS) + 9) // 10
        if batch < 0 or batch >= total_batches:
            return jsonify({"error": f"batch must be between 0 and {total_batches - 1}"}), 400
        symbols = STOCKS[batch * 10:(batch + 1) * 10]
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
            "results": rows, "scanned": len(symbols), "batch": batch + 1,
            "total_batches": total_batches, "updated": datetime.now().isoformat(),
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
        safe_rows = []
        required = ("symbol", "close", "rsi", "stop_loss", "target1", "target2")
        for item in rows[:20]:
            if isinstance(item, dict) and all(key in item for key in required):
                safe_rows.append(item)
        if not safe_rows:
            return jsonify({"message": "No BUY candidates; no Telegram alert sent."})
        lines = ["HILEGA MILEGA — NIFTY 50", "Research alerts only:"]
        for item in safe_rows:
            lines.append(
                f'{item["symbol"]} BUY WATCH | Close {item["close"]} | RSI9 {item["rsi"]} | '
                f'SL {item["stop_loss"]} | T1 {item["target1"]} | T2 {item["target2"]}'
            )
        if not send_telegram("\n".join(lines)):
            return jsonify({"error": "Telegram send failed. Check TG_TOKEN and TG_CHAT_ID."}), 500
        return jsonify({"message": f"Telegram alert sent for {len(safe_rows)} candidate(s)."})
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
            quote_key, exchange = "NSE:NIFTY 50", None
        elif underlying == "SENSEX":
            quote_key, exchange = "BSE:SENSEX", None
        else:
            if underlying not in STOCKS:
                return jsonify({"error": "Only current NIFTY 50 stocks are supported"}), 400
            quote_key, exchange = f"NSE:{underlying}", "NFO"
        spot = kite.ltp(quote_key)[quote_key]["last_price"]
        chain = get_option_analysis(kite, underlying, spot, exchange=exchange) if exchange else get_option_analysis(kite, underlying, spot)
        if chain.get("error"):
            return jsonify(chain), 404
        hilega_result = None
        if underlying not in ("NIFTY", "SENSEX"):
            try:
                hilega_result = scan_hilega_stock(underlying)
            except Exception as exc:
                print("Hilega confirmation error:", repr(exc))
        result = build_signal(kite, underlying, spot, chain, hilega_result)
        # Telegram alerts only for watch/pre-signal states; suppress WAIT/NO TRADE noise.
        signal_text = str(result.get("signal", "")).upper()
        alertable = any(tag in signal_text for tag in ("CE WATCH", "PE WATCH", "EARLY CE", "EARLY PE", "PRE-SIGNAL CE", "PRE-SIGNAL PE"))
        if alertable:
            message = (
                f"TRADING AI — {underlying} PAPER MODE\nSignal: {signal_text}\n"
                f"Spot: {result.get('spot')} | ATM: {result.get('atm_strike')} | Expiry: {result.get('expiry')}\n"
                f"PCR: {result.get('pcr')} | 5m: {result.get('bias_5m')} | 15m: {result.get('bias_15m')}\n"
                "Research alert only. Verify price, spread and liquidity. No order placed."
            )
            alert_sent = send_telegram(message)
        else:
            alert_sent = False
        result["telegram_alert_sent"] = alert_sent
        result["telegram_alert_status"] = "Alert sent" if alert_sent else (
            "No alert: signal is not an alertable watch state, or Telegram is not configured."
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
    if symbol not in STOCKS:
        return jsonify({"error": "Enter a valid current NIFTY 50 symbol"}), 400
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
