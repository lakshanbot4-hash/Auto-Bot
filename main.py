import os
import time
import json
import hmac
import math
import hashlib
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlencode

import requests
import pandas as pd

# =========================
# CONFIG FROM ENV
# =========================
BINANCE_API_KEY = os.getenv("VMYilkqC7UuXzhaaMGLBRipsAmFA6nsiVwBg4N6MA3AlI3BOa26JtcAYTEUIwnJK").strip()
BINANCE_API_SECRET = os.getenv("mPYonE5yHrqNYiHw2x1ETFpAPWcHRT4iBUI7CuQwFmZUjWrfJLbTtSevvJqEZ730").strip()
BOT_TOKEN = os.getenv("8752512217:AAG0Y6ogZ_1lUYKuu5heUm1Vs2dVZxxxK8w", "").strip()
CHAT_ID = os.getenv("-1003953557811", "").strip()

BASE_URL = os.getenv("BASE_URL", "https://fapi.binance.com").strip()
SYMBOLS = [s.strip().upper() for s in os.getenv("SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT").split(",") if s.strip()]
LEVERAGE = int(os.getenv("LEVERAGE", "10"))
LOOP_SECONDS = int(os.getenv("LOOP_SECONDS", "30"))
TIMEZONE = os.getenv("TIMEZONE", "Asia/Colombo")
DAILY_TARGET_USDT = float(os.getenv("DAILY_TARGET_USDT", "1.0"))
DAILY_MAX_LOSS_USDT = float(os.getenv("DAILY_MAX_LOSS_USDT", "2.0"))
MAX_CONSECUTIVE_LOSSES = int(os.getenv("MAX_CONSECUTIVE_LOSSES", "2"))

# Position sizing:
# wallet balance * capital fraction = margin used
# margin * leverage = notional
CAPITAL_FRACTION = float(os.getenv("CAPITAL_FRACTION", "0.20"))  # 20% of wallet as margin
MIN_SIGNAL_SCORE = float(os.getenv("MIN_SIGNAL_SCORE", "2.0"))

# Strategy tuning
ATR_STOP_MULT = float(os.getenv("ATR_STOP_MULT", "1.2"))
TP_MULT = float(os.getenv("TP_MULT", "1.5"))  # target = risk * TP_MULT
COOLDOWN_MINUTES = int(os.getenv("COOLDOWN_MINUTES", "15"))

STATE_FILE = "state.json"
SESSION = requests.Session()
SESSION.headers.update({"X-MBX-APIKEY": BINANCE_API_KEY})

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

if not BINANCE_API_KEY or not BINANCE_API_SECRET:
    raise ValueError("Missing BINANCE_API_KEY or BINANCE_API_SECRET")
if not BOT_TOKEN or not CHAT_ID:
    raise ValueError("Missing BOT_TOKEN or CHAT_ID")


# =========================
# STATE
# =========================
def default_state():
    return {
        "day": None,
        "day_start_wallet": None,
        "consecutive_losses": 0,
        "open_trade": None,
        "last_entry_ts": 0
    }


def load_state():
    if not os.path.exists(STATE_FILE):
        return default_state()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default_state()


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


STATE = load_state()


# =========================
# TELEGRAM
# =========================
def tg_send(text: str):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": text}
        requests.post(url, json=payload, timeout=15)
    except Exception as e:
        logging.error("Telegram send error: %s", e)


# =========================
# BINANCE HELPERS
# =========================
def _ts():
    return int(time.time() * 1000)


