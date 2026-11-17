import requests
from datetime import datetime

BOT_TOKEN = __import__("os").getenv("TG_TOKEN")
CHAT_ID = __import__("os").getenv("TG_CHAT_ID")

STOCKS = [
    "RELIANCE", "HDFCBANK", "ICICIBANK",
    "SBIN", "INFY", "TCS", "LT",
    "AXISBANK", "BHARTIARTL", "ITC",
    "TATAMOTORS", "MARUTI", "SUNPHARMA",
    "NTPC", "POWERGRID"
]

def get_data(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.NS?range=3mo&interval=1d"
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    r.raise_for_status()
    data = r.json()["chart"]["result"][0]
    return [x for x in data["indicators"]["quote"][0]["close"] if x is not None]

def ema(values, period):
    k = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = value * k + result * (1-k)
    return result

def rsi(values, period=14):
    changes = [values[i]-values[i-1] for i in range(1,len(values))]
    gains = [max(x,0) for x in changes[-period:]]
    losses = [max(-x,0) for x in changes[-period:]]
    avg_gain = sum(gains)/period
    avg_loss = sum(losses)/period
    if avg_loss == 0:
        return 100
    return 100 - (100/(1+avg_gain/avg_loss))

def main():
    message = "📊 TRADING AI AGENT\n"
    message += datetime.now().strftime("%d-%m-%Y %H:%M") + "\n\n"

    for name, symbol in [("NIFTY", "%5ENSEI"), ("SENSEX", "%5EBSESN")]:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d"
            data = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=15).json()
            price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
            message += f"{name}: {price:.2f}\n"
        except Exception:
            message += f"{name}: Data unavailable\n"

    message += "\n🔍 SWING SCANNER\n"

    for stock in STOCKS:
        try:
            values = get_data(stock)
            if len(values) < 50:
                continue

            e20 = ema(values[-20:], 20)
            e50 = ema(values[-50:], 50)
            strength = rsi(values)

            if e20 > e50 and strength > 50:
                message += f"\n🟢 {stock}\n"
                message += f"Price: {values[-1]:.2f}\n"
                message += f"RSI: {strength:.1f}\n"
                message += "Bullish momentum watch\n"

        except Exception:
            continue

    message += "\n⚠️ Watchlist only. Verify before trading."
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": message},
        timeout=20
    ).raise_for_status()

if __name__ == "__main__":
    main()
