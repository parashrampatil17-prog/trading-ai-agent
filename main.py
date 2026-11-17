import os
import json
import requests
from datetime import datetime

BOT_TOKEN = os.getenv("TG_TOKEN")
CHAT_ID = os.getenv("TG_CHAT_ID")

STATE_FILE = "signal_state.json"
HEADERS = {"User-Agent": "Mozilla/5.0"}

STOCKS = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN",
    "INFY", "TCS", "LT", "AXISBANK", "BHARTIARTL",
    "ITC", "TATAMOTORS", "MARUTI", "SUNPHARMA",
    "NTPC", "POWERGRID"
]

MARKETS = {
    "NIFTY": "^NSEI",
    "SENSEX": "^BSESN"
}


def load_state():
    try:
        with open(STATE_FILE, "r") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as file:
        json.dump(state, file, indent=2)


def get_data(symbol):
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{symbol}?range=3mo&interval=1d"
    )

    response = requests.get(
        url, headers=HEADERS, timeout=20
    )
    response.raise_for_status()

    result = response.json()["chart"]["result"]

    if not result:
        raise ValueError("Market data unavailable")

    quote = result[0]["indicators"]["quote"][0]

    closes = quote["close"]
    highs = quote["high"]
    lows = quote["low"]

    return [
        (c, h, l)
        for c, h, l in zip(closes, highs, lows)
        if c is not None and h is not None and l is not None
    ]


def ema(values, period):
    if len(values) < period:
        return None

    k = 2 / (period + 1)
    result = sum(values[:period]) / period

    for value in values[period:]:
        result = value * k + result * (1 - k)

    return result


def rsi(values, period=14):
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
        avg_gain = (
            avg_gain * (period - 1) + gain
        ) / period

        avg_loss = (
            avg_loss * (period - 1) + loss
        ) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("Telegram credentials missing")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": message
        },
        timeout=20
    )

    response.raise_for_status()


def analyze_market(rows):
    closes = [r[0] for r in rows]
    highs = [r[1] for r in rows]
    lows = [r[2] for r in rows]

    if len(closes) < 50:
        return None

    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    strength = rsi(closes)

    price = closes[-1]

    resistance = max(highs[-21:-1])
    support = min(lows[-21:-1])

    signal = "NEUTRAL"

    if price > resistance and e20 > e50 and strength > 50:
        signal = "BULLISH BREAKOUT"

    elif price < support and e20 < e50 and strength < 50:
        signal = "BEARISH BREAKDOWN"

    elif e20 > e50 and strength > 55:
        signal = "BULLISH TREND"

    elif e20 < e50 and strength < 45:
        signal = "BEARISH TREND"

    return {
        "signal": signal,
        "price": round(price, 2),
        "ema20": round(e20, 2),
        "ema50": round(e50, 2),
        "rsi": round(strength, 2),
        "support": round(support, 2),
        "resistance": round(resistance, 2)
    }


def main():

    state = load_state()
    new_state = dict(state)
    alerts = []

    print("TRADING AI AGENT STARTED")

    instruments = list(MARKETS.items())

    instruments += [
        (stock, stock + ".NS")
        for stock in STOCKS
    ]

    for name, symbol in instruments:

        try:
            rows = get_data(symbol)
            result = analyze_market(rows)

            if result is None:
                continue

            signal = result["signal"]
            old_signal = state.get(name, "NEUTRAL")

            new_state[name] = signal

            if signal != "NEUTRAL" and signal != old_signal:

                alert = (
                    f"🔔 NEW SIGNAL: {name}\n\n"
                    f"Signal: {signal}\n"
                    f"Price: {result['price']}\n"
                    f"EMA20: {result['ema20']}\n"
                    f"EMA50: {result['ema50']}\n"
                    f"RSI: {result['rsi']}\n"
                    f"Support: {result['support']}\n"
                    f"Resistance: {result['resistance']}"
                )

                alerts.append(alert)

            print(name, signal)

        except Exception as error:
            print(name, "ERROR:", error)

    if alerts:

        message = (
            "🚀 TRADING AI AGENT\n"
            + datetime.now().strftime("%d-%m-%Y %H:%M")
            + "\n\n"
            + "\n\n----------------\n\n".join(alerts)
            + "\n\n⚠️ Research alerts only. Verify before trading."
        )

        send_telegram(message)

        print("New alerts sent:", len(alerts))

    else:
        print("No new signals.")

    save_state(new_state)


if __name__ == "__main__":
    main()