def sign_params(params: dict) -> str:
    query = urlencode(params, doseq=True)
    return hmac.new(
        BINANCE_API_SECRET.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


def public_get(path: str, params=None):
    params = params or {}
    url = BASE_URL + path
    r = SESSION.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def signed_request(method: str, path: str, params=None):
    params = params or {}
    params["timestamp"] = _ts()
    params["recvWindow"] = 5000
    params["signature"] = sign_params(params)
    url = BASE_URL + path

    if method.upper() == "GET":
        r = SESSION.get(url, params=params, timeout=20)
    elif method.upper() == "POST":
        r = SESSION.post(url, params=params, timeout=20)
    elif method.upper() == "DELETE":
        r = SESSION.delete(url, params=params, timeout=20)
    else:
        raise ValueError("Unsupported method")

    if r.status_code >= 400:
        logging.error("Binance error %s %s -> %s", method, path, r.text)
        r.raise_for_status()
    return r.json()


# =========================
# MARKET DATA
# =========================
EXCHANGE_RULES = {}


def fetch_exchange_rules():
    global EXCHANGE_RULES
    data = public_get("/fapi/v1/exchangeInfo")
    rules = {}
    for sym in data.get("symbols", []):
        s = sym["symbol"]
        filt = {f["filterType"]: f for f in sym.get("filters", [])}
        rules[s] = {
            "pricePrecision": sym.get("pricePrecision", 2),
            "quantityPrecision": sym.get("quantityPrecision", 3),
            "stepSize": float(filt.get("LOT_SIZE", {}).get("stepSize", "0.001")),
            "minQty": float(filt.get("LOT_SIZE", {}).get("minQty", "0.001")),
            "tickSize": float(filt.get("PRICE_FILTER", {}).get("tickSize", "0.01")),
            "minNotional": float(filt.get("MIN_NOTIONAL", {}).get("notional", "5")),
        }
    EXCHANGE_RULES = rules


def round_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return math.floor(value / step) * step


def round_tick(value: float, tick: float) -> float:
    if tick <= 0:
        return value
    return math.floor(value / tick) * tick


def get_klines(symbol: str, interval: str, limit: int = 300) -> pd.DataFrame:
    raw = public_get("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    df = pd.DataFrame(raw, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "qav", "trades", "tb_base", "tb_quote", "ignore"
    ])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ema20"] = out["close"].ewm(span=20, adjust=False).mean()
    out["ema50"] = out["close"].ewm(span=50, adjust=False).mean()
    out["ema200"] = out["close"].ewm(span=200, adjust=False).mean()

    delta = out["close"].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    out["rsi"] = 100 - (100 / (1 + rs))

    prev_close = out["close"].shift(1)
    tr = pd.concat([
        out["high"] - out["low"],
        (out["high"] - prev_close).abs(),
        (out["low"] - prev_close).abs()
    ], axis=1).max(axis=1)
    out["atr"] = tr.ewm(alpha=1/14, adjust=False).mean()
    return out


# =========================
# ACCOUNT / ORDER
# =========================
def get_account():
    return signed_request("GET", "/fapi/v3/account")


def get_wallet_balance() -> float:
    acc = get_account()
    # totalWalletBalance is standard on account payload
    return float(acc.get("totalWalletBalance", 0.0))


def get_positions():
    return signed_request("GET", "/fapi/v3/positionRisk")


def get_open_position():
    positions = get_positions()
    for p in positions:
        symbol = p.get("symbol")
        amt = float(p.get("positionAmt", 0))
        if symbol in SYMBOLS and abs(amt) > 0:
            return {
                "symbol": symbol,
                "amount": amt,
                "entryPrice": float(p.get("entryPrice", 0)),
                "markPrice": float(p.get("markPrice", 0)),
                "unRealizedProfit": float(p.get("unRealizedProfit", 0)),
                "leverage": int(float(p.get("leverage", LEVERAGE)))
            }
    return None


def set_leverage(symbol: str, leverage: int):
    try:
        signed_request("POST", "/fapi/v1/leverage", {
            "symbol": symbol,
            "leverage": leverage
        })
    except Exception as e:
        logging.warning("Set leverage failed for %s: %s", symbol, e)


def place_market_order(symbol: str, side: str, qty: float, reduce_only: bool = False):
    params = {
        "symbol": symbol,
        "side": side,              # BUY / SELL
        "type": "MARKET",
        "quantity": format_qty(symbol, qty),
        "newOrderRespType": "RESULT"
    }
    if reduce_only:
        params["reduceOnly"] = "true"
    return signed_request("POST", "/fapi/v1/order", params)


def format_qty(symbol: str, qty: float) -> str:
    rules = EXCHANGE_RULES[symbol]
    step = rules["stepSize"]
    q = round_step(qty, step)
    precision = max(0, int(round(-math.log10(step))) if step < 1 else 0)
    return f"{q:.{precision}f}"


def calc_order_qty(symbol: str, price: float, wallet_balance: float) -> float:
    rules = EXCHANGE_RULES[symbol]
    margin_to_use = max(wallet_balance * CAPITAL_FRACTION, 1.0)
    notional = margin_to_use * LEVERAGE
    min_notional = max(rules["minNotional"], 5.0)

    if notional < min_notional:
        notional = min_notional

    qty = notional / price
    qty = round_step(qty, rules["stepSize"])

    if qty < rules["minQty"]:
        qty = rules["minQty"]

    # final notional check
    if qty * price < min_notional:
        qty = round_step((min_notional / price) + rules["stepSize"], rules["stepSize"])

    return qty


