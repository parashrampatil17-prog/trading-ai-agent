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
        f"{symbol}.NS?range=3mo&interval=1d"
    )

    response = requests.get(
        url, headers=HEADERS, timeout=20
    )
    response.raise_for_status()

    data = response.json()["chart"]["result"][0]
    closes = data["indicators"]["quote"][0]["close"]

    return [x for x in closes if x is not None]


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


def main():
    state = load_state()
    new_signals = []

    print("Trading AI Agent started")

    for stock in STOCKS:
        try:
            values = get_data(stock)

            if len(values) < 50:
                continue

            e20 = ema(values, 20)
            e50 = ema(values, 50)
            strength = rsi(values)

            if e20 is None or e50 is None or strength is None:
                continue

            bullish = e20 > e50 and strength > 50
            previous_bullish = state.get(stock, False)

            if bullish and not previous_bullish:
                new_signals.append(
                    f"🟢 {stock}\n"
                    f"Price: {values[-1]:.2f}\n"
                    f"EMA20: {e20:.2f}\n"
                    f"EMA50: {e50:.2f}\n"
                    f"RSI: {strength:.2f}\n"
                    f"BULLISH WATCH"
                )

            state[stock] = bullish

            print(stock, "Bullish" if bullish else "No signal")

        except Exception as error:
            print(stock, "Error:", error)

    save_state(state)

    if new_signals:
        message = (
            "🚀 TRADING AI AGENT\n"
            f"{datetime.now().strftime('%d-%m-%Y %H:%M')}\n\n"
            "NEW BULLISH SIGNALS\n\n"
            + "\n\n".join(new_signals)
            + "\n\n⚠️ Watchlist only. Verify before trading."
        )

        send_telegram(message)
        print("New signal alert sent")

    else:
        print("No new bullish signals")


if __name__ == "__main__":
    main()
