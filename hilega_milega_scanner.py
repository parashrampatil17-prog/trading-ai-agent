import numpy as np
import pandas as pd
import yfinance as yf

NIFTY50 = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT",
    "AXISBANK", "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV",
    "BEL", "BHARTIARTL", "CIPLA", "COALINDIA", "DRREDDY",
    "EICHERMOT", "ETERNAL", "GRASIM", "HCLTECH",
    "HDFCBANK", "HDFCLIFE", "HEROMOTOCO", "HINDALCO",
    "HINDUNILVR", "ICICIBANK", "INDUSINDBK", "INFY",
    "ITC", "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT",
    "M&M", "MARUTI", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SBIN",
    "SHRIRAMFIN", "SUNPHARMA", "TATACONSUM", "TATASTEEL",
    "TCS", "TECHM", "TITAN", "TRENT", "ULTRACEMCO",
    "WIPRO"
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

    result = 100 - 100 / (1 + rs)

    result = result.mask(
        (avg_loss == 0) & (avg_gain > 0), 100
    )

    result = result.mask(
        (avg_gain == 0) & (avg_loss > 0), 0
    )

    return result


def calculate_wma(series, period=9):
    weights = np.arange(1, period + 1, dtype=float)

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


def scan(symbol):

    ticker = symbol + ".NS"

    daily = yf.download(
        ticker,
        period="1y",
        interval="1d",
        progress=False,
        auto_adjust=True
    )

    weekly = yf.download(
        ticker,
        period="2y",
        interval="1wk",
        progress=False,
        auto_adjust=True
    )

    monthly = yf.download(
        ticker,
        period="5y",
        interval="1mo",
        progress=False,
        auto_adjust=True
    )

    if daily.empty or weekly.empty or monthly.empty:
        return None

    d_close = get_col(daily, "Close")
    w_close = get_col(weekly, "Close")
    m_close = get_col(monthly, "Close")
    volume = get_col(daily, "Volume")

    if min(len(d_close), len(w_close), len(m_close)) < 20:
        return None

    if len(volume) < 2:
        return None

    d_rsi = calculate_rsi(d_close)
    w_rsi = calculate_rsi(w_close)
    m_rsi = calculate_rsi(m_close)

    d_ema = calculate_ema(d_rsi)
    d_wma = calculate_wma(d_rsi)

    w_ema = calculate_ema(w_rsi)
    w_wma = calculate_wma(w_rsi)

    m_wma = calculate_wma(m_rsi)

    indicators = [
        d_rsi, d_ema, d_wma,
        w_ema, w_wma, m_wma
    ]

    if any(x.dropna().empty for x in indicators):
        return None

    close = float(d_close.iloc[-1])
    previous_close = float(d_close.iloc[-2])
    current_volume = int(volume.iloc[-1])

    conditions = [

        # Daily RSI above 50
        float(d_rsi.iloc[-1]) > 50,

        # Monthly RSI WMA below 50
        float(m_wma.iloc[-1]) < 50,

        # Weekly RSI WMA below 50
        float(w_wma.iloc[-1]) < 50,

        # Weekly EMA above WMA
        float(w_ema.iloc[-1]) > float(w_wma.iloc[-1]),

        # Daily EMA above WMA
        float(d_ema.iloc[-1]) > float(d_wma.iloc[-1]),

        # Daily positive change
        close > previous_close,

        # Volume above 50000
        current_volume > VOLUME_MIN
    ]

    if not all(conditions):
        return None

    high = get_col(daily, "High")
    low = get_col(daily, "Low")

    previous = d_close.shift(1)

    true_range = pd.concat([
        high - low,
        (high - previous).abs(),
        (low - previous).abs()
    ], axis=1).max(axis=1)

    atr = float(true_range.rolling(14).mean().iloc[-1])

    if not np.isfinite(atr) or atr <= 0:
        atr = close * 0.02

    stop_loss = round(close - atr, 2)

    risk = max(close - stop_loss, close * 0.005)

    target1 = round(close + risk * 1.5, 2)
    target2 = round(close + risk * 2, 2)

    return {
        "symbol": symbol,
        "signal": "BUY WATCH",
        "close": round(close, 2),
        "rsi": round(float(d_rsi.iloc[-1]), 2),
        "volume": current_volume,
        "entry": round(close, 2),
        "stop_loss": stop_loss,
        "target1": target1,
        "target2": target2,
        "risk_reward_t1": "1:1.5",
        "risk_reward_t2": "1:2"
    }


def scan_all():

    results = []

    for symbol in NIFTY50:

        try:
            result = scan(symbol)

            if result:
                results.append(result)

        except Exception as error:
            print(
                f"Scanner skipped {symbol}: {error}"
            )

    return results