# =========================
# STRATEGY
# =========================
def score_symbol(symbol: str):
    df1h = add_indicators(get_klines(symbol, "1h", 300))
    df5m = add_indicators(get_klines(symbol, "5m", 300))

    h = df1h.iloc[-2]  # last closed candle
    a = df5m.iloc[-3]
    b = df5m.iloc[-2]  # signal candle (closed)
    c = df5m.iloc[-1]  # current candle

    trend_up = h["ema50"] > h["ema200"]
    trend_down = h["ema50"] < h["ema200"]

    # Basic volatility filter
    atr_pct = float(b["atr"] / b["close"]) if b["close"] else 0.0
    if atr_pct < 0.002:
        return None

    score = 0.0
    side = None
    entry = float(c["close"])

    # Long setup
    if trend_up:
        if b["close"] > b["ema20"] > b["ema50"]:
            score += 1.0
        if 45 <= b["rsi"] <= 68:
            score += 1.0
        if b["close"] > a["high"]:
            score += 1.5
        if b["volume"] > a["volume"]:
            score += 0.5
        if score >= MIN_SIGNAL_SCORE:
            side = "BUY"

    # Short setup
    if trend_down and side is None:
        score = 0.0
        if b["close"] < b["ema20"] < b["ema50"]:
            score += 1.0
        if 32 <= b["rsi"] <= 55:
            score += 1.0
        if b["close"] < a["low"]:
            score += 1.5
        if b["volume"] > a["volume"]:
            score += 0.5
        if score >= MIN_SIGNAL_SCORE:
            side = "SELL"

    if side is None:
        return None

    atr = float(b["atr"])
    if side == "BUY":
        stop = entry - atr * ATR_STOP_MULT
        risk = entry - stop
        target = entry + risk * TP_MULT
    else:
        stop = entry + atr * ATR_STOP_MULT
        risk = stop - entry
        target = entry - risk * TP_MULT

    if risk <= 0:
        return None

    return {
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "stop": stop,
        "target": target,
        "score": round(score, 2),
        "atr_pct": round(atr_pct * 100, 3)
    }


def choose_best_signal():
    signals = []
    for symbol in SYMBOLS:
        try:
            sig = score_symbol(symbol)
            if sig:
                signals.append(sig)
        except Exception as e:
            logging.error("Signal error %s: %s", symbol, e)

    if not signals:
        return None

    signals.sort(key=lambda x: x["score"], reverse=True)
    return signals[0]


# =========================
# DAILY GUARDS
# =========================
def now_local():
    return datetime.now(ZoneInfo(TIMEZONE))


def reset_day_if_needed():
    today = now_local().date().isoformat()
    wallet = get_wallet_balance()

    if STATE["day"] != today:
        STATE["day"] = today
        STATE["day_start_wallet"] = wallet
        STATE["consecutive_losses"] = 0
        save_state(STATE)
        tg_send(f"📅 New trading day started\nDay start wallet: {wallet:.4f} USDT")


def get_day_pnl() -> float:
    wallet = get_wallet_balance()
    start = STATE.get("day_start_wallet") or wallet
    return wallet - start


def daily_limits_hit() -> bool:
    pnl = get_day_pnl()
    if pnl >= DAILY_TARGET_USDT:
        return True
    if pnl <= -abs(DAILY_MAX_LOSS_USDT):
        return True
    if STATE.get("consecutive_losses", 0) >= MAX_CONSECUTIVE_LOSSES:
        return True
    return False


# =========================
# TRADE MANAGEMENT
# =========================
def enter_trade(signal: dict):
    wallet = get_wallet_balance()
    symbol = signal["symbol"]
    side = signal["side"]
    entry = signal["entry"]

    set_leverage(symbol, LEVERAGE)
    qty = calc_order_qty(symbol, entry, wallet)
    order = place_market_order(symbol, side, qty, reduce_only=False)

    filled_price = entry
    avg_price = order.get("avgPrice")
    if avg_price:
        try:
            filled_price = float(avg_price)
        except Exception:
            pass

    STATE["open_trade"] = {
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "entry": filled_price,
        "stop": signal["stop"],
        "target": signal["target"],
        "opened_at": int(time.time()),
        "score": signal["score"]
    }
    STATE["last_entry_ts"] = int(time.time())
    save_state(STATE)

    tg_send(
        f"✅ ENTRY\n"
        f"Symbol: {symbol}\n"
        f"Side: {side}\n"
        f"Qty: {qty}\n"
        f"Entry: {filled_price:.4f}\n"
        f"SL: {signal['stop']:.4f}\n"
        f"TP: {signal['target']:.4f}\n"
        f"Score: {signal['score']}"
    )


