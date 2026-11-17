import numpy as np
import pandas as pd
import yfinance as yf
import time

# NIFTY 50 - 50 STOCKS
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
    "WIPRO", "TMPV"
]

PERIOD = 9
VOLUME_MIN = 50000


def get_col(df, name):
    s = df[name]

    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]

    return s.dropna().astype(float)


def calculate_rsi(close, period=9):
    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    rsi = 100 - (100 / (1 + rs))

    rsi = rsi.mask(
        (avg_loss == 0) & (avg_gain > 0), 100
    )

    rsi = rsi.mask(
        (avg_gain == 0) & (avg_loss > 0), 0
    )

    return rsi


def calculate_wma(series, period=9):
    weights = np.arange(1, period + 1)

    return series.rolling(period).apply(
        lambda x: np.dot(x, weights) / weights.sum(),
        raw=True
    )


def calculate_ema(series, period=9):
    return series.ewm(
        span=period,
        adjust=False,
        min_periods=period
    ).mean()


def download_data(ticker, period, interval):
    return yf.download(
        ticker,
        period=period,
        interval=interval,
        progress=False,
        auto_adjust=True,
        threads=False,
        timeout=20
    )


def scan(symbol):

    ticker = symbol + ".NS"

    daily = download_data(ticker, "1y", "1d")
    weekly = download_data(ticker, "2y", "1wk")
    monthly = download_data(ticker, "5y", "1mo")

    if daily.empty or weekly.empty or monthly.empty:
        return None

    dc = get_col(daily, "Close")
    wc = get_col(weekly, "Close")
    mc = get_col(monthly, "Close")

    volume = get_col(daily, "Volume")

    if min(len(dc), len(wc), len(mc)) < 20:
        return None

    if len(volume) < 2:
        return None

    daily_rsi = calculate_rsi(dc)
    weekly_rsi = calculate_rsi(wc)
    monthly_rsi = calculate_rsi(mc)

    daily_ema = calculate_ema(daily_rsi)
    daily_wma = calculate_wma(daily_rsi)

    weekly_ema = calculate_ema(weekly_rsi)
    weekly_wma = calculate_wma(weekly_rsi)

    monthly_wma = calculate_wma(monthly_rsi)

    indicators = [
        daily_rsi,
        daily_ema,
        daily_wma,
        weekly_ema,
        weekly_wma,
        monthly_wma
    ]

    if any(x.dropna().empty for x in indicators):
        return None

    close = float(dc.iloc[-1])
    previous_close = float(dc.iloc[-2])
    current_volume = int(volume.iloc[-1])

    checks = [
        float(daily_rsi.iloc[-1]) > 50,
        float(monthly_wma.iloc[-1]) < 50,
        float(weekly_wma.iloc[-1]) < 50,
        float(weekly_ema.iloc[-1]) > float(weekly_wma.iloc[-1]),
        float(daily_ema.iloc[-1]) > float(daily_wma.iloc[-1]),
        close > previous_close,
        current_volume > VOLUME_MIN
    ]

    if not all(checks):
        return None

    high = get_col(daily, "High")
    low = get_col(daily, "Low")

    previous = dc.shift(1)

    tr = pd.concat([
        high - low,
        (high - previous).abs(),
        (low - previous).abs()
    ], axis=1).max(axis=1)

    atr = float(tr.rolling(14).mean().iloc[-1])

    if not np.isfinite(atr) or atr <= 0:
        atr = close * 0.02

    stop_loss = round(close - atr, 2)

    risk = max(close - stop_loss, close * 0.005)

    return {
        "symbol": symbol,
        "signal": "BUY WATCH",
        "close": round(close, 2),
        "rsi": round(float(daily_rsi.iloc[-1]), 2),
        "volume": current_volume,
        "entry": round(close, 2),
        "stop_loss": stop_loss,
        "target1": round(close + risk * 1.5, 2),
        "target2": round(close + risk * 2, 2),
        "risk_reward_t1": "1:1.5",
        "risk_reward_t2": "1:2"
    }


def scan_all():

    results = []

    print("HILEGA MILEGA SCANNER STARTED")

    for symbol in NIFTY50:

        try:
            print("Scanning:", symbol)

            result = scan(symbol)

            if result:
                results.append(result)
                print("SIGNAL FOUND:", symbol)

        except Exception as error:
            print("Scanner skipped", symbol, ":", str(error))

        time.sleep(0.3)

    print("SCANNER COMPLETED")
    print("Total BUY WATCH signals:", len(results))

    return results
