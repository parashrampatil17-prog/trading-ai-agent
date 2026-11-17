import yfinance as yf
import pandas as pd
import numpy as np

NIFTY50 = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT",
    "AXISBANK", "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV",
    "BEL", "BHARTIARTL", "CIPLA", "COALINDIA",
    "DRREDDY", "EICHERMOT", "ETERNAL", "GRASIM",
    "HCLTECH", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO",
    "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDUSINDBK",
    "INFY", "ITC", "JIOFIN", "JSWSTEEL",
    "KOTAKBANK", "LT", "M&M", "MARUTI",
    "NESTLEIND", "NTPC", "ONGC", "POWERGRID",
    "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN",
    "SUNPHARMA", "TATACONSUM", "TATASTEEL", "TCS",
    "TECHM", "TITAN", "TRENT", "ULTRACEMCO",
    "WIPRO"
]

def rsi(series, period=9):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def wma(series, period=9):
    weights = np.arange(1, period + 1)
    return series.rolling(period).apply(
        lambda x: np.dot(x, weights) / weights.sum(),
        raw=True
    )

def ema(series, period=9):
    return series.ewm(span=period, adjust=False).mean()

def scan(symbol):
    ticker = symbol + ".NS"

    daily = yf.download(
        ticker, period="1y", interval="1d",
        progress=False, auto_adjust=True
    )

    weekly = yf.download(
        ticker, period="2y", interval="1wk",
        progress=False, auto_adjust=True
    )

    monthly = yf.download(
        ticker, period="5y", interval="1mo",
        progress=False, auto_adjust=True
    )

    if daily.empty or weekly.empty or monthly.empty:
        return None

    def get_close(data):
        close = data["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        return close.dropna()

    d = get_close(daily)
    w = get_close(weekly)
    m = get_close(monthly)

    if min(len(d), len(w), len(m)) < 20:
        return None

    dr = rsi(d)
    wr = rsi(w)
    mr = rsi(m)

    d_ema, d_wma = ema(dr), wma(dr)
    w_ema, w_wma = ema(wr), wma(wr)
    m_wma = wma(mr)

    conditions = [
        dr.iloc[-1] > 50,
        mr.iloc[-1] < 50,
        wr.iloc[-1] < 50,
        w_ema.iloc[-1] > w_wma.iloc[-1],
        d_ema.iloc[-1] > d_wma.iloc[-1],
        d.iloc[-1] > d.iloc[-2],
        daily["Volume"].iloc[-1] > 50000
    ]

    if all(conditions):
        return {
            "symbol": symbol,
            "signal": "BUY",
            "close": round(float(d.iloc[-1]), 2),
            "rsi": round(float(dr.iloc[-1]), 2),
            "as_of": str(d.index[-1].date())
        }

    return None

def scan_all():
    results = []

    for symbol in NIFTY50:
        try:
            result = scan(symbol)
            if result:
                results.append(result)
        except Exception as error:
            print(symbol, error)

    return results