def close_trade(reason: str):
    trade = STATE.get("open_trade")
    if not trade:
        return

    symbol = trade["symbol"]
    side = trade["side"]
    qty = trade["qty"]
    entry = trade["entry"]

    exit_side = "SELL" if side == "BUY" else "BUY"

    # Get latest price before exit
    px = float(get_klines(symbol, "1m", 2).iloc[-1]["close"])

    place_market_order(symbol, exit_side, qty, reduce_only=True)

    # Approximate PnL before fees
    pnl = (px - entry) * qty if side == "BUY" else (entry - px) * qty

    if pnl < 0:
        STATE["consecutive_losses"] = STATE.get("consecutive_losses", 0) + 1
    else:
        STATE["consecutive_losses"] = 0

    tg_send(
        f"🛑 EXIT\n"
        f"Reason: {reason}\n"
        f"Symbol: {symbol}\n"
        f"Exit Price: {px:.4f}\n"
        f"Approx PnL: {pnl:.4f} USDT\n"
        f"Consecutive losses: {STATE['consecutive_losses']}"
    )

    STATE["open_trade"] = None
    save_state(STATE)


def manage_open_trade():
    trade = STATE.get("open_trade")
    if not trade:
        return

    symbol = trade["symbol"]
    side = trade["side"]
    stop = trade["stop"]
    target = trade["target"]

    px = float(get_klines(symbol, "1m", 2).iloc[-1]["close"])

    if side == "BUY":
        if px <= stop:
            close_trade("Stop loss hit")
            return
        if px >= target:
            close_trade("Target hit")
            return
    else:
        if px >= stop:
            close_trade("Stop loss hit")
            return
        if px <= target:
            close_trade("Target hit")
            return


def cooldown_active() -> bool:
    last_ts = STATE.get("last_entry_ts", 0)
    if not last_ts:
        return False
    return (time.time() - last_ts) < (COOLDOWN_MINUTES * 60)


# =========================
# MAIN LOOP
# =========================
def bootstrap():
    fetch_exchange_rules()
    reset_day_if_needed()
    tg_send(
        f"🚀 Bot started\n"
        f"Symbols: {', '.join(SYMBOLS)}\n"
        f"Leverage: {LEVERAGE}x\n"
        f"Daily target: +{DAILY_TARGET_USDT} USDT\n"
        f"Daily stop: -{DAILY_MAX_LOSS_USDT} USDT\n"
        f"Capital fraction: {CAPITAL_FRACTION}"
    )


def main():
    bootstrap()

    while True:
        try:
            reset_day_if_needed()

            # hard daily limits
            pnl = get_day_pnl()
            if pnl >= DAILY_TARGET_USDT:
                if STATE.get("open_trade"):
                    close_trade("Daily target reached")
                logging.info("Daily target reached: %.4f", pnl)
                time.sleep(LOOP_SECONDS)
                continue

            if pnl <= -abs(DAILY_MAX_LOSS_USDT):
                if STATE.get("open_trade"):
                    close_trade("Daily max loss reached")
                logging.info("Daily max loss reached: %.4f", pnl)
                time.sleep(LOOP_SECONDS)
                continue

            if STATE.get("consecutive_losses", 0) >= MAX_CONSECUTIVE_LOSSES:
                if STATE.get("open_trade"):
                    close_trade("Max consecutive losses reached")
                logging.info("Consecutive loss limit reached")
                time.sleep(LOOP_SECONDS)
                continue

            # manage current trade first
            pos = get_open_position()
            if STATE.get("open_trade") and pos:
                manage_open_trade()
                time.sleep(LOOP_SECONDS)
                continue

            # if no exchange position but state still says open, clear it
            if STATE.get("open_trade") and not pos:
                STATE["open_trade"] = None
                save_state(STATE)

            if cooldown_active():
                logging.info("Cooldown active")
                time.sleep(LOOP_SECONDS)
                continue

            # no new entries if guard already hit
            if daily_limits_hit():
                time.sleep(LOOP_SECONDS)
                continue

            signal = choose_best_signal()
            if signal:
                logging.info("Best signal: %s", signal)
                enter_trade(signal)
            else:
                logging.info("No valid signal")

        except Exception as e:
            logging.exception("Main loop error: %s", e)
            tg_send(f"⚠️ Bot error: {e}")

        time.sleep(LOOP_SECONDS)


if __name__ == "__main__":
    main()
