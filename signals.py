"""
PolyInsider — signals.py
Advanced signal detection engine. Runs on top of insider.db.
Detects: repeated buys, sudden odds shifts, price velocity spikes, coordinated entries.
"""

import sqlite3
import time
import logging
import os
import requests
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

DB_PATH   = Path(__file__).parent / "insider.db"
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
INTERVAL  = 60   # run signal scan every 60 seconds

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SIGNAL] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("signals")

# ── already-alerted signals to avoid spam ─────────────────────────────────────
alerted = set()

def send(text):
    if not BOT_TOKEN or not CHAT_ID: return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10
        )
    except Exception as e:
        log.error(f"Telegram error: {e}")

def query(sql, params=()):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(sql, params).fetchall()]
    con.close()
    return rows

# ── Signal 1: Repeated buys on same market in short window ────────────────────
def detect_accumulation():
    since = (datetime.utcnow() - timedelta(minutes=15)).isoformat()
    rows = query("""
        SELECT market_name, outcome, COUNT(*) as cnt, SUM(usd_value) as total,
               AVG(price) as avg_price, MAX(price) as max_price, MIN(price) as min_price
        FROM trades
        WHERE timestamp >= ? AND side = 'BUY' AND usd_value >= 200
        GROUP BY market_name, outcome
        HAVING cnt >= 3
        ORDER BY total DESC
    """, (since,))
    for r in rows:
        key = f"accum_{r['market_name']}_{r['outcome']}_{since[:13]}"
        if key in alerted: continue
        alerted.add(key)
        price_range = f"{r['min_price']:.3f} → {r['max_price']:.3f}"
        msg = (
            f"📈 <b>ACCUMULATION DETECTED</b>\n\n"
            f"📌 <b>{r['market_name'][:80]}</b>\n"
            f"🎯 Side: <code>{r['outcome']}</code>\n\n"
            f"🔄 Buys in 15min: <code>{r['cnt']}</code>\n"
            f"💰 Total: <code>${r['total']:,.0f}</code>\n"
            f"📈 Avg price: <code>{r['avg_price']:.3f}</code>\n"
            f"📊 Range: <code>{price_range}</code>\n\n"
            f"⚠️ <i>Someone is repeatedly buying this position</i>"
        )
        send(msg)
        log.info(f"📈 Accumulation: {r['market_name'][:40]} {r['outcome']} x{r['cnt']} ${r['total']:,.0f}")

# ── Signal 2: Sudden price velocity spike ─────────────────────────────────────
def detect_price_velocity():
    since_15 = (datetime.utcnow() - timedelta(minutes=15)).isoformat()
    since_60 = (datetime.utcnow() - timedelta(minutes=60)).isoformat()

    markets = query("SELECT DISTINCT market_name FROM trades WHERE timestamp >= ?", (since_15,))
    for m in markets:
        name = m["market_name"]
        recent = query("""
            SELECT AVG(price) as avg_p FROM trades
            WHERE market_name=? AND timestamp >= ?
        """, (name, since_15))
        older = query("""
            SELECT AVG(price) as avg_p FROM trades
            WHERE market_name=? AND timestamp >= ? AND timestamp < ?
        """, (name, since_60, since_15))
        if not recent or not older: continue
        r_p = recent[0]["avg_p"]
        o_p = older[0]["avg_p"]
        if not r_p or not o_p or o_p == 0: continue
        move = (r_p - o_p) / o_p
        if abs(move) >= 0.10:  # 10%+ price move in 15min
            key = f"velocity_{name}_{since_15[:15]}"
            if key in alerted: continue
            alerted.add(key)
            direction = "🚀 SURGING" if move > 0 else "💥 CRASHING"
            msg = (
                f"⚡ <b>PRICE VELOCITY SPIKE</b>\n\n"
                f"📌 <b>{name[:80]}</b>\n\n"
                f"{direction}\n"
                f"📊 Was: <code>{o_p:.3f}</code> ({o_p*100:.1f}¢)\n"
                f"📊 Now: <code>{r_p:.3f}</code> ({r_p*100:.1f}¢)\n"
                f"📈 Move: <code>{move*100:+.1f}%</code> in 15min\n\n"
                f"⚠️ <i>Significant price movement — check for news</i>"
            )
            send(msg)
            log.info(f"⚡ Velocity: {name[:40]} {move*100:+.1f}%")

