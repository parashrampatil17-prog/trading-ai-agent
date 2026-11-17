import os
import json
import requests
from datetime import datetime

BOT_TOKEN = os.getenv("TG_TOKEN")
CHAT_ID = os.getenv("TG_CHAT_ID")

STATE_FILE = "signal_state.json"

STOCKS = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN",
    "INFY", "TCS", "LT", "AXISBANK", "BHARTIARTL",
    "ITC", "TATAMOTORS", "MARUTI", "SUNPHARMA",
    "NTPC", "POWERGRID"
]

HEADERS = {"User-Agent": "Mozilla/5.0"}


def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("Telegram environment variables missing")

    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": message},
        timeout=20
    )
    response.raise_for_status()


def get_data(symbol):
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{symbol}?range=3mo&interval=1d"
    )

    response = requests.get(url, headers=HEADERS, timeout=15)
    response.raise_for_status()

    result = response.json()["chart"]["result"]

    if not result:
        raise ValueError("No market data")

    quote = result[0]["indicators"]["quote"][0]

    closes = quote["close"]
    highs = quote["high"]
    lows = quote["low"]

    rows = [
        (c, h, l)
        for c, h, l in zip(closes, highs, lows)
        if c is not None and h is not None and l is not None
    ]

    return rows


def ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)
    result = sum(values[:period]) / period

    for value in values[period:]:
        result = (value - result) * multiplier + result

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
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def get_signal(rows):
    closes = [r[0] for r in rows]
    highs = [r[1] for r in rows]
    lows = [r[2] for r in rows]

    if len(closes) < 50:
        return None

    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    strength = rsi(closes)

    price = closes[-1]

    # Previous 20 completed candles, excluding current candle
    resistance = max(highs[-21:-1])
    support = min(lows[-21:-1])

    signal = "NEUTRAL"

    if (
        price > resistance
        and e20 > e50
        and strength > 50
    ):
        signal = "BULLISH BREAKOUT"

    elif (
        price < support
        and e20 < e50
        and strength < 50
    ):
        signal = "BEARISH BREAKDOWN"

    elif e20 > e50 and strength > 55:
        signal = "BULLISH WATCH"

    elif e20 < e50 and strength < 45:
        signal = "BEARISH WATCH"

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

    instruments = [
        ("NIFTY", "^NSEI"),
        ("SENSEX", "^BSESN")
    ]

    for name, symbol in instruments + [
        (stock, stock + ".NS") for stock in STOCKS
    ]:
        try:
            rows = get_data(symbol)
            result = get_signal(rows)

            if not result:
                continue

            signal = result["signal"]
            old_signal = state.get(name, "NEUTRAL")

            new_state[name] = signal

            if signal != "NEUTRAL" and signal != old_signal:
                alerts.append(
                    f"🔔 {name}\n"
                    f"Signal: {signal}\n"
                    f"Price: {result['price']}\n"
                    f"EMA20: {result['ema20']}\n"
                    f"EMA50: {result['ema50']}\n"
                    f"RSI: {result['rsi']}\n"
                    f"Support: {result['support']}\n"
                    f"Resistance: {result['resistance']}"
                )

        except Exception as error:
            print(f"{name} scan error: {error}")

    if alerts:
        message = (
            "🚀 TRADING AI – NEW SIGNALS\n"
            + datetime.now().strftime("%d-%m-%Y %H:%M")
            + "\n\n"
            + "\n\n--------------------\n\n".join(alerts)
            + "\n\n⚠️ Research alerts only. Verify before trading."
        )

        send_telegram(message)
        print(f"Sent {len(alerts)} new alerts.")
    else:
        print("No new signals.")

    save_state(new_state)


if __name__ == "__main__":
    main()

Step 2

कोड Paste केल्यानंतर Commit changes करा.

झाल्यावर फक्त "Done" सांगा.

महत्त्वाची सूचना: हा Scanner सध्या Daily candles वापरतो. त्यामुळे दर 5 मिनिटांनी तपासणी झाली तरी नवीन Signal फक्त Daily Data बदलल्यावर तयार होऊ शकतो. हा Research Scanner आहे, Buy/Sell आदेश देत नाही. "main.yml" अजून बदलू नका.
