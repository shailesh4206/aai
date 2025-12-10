import os
import time
import hmac
import hashlib
import json
import threading
import sqlite3
from datetime import datetime, timedelta

import requests
import pandas as pd
from flask import Flask, jsonify, request, render_template, send_from_directory

app = Flask(__name__)

# =========================
# CONFIG (SECURE: use env vars)
# =========================
DELTA_BASE_URL = os.getenv("DELTA_BASE_URL", "https://api.delta.exchange")
DELTA_API_KEY = os.getenv("DELTA_API_KEY", "paPUtjUgWZexpXhue0esNzG6M8hD5l")
DELTA_SECRET = os.getenv("DELTA_API_SECRET", "xZwU5BA2Vs2aV58VWYHNMEKkGmdZxQXH9V7wbiu8KkpbqiZ8XI2R3dMMpcii")

# trading params (can be tuned from UI or env)
RISK_PERCENT = float(os.getenv("DELTA_RISK_PERCENT", "1"))   # 1% per trade
MIN_BALANCE_TO_TRADE = float(os.getenv("MIN_BALANCE_TO_TRADE", "250"))  # ₹
STOP_LOSS_PERCENT = float(os.getenv("STOP_LOSS_PERCENT", "1"))  # 1%
TARGET_PERCENT = float(os.getenv("TARGET_PERCENT", "2"))        # 2%

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DB_PATH = os.path.join(DATA_DIR, "trades.db")
os.makedirs(DATA_DIR, exist_ok=True)

# --- Multi-coin product-id mapping (तू तुझ्या Delta वरून बरोबर ids ठेवल्यास 100% work होईल) ---
COIN_CATALOG = {
    "ETHUSD": "3136",   # तुझ्या दिलेल्या/verified ID नुसार ठेवा
    "BTCUSD": "27",      # placeholder (तुझ्या Delta वरून verify करून बदला)
}