# ── Signal 3: Single massive trade ────────────────────────────────────────────
def detect_single_whale():
    since = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
    rows = query("""
        SELECT * FROM trades
        WHERE timestamp >= ? AND usd_value >= 25000
        ORDER BY usd_value DESC LIMIT 5
    """, (since,))
    for r in rows:
        key = f"bigwhale_{r['id']}"
        if key in alerted: continue
        alerted.add(key)
        msg = (
            f"🐋 <b>MEGA WHALE DETECTED</b>\n\n"
            f"📌 <b>{r['market_name'][:80]}</b>\n"
            f"🎯 <code>{r['outcome']}</code> @ <code>{r['price']:.4f}</code>\n\n"
            f"💰 <b>${r['usd_value']:,.0f}</b>\n"
            f"📊 Size: <code>{r['size']:,.0f} shares</code>\n\n"
            f"🚨 <i>Single trade over $25,000 — major position taken</i>"
        )
        send(msg)
        log.info(f"🐋 Mega whale: {r['market_name'][:40]} ${r['usd_value']:,.0f}")

# ── Signal 4: Market approaching resolution (price near 0 or 1) ───────────────
def detect_near_resolution():
    since = (datetime.utcnow() - timedelta(minutes=10)).isoformat()
    rows = query("""
        SELECT market_name, outcome, AVG(price) as avg_p, SUM(usd_value) as vol
        FROM trades
        WHERE timestamp >= ? AND (price >= 0.93 OR price <= 0.07)
        GROUP BY market_name, outcome
        HAVING vol >= 1000
        ORDER BY vol DESC LIMIT 5
    """, (since,))
    for r in rows:
        key = f"nearres_{r['market_name']}_{since[:13]}"
        if key in alerted: continue
        alerted.add(key)
        p = r["avg_p"]
        likely = "YES" if p > 0.5 else "NO"
        conf = p if p > 0.5 else 1 - p
        msg = (
            f"🎯 <b>NEAR RESOLUTION</b>\n\n"
            f"📌 <b>{r['market_name'][:80]}</b>\n"
            f"🏁 Market pricing <b>{likely}</b> at <code>{conf*100:.0f}%</code> confidence\n\n"
            f"💰 Volume (10min): <code>${r['vol']:,.0f}</code>\n"
            f"📊 Avg price: <code>{p:.4f}</code>\n\n"
            f"⚠️ <i>Market near resolution — insider confidence signal</i>"
        )
        send(msg)
        log.info(f"🎯 Near resolution: {r['market_name'][:40]} {p:.3f}")

# ── Signal 5: Coordinated multi-market activity ────────────────────────────────
def detect_broad_activity():
    since = (datetime.utcnow() - timedelta(minutes=10)).isoformat()
    rows = query("""
        SELECT COUNT(DISTINCT market_name) as mkt_count, SUM(usd_value) as total
        FROM trades WHERE timestamp >= ? AND usd_value >= 1000
    """, (since,))
    if rows and rows[0]["mkt_count"] >= 8 and rows[0]["total"] >= 50000:
        key = f"broad_{since[:15]}"
        if key not in alerted:
            alerted.add(key)
            msg = (
                f"🌊 <b>BROAD MARKET SURGE</b>\n\n"
                f"🔥 <code>{rows[0]['mkt_count']}</code> markets active simultaneously\n"
                f"💰 Total flow (10min): <code>${rows[0]['total']:,.0f}</code>\n\n"
                f"⚠️ <i>Unusual broad activity — possible macro event or coordinated trading</i>"
            )
            send(msg)
            log.info(f"🌊 Broad surge: {rows[0]['mkt_count']} markets ${rows[0]['total']:,.0f}")

# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    if not BOT_TOKEN or not CHAT_ID:
        log.error("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env"); return
    if not DB_PATH.exists():
        log.warning(f"DB not found at {DB_PATH} — waiting for sonar_insider.py to create it...")

    log.info(f"Signal engine running | scan every {INTERVAL}s")
    send("🧠 <b>PolyInsider Signal Engine Online</b>\n\nScanning for: accumulation, price velocity, mega whales, near-resolution, broad surges.")

    while True:
        try:
            if DB_PATH.exists():
                detect_accumulation()
                detect_price_velocity()
                detect_single_whale()
                detect_near_resolution()
                detect_broad_activity()
        except Exception as e:
            log.error(f"Signal scan error: {e}")
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
