"""
PolyInsider — discord_sonar_watcher.py
Monitors insider.db for high-value trades and pushes alerts to Discord via Webhook.
Run in background: screen -dmS discord_watcher python discord_sonar_watcher.py
"""

import sqlite3
import time
import logging
import os
import requests
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# ─── Config ───────────────────────────────────────────────────────────────────
DB_PATH         = Path(__file__).parent / "insider.db"
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "")
CHECK_INTERVAL  = 15       # seconds between DB polls
MIN_SCORE       = 3.0      # minimum score to trigger alert
MIN_USD         = 500      # minimum USD value to alert on
WHALE_USD       = 10_000   # USD threshold for whale alert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [DISCORD] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("discord_watcher")

# ─── State ────────────────────────────────────────────────────────────────────
last_seen_id = 0   # track last alerted trade ID to avoid duplicates

# ─── Discord Payload Builder ──────────────────────────────────────────────────
def build_embed(trade: dict) -> dict:
    usd   = trade["usd_value"]
    price = trade["price"]
    score = trade["score"]
    mkt   = trade["market_name"][:80]
    alert = trade["alert"]
    ts    = trade["timestamp"]

    if usd >= WHALE_USD:
        color  = 0xFF3333   # red — whale
        title  = f"🐳 WHALE ALERT — ${usd:,.0f}"
    elif score >= 3.0:
        color  = 0xFF8C00   # orange — large
        title  = f"🦈 Large Trade — ${usd:,.0f}"
    else:
        color  = 0x00AA88
        title  = f"📊 Trade Alert — ${usd:,.0f}"

    return {
        "embeds": [{
            "title":       title,
            "description": f"**{mkt}**",
            "color":       color,
            "fields": [
                {"name": "💰 USD Value",   "value": f"`${usd:,.2f}`",         "inline": True},
                {"name": "📈 Price",       "value": f"`{price:.4f}`",          "inline": True},
                {"name": "📊 Score",       "value": f"`{score:.1f} / 5.0`",    "inline": True},
                {"name": "🔍 Signal",      "value": alert,                     "inline": False},
                {"name": "⏰ Time (UTC)",  "value": f"`{ts}`",                 "inline": True},
                {"name": "🎯 Outcome",     "value": f"`{trade['outcome']}`",   "inline": True},
            ],
            "footer": {"text": "PolyInsider Terminal • polymarket.com"},
            "timestamp": datetime.utcnow().isoformat(),
        }]
    }

# ─── Discord Sender ───────────────────────────────────────────────────────────
def send_discord_alert(trade: dict):
    if not DISCORD_WEBHOOK:
        log.warning("No DISCORD_WEBHOOK_URL set — skipping alert.")
        return

    payload = build_embed(trade)
    try:
        resp = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        if resp.status_code in (200, 204):
            log.info(f"✅ Alerted: {trade['market_name'][:40]} | ${trade['usd_value']:,.0f}")
        else:
            log.warning(f"Discord returned {resp.status_code}: {resp.text[:200]}")
    except requests.RequestException as e:
        log.error(f"Failed to send Discord alert: {e}")

# ─── DB Poller ────────────────────────────────────────────────────────────────
def poll_new_trades() -> list[dict]:
    global last_seen_id
    if not DB_PATH.exists():
        return []

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.execute("""
        SELECT * FROM trades
        WHERE id > ?
          AND score >= ?
          AND usd_value >= ?
        ORDER BY id ASC
    """, (last_seen_id, MIN_SCORE, MIN_USD))
    rows = [dict(r) for r in cur.fetchall()]
    con.close()

    if rows:
        last_seen_id = rows[-1]["id"]

    return rows

# ─── Summary Digest (every hour) ─────────────────────────────────────────────
last_digest = datetime.utcnow()

def send_hourly_digest():
    if not DISCORD_WEBHOOK:
        return

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    since = (datetime.utcnow() - timedelta(hours=1)).isoformat()

    total_vol  = con.execute("SELECT SUM(usd_value) FROM trades WHERE timestamp >= ?", (since,)).fetchone()[0] or 0
    trade_cnt  = con.execute("SELECT COUNT(*) FROM trades WHERE timestamp >= ?", (since,)).fetchone()[0] or 0
    whale_cnt  = con.execute("SELECT COUNT(*) FROM trades WHERE timestamp >= ? AND usd_value >= ?", (since, WHALE_USD)).fetchone()[0] or 0

    top_markets = con.execute("""
        SELECT market_name, SUM(usd_value) as vol
        FROM trades WHERE timestamp >= ?
        GROUP BY market_name ORDER BY vol DESC LIMIT 3
    """, (since,)).fetchall()
    con.close()

    top_str = "\n".join([f"• {r['market_name'][:50]} — ${r['vol']:,.0f}" for r in top_markets]) or "No data"

    payload = {
        "embeds": [{
            "title":       "📋 Hourly Digest — PolyInsider",
            "color":       0x4444FF,
            "fields": [
                {"name": "💰 1H Volume",      "value": f"`${total_vol:,.0f}`",  "inline": True},
                {"name": "📊 Trades",         "value": f"`{trade_cnt}`",         "inline": True},
                {"name": "🐳 Whale Trades",   "value": f"`{whale_cnt}`",         "inline": True},
                {"name": "🔥 Top Markets",    "value": top_str,                  "inline": False},
            ],
            "footer":    {"text": "PolyInsider Terminal"},
            "timestamp": datetime.utcnow().isoformat(),
        }]
    }
    requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
    log.info("📋 Sent hourly digest")

# ─── Main Loop ────────────────────────────────────────────────────────────────
def main():
    global last_digest

    if not DISCORD_WEBHOOK:
        log.warning("⚠️  DISCORD_WEBHOOK_URL not set in .env — alerts will be logged only.")
    else:
        log.info(f"✅ Webhook configured. Watching {DB_PATH}...")

    # Seed last_seen_id to current max so we don't re-alert old trades
    if DB_PATH.exists():
        con = sqlite3.connect(DB_PATH)
        row = con.execute("SELECT MAX(id) FROM trades").fetchone()
        con.close()
        global last_seen_id
        last_seen_id = row[0] or 0
        log.info(f"Starting from trade ID {last_seen_id}")

    log.info(f"Polling every {CHECK_INTERVAL}s | Min score: {MIN_SCORE} | Min $: {MIN_USD}")

    while True:
        try:
            trades = poll_new_trades()
            for trade in trades:
                log.info(f"🚨 {trade['alert']} | {trade['market_name'][:40]} | ${trade['usd_value']:,.0f}")
                send_discord_alert(trade)

            # Hourly digest
            if (datetime.utcnow() - last_digest).seconds >= 3600:
                send_hourly_digest()
                last_digest = datetime.utcnow()

        except Exception as e:
            log.error(f"Watcher error: {e}")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
