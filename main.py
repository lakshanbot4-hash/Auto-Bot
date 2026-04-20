import os
import time
import requests
import pandas as pd

BOT_TOKEN = (os.getenv("8752512217:AAEkx1lDTIzuYV8CBAYuQ6WwhfSRMWumZxs") or "").strip()
CHAT_ID = (os.getenv("1003953557811") or "").strip()
BASE_URL = "https://fapi.binance.com"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
LOOP_SECONDS = 60


sent_signals = {}

def tg_send(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": text}, timeout=15)

def get_klines(symbol, interval="5m", limit=300):
    r = requests.get(
        f"{BASE_URL}/fapi/v1/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=20
    )
    r.raise_for_status()
    raw = r.json()
    df = pd.DataFrame(raw, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "qav", "trades", "tb_base", "tb_quote", "ignore"
    ])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    return df

def add_indicators(df):
    df = df.copy()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    df["rsi"] = 100 - (100 / (1 + rs))

    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs()
    ], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1/14, adjust=False).mean()
    return df

def get_signal(symbol):
    df1h = add_indicators(get_klines(symbol, "1h", 300))
    df5m = add_indicators(get_klines(symbol, "5m", 300))

    h = df1h.iloc[-2]
    a = df5m.iloc[-3]
    b = df5m.iloc[-2]
    c = df5m.iloc[-1]

    entry = float(c["close"])
    atr = float(b["atr"])

    if atr <= 0:
        return None

    # BUY setup
    if h["ema50"] > h["ema200"]:
        if b["close"] > b["ema20"] > b["ema50"] and 45 <= b["rsi"] <= 68 and b["close"] > a["high"]:
            sl = entry - atr * 1.2
            tp = entry + (entry - sl) * 1.5
            return {
                "symbol": symbol,
                "side": "BUY",
                "entry": round(entry, 4),
                "sl": round(sl, 4),
                "tp": round(tp, 4),
                "reason": "1h uptrend + 5m breakout"
            }

    # SELL setup
    if h["ema50"] < h["ema200"]:
        if b["close"] < b["ema20"] < b["ema50"] and 32 <= b["rsi"] <= 55 and b["close"] < a["low"]:
            sl = entry + atr * 1.2
            tp = entry - (sl - entry) * 1.5
            return {
                "symbol": symbol,
                "side": "SELL",
                "entry": round(entry, 4),
                "sl": round(sl, 4),
                "tp": round(tp, 4),
                "reason": "1h downtrend + 5m breakdown"
            }

    return None

def signal_key(sig):
    return f"{sig['symbol']}_{sig['side']}_{sig['entry']}"

def main():
    tg_send("🚀 Signal bot started")

    while True:
        try:
            for symbol in SYMBOLS:
                sig = get_signal(symbol)
                if sig:
                    key = signal_key(sig)
                    now = time.time()

                    if key not in sent_signals or now - sent_signals[key] > 3600:
                        msg = (
                            f"📡 Binance Futures Signal\n\n"
                            f"Symbol: {sig['symbol']}\n"
                            f"Side: {sig['side']}\n"
                            f"Entry: {sig['entry']}\n"
                            f"SL: {sig['sl']}\n"
                            f"TP: {sig['tp']}\n"
                            f"Reason: {sig['reason']}"
                        )
                        tg_send(msg)
                        sent_signals[key] = now

            time.sleep(LOOP_SECONDS)

        except Exception as e:
            tg_send(f"⚠️ Bot error: {e}")
            time.sleep(15)

if __name__ == "__main__":
    main()
