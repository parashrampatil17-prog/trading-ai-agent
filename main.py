import os
import requests
from datetime import datetime

BOT_TOKEN = os.getenv("TG_TOKEN")
CHAT_ID = os.getenv("TG_CHAT_ID")

def send_alert(message):
    if not BOT_TOKEN or not CHAT_ID:
        raise ValueError("Telegram secrets are missing")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": message
        },
        timeout=15
    )

    response.raise_for_status()
    print("Telegram alert sent successfully!")

def main():
    message = (
        "Trading AI Agent Started Successfully! 🚀\n"
        + datetime.now().strftime("%d-%m-%Y %H:%M")
    )

    send_alert(message)

if __name__ == "__main__":
    main()
