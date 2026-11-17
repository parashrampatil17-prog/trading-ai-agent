import os
import requests
from datetime import datetime

BOT_TOKEN = os.getenv("TG_TOKEN")
CHAT_ID = os.getenv("TG_CHAT_ID")

def send_alert(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram credentials not configured")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(
        url,
        data={"chat_id": CHAT_ID, "text": message},
        timeout=15
    )

def main():
    print("Trading AI Agent Started")

    message = (
        "Trading AI Agent Started Successfully!\n"
        + datetime.now().strftime("%d-%m-%Y %H:%M")
    )

    send_alert(message)

if __name__ == "__main__":
    main()
