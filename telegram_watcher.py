"""
PolyInsider — telegram_watcher.py
Monitors insider.db for high-value trades and pushes alerts to Telegram.
"""

import sqlite3, time, logging, os, requests
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

DB_PATH        = Path(__file__).parent / "insider.db"
BOT_TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID        = os.getenv("TELEGRAM_CHAT_ID", "")
CHECK_INTERVAL = 15
MIN_SCORE      = 3.0
MIN_USD        = 500
WHALE_USD      = 10_000
DIGEST_SECS    = 3600

logging.basicConfig(level=logging.INFO, format="%(asctime)s [TG] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("tg_watcher")

last_seen_id = 0
last_digest  = datetime.utcnow()

def send_message(text):
    if not BOT_TOKEN or not CHAT_ID:
        log.warning("Token or chat ID missing")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10
        )
        return r.status_code == 200
    except Exception as e:
        log.error(f"Send error: {e}")
        return False

def build_alert(t):
    usd, price, score = t["usd_value"], t["price"], t["score"]
    if usd >= WHALE_USD:
        hdr, bar = "🐳 <b>WHALE ALERT</b>", "🔴🔴🔴🔴🔴"
    elif usd >= 5000:
        hdr, bar = "🦈 <b>Large Trade</b>", "🟠🟠🟠🟠⬜"
    elif score >= 4.0:
        hdr, bar = "⚡ <b>High Score Trade</b>", "🟡🟡🟡🟡⬜"
    else:
        hdr, bar = "📊 <b>Trade Alert</b>", "🟢🟢🟢⬜⬜"
    stars = "⭐" * min(5, int(score))
    return (
        f"{hdr}\n\n"
        f"📌 <b>{t['market_name'][:80]}</b>\n"
        f"🎯 Outcome: <code>{t.get('outcome','')}</code>\n\n"
        f"💰 Value:  <code>${usd:>10,.2f}</code>\n"
        f"📈 Price:  <code>{price:.4f}</code>  ({price*100:.1f}¢)\n"
        f"🔥 Score:  {stars} <code>{score:.1f}/5</code>  {bar}\n\n"
        f"🔍 <i>{t.get('alert','')}</i>\n\n"
        f"⏰ <code>{str(t.get('timestamp',''))[:19]} UTC</code>"
    )

def build_digest(since_dt):
    if not DB_PATH.exists(): return "No data yet."
    since = since_dt.isoformat()
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    vol   = con.execute("SELECT COALESCE(SUM(usd_value),0) FROM trades WHERE timestamp>=?", (since,)).fetchone()[0]
    cnt   = con.execute("SELECT COUNT(*) FROM trades WHERE timestamp>=?", (since,)).fetchone()[0]
    whal  = con.execute("SELECT COUNT(*) FROM trades WHERE timestamp>=? AND usd_value>=?", (since, WHALE_USD)).fetchone()[0]
    top   = con.execute("SELECT market_name, SUM(usd_value) vol, COUNT(*) cnt FROM trades WHERE timestamp>=? GROUP BY market_name ORDER BY vol DESC LIMIT 5", (since,)).fetchall()
    con.close()
    top_str = "\n".join([f"  {i+1}. {r['market_name'][:45]}\n     └ <code>${r['vol']:,.0f}</code> ({r['cnt']} trades)" for i,r in enumerate(top)]) or "  None"
    return (
        f"📋 <b>PolyInsider Hourly Digest</b>\n"
        f"{'─'*28}\n"
        f"💰 Volume: <code>${vol:>12,.0f}</code>\n"
        f"📊 Trades: <code>{cnt:>12,}</code>\n"
        f"🐳 Whales: <code>{whal:>12,}</code>\n\n"
        f"🔥 <b>Top Markets:</b>\n{top_str}\n\n"
        f"⏰ <code>{datetime.utcnow().strftime('%H:%M UTC')}</code>"
    )

def poll():
    global last_seen_id
    if not DB_PATH.exists(): return []
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM trades WHERE id>? AND score>=? AND usd_value>=? ORDER BY id ASC LIMIT 20",
        (last_seen_id, MIN_SCORE, MIN_USD)
    ).fetchall()]
    con.close()
    if rows: last_seen_id = rows[-1]["id"]
    return rows

def main():
    global last_seen_id, last_digest
    if not BOT_TOKEN or not CHAT_ID:
        log.error("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env"); return
    log.info(f"Watching {DB_PATH} | interval={CHECK_INTERVAL}s | min=${MIN_USD} score>={MIN_SCORE}")
    if DB_PATH.exists():
        con = sqlite3.connect(DB_PATH)
        last_seen_id = con.execute("SELECT COALESCE(MAX(id),0) FROM trades").fetchone()[0]
        con.close(); log.info(f"Seeded from trade ID {last_seen_id}")
    send_message(
        f"🚀 <b>PolyInsider Online</b>\n\n"
        f"⚡ Interval: <code>{CHECK_INTERVAL}s</code>\n"
        f"💰 Min: <code>${MIN_USD:,}</code> | Score: <code>{MIN_SCORE}</code>\n"
        f"🐳 Whale threshold: <code>${WHALE_USD:,}</code>"
    )
    while True:
        try:
            for t in poll():
                log.info(f"🚨 {t['market_name'][:40]} | ${t['usd_value']:,.0f}")
                send_message(build_alert(t))
                time.sleep(0.5)
            if (datetime.utcnow() - last_digest).seconds >= DIGEST_SECS:
                send_message(build_digest(last_digest))
                last_digest = datetime.utcnow()
                log.info("📋 Digest sent")
        except Exception as e:
            log.error(f"Error: {e}")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
