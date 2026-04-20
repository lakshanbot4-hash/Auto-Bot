import os
import time
import hmac
import hashlib
import requests

# ENV VARIABLES
API_KEY = os.getenv("qQPzHRXBeGnSTXw3sJ1wp69uY8oH3J0V8SiRXcKnhmLtjCMi13lG64iBdVPuMOYa")
API_SECRET = os.getenv("cpcViAholLoiTrfPalyuS5KjJVmEhKhbaJ2xsYm2ETDKQorFVJBLl7mxlT2ozVoC")
BOT_TOKEN = os.getenv("8752512217:AAEkx1lDTIzuYV8CBAYuQ6WwhfSRMWumZxs")
CHAT_ID = os.getenv("1003953557811")

BASE_URL = "https://fapi.binance.com"
SYMBOL = "BTCUSDT"
LEVERAGE = 5
QTY = 0.001  # small safe qty

# TELEGRAM
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": msg}
    requests.post(url, json=data)

# SIGN
def sign(params):
    query = "&".join([f"{k}={v}" for k,v in params.items()])
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

# SET LEVERAGE
def set_leverage():
    params = {
        "symbol": SYMBOL,
        "leverage": LEVERAGE,
        "timestamp": int(time.time()*1000)
    }
    params["signature"] = sign(params)
    requests.post(BASE_URL+"/fapi/v1/leverage", headers={"X-MBX-APIKEY": API_KEY}, params=params)

# GET PRICE
def get_price():
    r = requests.get(BASE_URL+"/fapi/v1/ticker/price", params={"symbol": SYMBOL})
    return float(r.json()["price"])

# MARKET ORDER
def order(side):
    params = {
        "symbol": SYMBOL,
        "side": side,
        "type": "MARKET",
        "quantity": QTY,
        "timestamp": int(time.time()*1000)
    }
    params["signature"] = sign(params)
    r = requests.post(BASE_URL+"/fapi/v1/order",
        headers={"X-MBX-APIKEY": API_KEY},
        params=params
    )
    return r.json()

# SIMPLE STRATEGY
last_price = None

def run():
    global last_price

    send_telegram("🚀 Bot Started")

    set_leverage()

    while True:
        try:
            price = get_price()

            if last_price is None:
                last_price = price
                continue

            # SIMPLE LOGIC
            if price > last_price:
                order("BUY")
                send_telegram(f"📈 BUY {price}")
            elif price < last_price:
                order("SELL")
                send_telegram(f"📉 SELL {price}")

            last_price = price
            time.sleep(60)

        except Exception as e:
            send_telegram(f"⚠️ Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    run()
