"""
PolyInsider — sonar_insider.py
Data Engine: Live trade ingestion from Polymarket CLOB via WebSocket.
Fetches top 20 markets by 24h volume, subscribes, filters, stores to SQLite.
"""

import asyncio
import json
import sqlite3
import logging
import time
from datetime import datetime
from pathlib import Path

import aiohttp
import websockets

# ─── Config ───────────────────────────────────────────────────────────────────
WS_URL       = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA_API    = "https://gamma-api.polymarket.com/markets"
DB_PATH      = Path(__file__).parent / "insider.db"
MIN_TRADE_USD = 5.0       # filter noise below this
TOP_N_MARKETS = 20        # how many markets to track
RECONNECT_DELAY = 5       # seconds before reconnect on drop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sonar")

# ─── Database Setup ────────────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            market_id   TEXT NOT NULL,
            market_name TEXT,
            outcome     TEXT,
            price       REAL,
            size        REAL,
            usd_value   REAL,
            side        TEXT,
            score       REAL,
            alert       TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS markets (
            token_id    TEXT PRIMARY KEY,
            name        TEXT,
            question    TEXT,
            volume_24h  REAL,
            last_seen   TEXT
        )
    """)
    con.commit()
    con.close()
    log.info(f"Database ready at {DB_PATH}")

def insert_trade(trade: dict):
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        INSERT INTO trades (timestamp, market_id, market_name, outcome, price, size, usd_value, side, score, alert)
        VALUES (:timestamp, :market_id, :market_name, :outcome, :price, :size, :usd_value, :side, :score, :alert)
    """, trade)
    con.commit()
    con.close()

def upsert_market(token_id: str, name: str, question: str, volume: float):
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        INSERT INTO markets (token_id, name, question, volume_24h, last_seen)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(token_id) DO UPDATE SET
            name=excluded.name,
            volume_24h=excluded.volume_24h,
            last_seen=excluded.last_seen
    """, (token_id, name, question, volume, datetime.utcnow().isoformat()))
    con.commit()
    con.close()

# ─── Market Discovery ─────────────────────────────────────────────────────────
async def fetch_top_markets() -> tuple[list[str], dict[str, str]]:
    """Query Gamma API for top N markets by 24h volume. Returns token IDs + name map."""
    log.info("Fetching top markets from Gamma API...")
    params = {
        "limit": 50,
        "order": "volume24hr",
        "ascending": "false",
        "active": "true",
        "closed": "false",
    }
    token_ids = []
    name_map = {}

    async with aiohttp.ClientSession() as session:
        async with session.get(GAMMA_API, params=params) as resp:
            markets = await resp.json()

    for m in markets[:TOP_N_MARKETS]:
        question = m.get("question", "Unknown Market")
        volume = float(m.get("volume24hr") or m.get("volume") or 0)
        for token in m.get("clobTokenIds", []):
            token_ids.append(token)
            name_map[token] = question
            upsert_market(token, question[:80], question, volume)

    log.info(f"Tracking {len(token_ids)} tokens across {TOP_N_MARKETS} markets")
    return token_ids, name_map

# ─── Trade Scoring ────────────────────────────────────────────────────────────
def score_trade(usd_value: float, price: float) -> tuple[float, str]:
    """Score a trade and generate alert text based on size + position."""
    score = 0.0
    reasons = []

    if usd_value >= 10_000:
        score += 5.0
        reasons.append("🐳 WHALE (>$10k)")
    elif usd_value >= 2_000:
        score += 3.0
        reasons.append("🦈 Large trade (>$2k)")
    elif usd_value >= 500:
        score += 1.5
        reasons.append("📊 Mid trade (>$500)")
    else:
        score += 0.5

    if price >= 0.85:
        score += 2.0
        reasons.append("🔥 Late-stage sniper (price ≥85¢)")
    elif price <= 0.15:
        score += 1.5
        reasons.append("💎 Low-prob contrarian (price ≤15¢)")

    return round(score, 2), " | ".join(reasons) if reasons else "Standard trade"

# ─── WebSocket Handler ────────────────────────────────────────────────────────
async def run_sonar():
    token_ids, name_map = await fetch_top_markets()

    subscribe_payload = json.dumps({
        "type": "market",
        "assets_ids": token_ids,
    })

    while True:
        try:
            log.info(f"Connecting to {WS_URL}...")
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=30) as ws:
                await ws.send(subscribe_payload)
                log.info("Subscribed. Listening for trades... 🎯")

                async for raw in ws:
                    # Handle server pings
                    if raw == "ping":
                        await ws.send("pong")
                        continue

                    try:
                        events = json.loads(raw)
                        if not isinstance(events, list):
                            events = [events]

                        for event in events:
                            etype = event.get("event_type") or event.get("type", "")

                            if etype in ("price_change", "last_trade_price", "trade"):
                                market_id = event.get("asset_id") or event.get("market", "")
                                price     = float(event.get("price") or event.get("last_trade_price") or 0)
                                size      = float(event.get("size") or event.get("amount") or 0)
                                side      = event.get("side", "UNKNOWN")
                                usd_value = price * size

                                if usd_value < MIN_TRADE_USD or price <= 0:
                                    continue

                                market_name = name_map.get(market_id, market_id[:16])
                                score, alert = score_trade(usd_value, price)

                                trade = {
                                    "timestamp":   datetime.utcnow().isoformat(),
                                    "market_id":   market_id,
                                    "market_name": market_name,
                                    "outcome":     "YES" if price > 0.5 else "NO",
                                    "price":       price,
                                    "size":        size,
                                    "usd_value":   round(usd_value, 2),
                                    "side":        side,
                                    "score":       score,
                                    "alert":       alert,
                                }
                                insert_trade(trade)

                                if score >= 3.0:
                                    log.info(f"🚨 {alert} | {market_name[:40]} | ${usd_value:,.0f} @ {price:.2f}")
                                else:
                                    log.debug(f"Trade: {market_name[:40]} | ${usd_value:.0f} @ {price:.2f}")

                    except (json.JSONDecodeError, ValueError, KeyError):
                        continue

        except (websockets.ConnectionClosed, ConnectionResetError, OSError) as e:
            log.warning(f"Connection dropped: {e}. Reconnecting in {RECONNECT_DELAY}s...")
            await asyncio.sleep(RECONNECT_DELAY)
        except Exception as e:
            log.error(f"Unexpected error: {e}. Reconnecting in {RECONNECT_DELAY}s...")
            await asyncio.sleep(RECONNECT_DELAY)

# ─── Entry Point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    log.info("🚀 PolyInsider Sonar starting...")
    asyncio.run(run_sonar())
