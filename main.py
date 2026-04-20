import os
import time
import requests

BOT_TOKEN = os.getenv("8752512217:AAEkx1lDTIzuYV8CBAYuQ6WwhfSRMWumZxs")
CHAT_ID = os.getenv("-1003953557811")

if not BOT_TOKEN or not CHAT_ID:
    print("❌ BOT_TOKEN or CHAT_ID missing")
    while True:
        time.sleep(60)

def send(msg):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": msg})

def get_price(symbol):
    r = requests.get(f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}")
    return float(r.json()["price"])

symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

last_price = {}

def main():
    send("🚀 Signal Bot Started")

    while True:
        try:
            for s in symbols:
                price = get_price(s)

                if s not in last_price:
                    last_price[s] = price
                    continue

                # simple trend signal
                if price > last_price[s]:
                    send(f"📈 BUY SIGNAL\n{s}\nPrice: {price}")

                elif price < last_price[s]:
                    send(f"📉 SELL SIGNAL\n{s}\nPrice: {price}")

                last_price[s] = price

            time.sleep(60)

        except Exception as e:
            send(f"⚠️ Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