# =========================
# DB helpers (store executed trades)
# =========================
def db_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_utc TEXT NOT NULL,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        entry REAL NOT NULL,
        exit REAL NOT NULL,
        qty REAL NOT NULL,
        pnl REAL NOT NULL
    );
    """)
    conn.commit()
    conn.close()

init_db()

# =========================
# Delta auth + http helpers
# =========================
def _signature(method: str, endpoint: str, body: str, timestamp: str) -> str:
    message = (method + timestamp + endpoint + body).encode("utf-8")
    return hmac.new(DELTA_SECRET.encode("utf-8"), message, hashlib.sha256).hexdigest()

def delta_post(endpoint: str, payload: dict):
    url = DELTA_BASE_URL + endpoint
    body = json.dumps(payload, separators=(",", ":"))
    timestamp = str(int(time.time() * 1000))
    headers = {
        "api-key": DELTA_API_KEY,
        "signature": _signature("POST", endpoint, body, timestamp),
        "timestamp": timestamp,
        "Content-Type": "application/json"
    }
    r = requests.post(url, headers=headers, data=body, timeout=20)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"raw": r.text}

def delta_get(url: str):
    r = requests.get(url, timeout=12)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {}

# =========================
# Market data + strategy (EMA 9/15)
# =========================
def get_recent_candles(product_id: str, interval: str, limit: int = 60):
    url = f"{DELTA_BASE_URL}/v2/candles?symbol={product_id}&resolution={interval}&limit={limit}"
    code, data = delta_get(url)
    if code != 200:
        return None
    items = data.get("result", [])
    if not items:
        return None
    df = pd.DataFrame(items)
    df["close"] = df["close"].astype(float)
    return df

def generate_signal(df: pd.DataFrame) -> str:
    df["EMA9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["EMA15"] = df["close"].ewm(span=15, adjust=False).mean()
    prev_ema9, prev_ema15 = df["EMA9"].iloc[-2], df["EMA15"].iloc[-2]
    last_ema9, last_ema15 = df["EMA9"].iloc[-1], df["EMA15"].iloc[-1]
    if prev_ema9 < prev_ema15 and last_ema9 > last_ema15:
        return "BUY"
    if prev_ema9 > prev_ema15 and last_ema9 < last_ema15:
        return "SELL"
    return "HOLD"

def calc_quantity(account_balance: float, entry: float, stop: float) -> float:
    price_diff = abs(entry - stop) or (entry * 0.01)
    risk_amount = account_balance * (RISK_PERCENT / 100.0)
    qty = risk_amount / price_diff
    return max(round(qty, 6), 0.000001)

# =========================
# Trading engine (start/stop + multi-coin)
# =========================
class Engine:
    def __init__(self):
        self.running = False
        self.thread = None
        self.config = {
            "symbols": ["ETHUSD"],
            "interval": "5m",
            "account_balance": 300.0
        }

    def start(self, symbols, interval, account_balance):
        self.config["symbols"] = symbols or ["ETHUSD"]
        self.config["interval"] = interval or "5m"
        self.config["account_balance"] = float(account_balance or self.config["account_balance"])
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    def _save_trade(self, symbol, side, entry, exit_p, qty, pnl):
        conn = db_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO trades (ts_utc, symbol, side, entry, exit, qty, pnl) VALUES (?,?,?,?,?,?,?)",
            (datetime.utcnow().isoformat(), symbol, side, entry, exit_p, qty, pnl)
        )
        conn.commit()
        conn.close()

    def _loop(self):
        while self.running:
            bal = self.config["account_balance"]
            if bal < MIN_BALANCE_TO_TRADE:
                time.sleep(6)
                continue

            for sym in self.config["symbols"]:
                product_id = COIN_CATALOG.get(sym)
                if not product_id:
                    continue

                df = get_recent_candles(product_id, interval=self.config["interval"])
                if df is None:
                    continue

                signal = generate_signal(df)
                if signal == "HOLD":
                    continue

                current = float(df["close"].iloc[-1])
                stop = current * (1 - STOP_LOSS_PERCENT / 100.0) if signal == "BUY" else current * (1 + STOP_LOSS_PERCENT / 100.0)
                target = current * (1 + TARGET_PERCENT / 100.0) if signal == "BUY" else current * (1 - TARGET_PERCENT / 100.0)
                qty = calc_quantity(bal, current, stop)

                # entry order (market)
                status, resp = delta_post("/v2/orders", {
                    "product_id": int(product_id),
                    "size": qty,
                    "side": signal.lower(),
                    "order_type": "market"
                })
                if status != 200:
                    continue

                # simple exit logic: poll until hit stop or target (limited attempts)
                side_close = "sell" if signal == "BUY" else "buy"
                entry_price = current
                exit_price = None
                for _ in range(24):   # ~24 checks
                    time.sleep(5)
                    df2 = get_recent_candles(product_id, interval=self.config["interval"])
                    if df2 is None:
                        continue
                    last = float(df2["close"].iloc[-1])
                    if signal == "BUY" and (last <= stop or last >= target):
                        exit_price = last
                        break
                    if signal == "SELL" and (last >= stop or last <= target):
                        exit_price = last
                        break

                if exit_price is None:
                    df2 = get_recent_candles(product_id, interval=self.config["interval"])
                    exit_price = float(df2["close"].iloc[-1]) if df2 is not None else entry_price

                # close order
                delta_post("/v2/orders", {
                    "product_id": int(product_id),
                    "size": qty,
                    "side": side_close,
                    "order_type": "market"
                })

                pnl = (exit_price - entry_price) * qty if signal == "BUY" else (entry_price - exit_price) * qty
                self._save_trade(sym, signal, entry_price, exit_price, qty, pnl)

            time.sleep(6)

engine = Engine()

# =========================
# Web UI + APIs (for your mobile browser)
# =========================
@app.get("/")
def index():
    return render_template("index.html")

@app.get("/static/<path:path>")
def static_files(path):
    return send_from_directory("static", path)

@app.post("/api/start")
def api_start():
    data = request.get_json(silent=True) or {}
    symbols = data.get("symbols") or ["ETHUSD"]
    interval = data.get("interval") or "5m"
    balance = float(data.get("account_balance", engine.config["account_balance"]))
    engine.start(symbols, interval, balance)
    return jsonify({"ok": True, "running": True, "symbols": symbols, "interval": interval})

@app.post("/api/stop")
def api_stop():
    engine.stop()
    return jsonify({"ok": True, "running": False})

@app.get("/api/status")
def api_status():
    return jsonify({
        "running": engine.running,
        "symbols": engine.config["symbols"],
        "interval": engine.config["interval"],
        "account_balance": engine.config["account_balance"]
    })

@app.get("/api/stats")
def api_stats():
    conn = db_conn()
    cur = conn.cursor()
    since = (datetime.utcnow() - timedelta(days=7)).isoformat()
    cur.execute("SELECT ts_utc, symbol, side, entry, exit, qty, pnl FROM trades WHERE ts_utc >= ?", (since,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    total_pnl = sum(r["pnl"] for r in rows)
    total_trades = len(rows)
    wins = sum(1 for r in rows if r["pnl"] > 0)
    loss = sum(1 for r in rows if r["pnl"] <= 0)

    return jsonify({
        "period": "last_7_days",
        "total_pnl": total_pnl,
        "total_trades": total_trades,
        "wins": wins,
        "losses": loss,
        "rows": rows
    })

@app.get("/api/trades")
def api_trades():
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT ts_utc, symbol, side, entry, exit, qty, pnl FROM trades ORDER BY id DESC LIMIT 40")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify({"rows": rows})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
