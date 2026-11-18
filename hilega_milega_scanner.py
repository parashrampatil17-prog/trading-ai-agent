import numpy as np
import pandas as pd
import yfinance as yf

NIFTY50 = [
    "ADANIENT","ADANIPORTS","APOLLOHOSP","ASIANPAINT","AXISBANK",
    "BAJAJ-AUTO","BAJFINANCE","BAJAJFINSV","BEL","BHARTIARTL",
    "CIPLA","COALINDIA","DRREDDY","EICHERMOT","ETERNAL","GRASIM",
    "HCLTECH","HDFCBANK","HDFCLIFE","HEROMOTOCO","HINDALCO",
    "HINDUNILVR","ICICIBANK","INDUSINDBK","INFY","ITC","JIOFIN",
    "JSWSTEEL","KOTAKBANK","LT","M&M","MARUTI","NESTLEIND","NTPC",
    "ONGC","POWERGRID","RELIANCE","SBILIFE","SBIN","SHRIRAMFIN",
    "SUNPHARMA","TATACONSUM","TATASTEEL","TCS","TECHM","TITAN",
    "TRENT","ULTRACEMCO","WIPRO"
]
PERIOD = 9
VOLUME_MIN = 50000

def _col(df, name):
    s = df[name]
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    return s.dropna().astype(float)

def _rsi(close, period=PERIOD):
    delta = close.diff()
    gain, loss = delta.clip(lower=0), -delta.clip(upper=0)
    ag = gain.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    al = loss.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    rs = ag / al.replace(0, np.nan)
    result = 100 - 100/(1+rs)
    result = result.mask((al == 0) & (ag > 0), 100)
    result = result.mask((ag == 0) & (al > 0), 0)
    return result

def _wma(s, period=PERIOD):
    weights = np.arange(1, period+1, dtype=float)
    return s.rolling(period).apply(lambda x: float(np.dot(x, weights)/weights.sum()), raw=True)

def _ema(s, period=PERIOD):
    return s.ewm(span=period, adjust=False, min_periods=period).mean()

def scan(symbol):
    ticker = symbol + ".NS"
    d = yf.download(ticker, period="1y", interval="1d", progress=False, auto_adjust=True)
    w = yf.download(ticker, period="2y", interval="1wk", progress=False, auto_adjust=True)
    m = yf.download(ticker, period="5y", interval="1mo", progress=False, auto_adjust=True)
    if d.empty or w.empty or m.empty:
        return None
    dc, wc, mc = _col(d,"Close"), _col(w,"Close"), _col(m,"Close")
    vol = _col(d,"Volume")
    if min(len(dc),len(wc),len(mc)) < 20 or len(vol) < 2:
        return None
    dr, wr, mr = _rsi(dc), _rsi(wc), _rsi(mc)
    de, dw, we, ww, mw = _ema(dr), _wma(dr), _ema(wr), _wma(wr), _wma(mr)
    series = [dr,de,dw,we,ww,mw]
    if any(x.dropna().empty for x in series):
        return None
    close, prev, volume = float(dc.iloc[-1]), float(dc.iloc[-2]), int(vol.iloc[-1])
    checks = [
        float(dr.iloc[-1]) > 50,
        float(mw.iloc[-1]) < 50,
        float(ww.iloc[-1]) < 50,
        float(we.iloc[-1]) > float(ww.iloc[-1]),
        float(de.iloc[-1]) > float(dw.iloc[-1]),
        close > prev,
        volume > VOLUME_MIN,
    ]
    if not all(checks):
        return None

    high, low = _col(d,"High"), _col(d,"Low")
    prev_close = dc.shift(1)
    tr = pd.concat([(high-low),(high-prev_close).abs(),(low-prev_close).abs()],axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])
    if not np.isfinite(atr) or atr <= 0:
        atr = close * 0.02
    sl = round(close-atr,2)
    risk = max(close-sl, close*0.005)
    return {
        "symbol": symbol, "signal": "BUY WATCH", "close": round(close,2),
        "rsi": round(float(dr.iloc[-1]),2), "volume": volume,
        "entry": round(close,2), "stop_loss": sl,
        "target1": round(close+risk*1.5,2), "target2": round(close+risk*2,2),
        "risk_reward_t1": "1:1.5", "risk_reward_t2": "1:2"
    }

def scan_all():
    found=[]
    for symbol in NIFTY50:
        try:
            result=scan(symbol)
            if result:
                found.append(result)
        except Exception as exc:
            print(f"Scanner skipped {symbol}: {exc}")
    return found
