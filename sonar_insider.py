"""
PolyInsider — sonar_insider.py
Live trade ingestion from Polymarket CLOB WebSocket.
"""

import asyncio, json, sqlite3, logging
from datetime import datetime
from pathlib import Path
import aiohttp, websockets

WS_URL         = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA_API      = "https://gamma-api.polymarket.com/markets"
DB_PATH        = Path(__file__).parent / "insider.db"
MIN_USD        = 5.0
TOP_N_MARKETS  = 30
RECONNECT_DELAY = 5

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SONAR] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("sonar")

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, market_id TEXT, market_name TEXT,
        outcome TEXT, price REAL, size REAL, usd_value REAL,
        side TEXT, score REAL, alert TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS markets (
        token_id TEXT PRIMARY KEY, name TEXT, question TEXT,
        volume_24h REAL, last_seen TEXT)""")
    con.commit(); con.close()
    log.info(f"DB ready at {DB_PATH}")

def insert_trade(t):
    con = sqlite3.connect(DB_PATH)
    con.execute("""INSERT INTO trades
        (timestamp,market_id,market_name,outcome,price,size,usd_value,side,score,alert)
        VALUES (:timestamp,:market_id,:market_name,:outcome,:price,:size,:usd_value,:side,:score,:alert)""", t)
    con.commit(); con.close()

def upsert_market(token_id, name, volume):
    con = sqlite3.connect(DB_PATH)
    con.execute("""INSERT INTO markets (token_id,name,question,volume_24h,last_seen)
        VALUES (?,?,?,?,?)
        ON CONFLICT(token_id) DO UPDATE SET
        name=excluded.name, volume_24h=excluded.volume_24h, last_seen=excluded.last_seen""",
        (token_id, name[:80], name, volume, datetime.utcnow().isoformat()))
    con.commit(); con.close()

async def fetch_markets():
    log.info("Fetching top markets from Gamma API...")
    params = {"limit":50,"order":"volume24hr","ascending":"false","active":"true","closed":"false"}
    async with aiohttp.ClientSession() as s:
        async with s.get(GAMMA_API, params=params) as r:
            data = await r.json()

    token_ids = []
    name_map  = {}
    count = 0
    for m in data:
        if count >= TOP_N_MARKETS: break
        question = m.get("question","?")
        volume   = float(m.get("volume24hr") or 0)
        # clobTokenIds comes as a JSON string — parse it
        raw = m.get("clobTokenIds", "[]")
        tokens = json.loads(raw) if isinstance(raw, str) else raw
        if not tokens: continue
        for t in tokens[:2]:  # YES + NO only
            tid = str(t)
            token_ids.append(tid)
            name_map[tid] = question
            upsert_market(tid, question, volume)
        count += 1
        log.info(f"  #{count:02d} ${volume:>12,.0f} | {question[:55]}")

    log.info(f"Tracking {len(token_ids)} tokens across {count} markets")
    return token_ids, name_map

def score_trade(usd, price):
    score, reasons = 0.0, []
    if usd >= 25000: score += 5.0; reasons.append("🐋 MEGA WHALE (>$25k)")
    elif usd >= 10000: score += 4.0; reasons.append("🐳 WHALE (>$10k)")
    elif usd >= 2000:  score += 2.5; reasons.append("🦈 Large (>$2k)")
    elif usd >= 500:   score += 1.5; reasons.append("📊 Mid (>$500)")
    else: score += 0.5
    if price >= 0.85:   score += 2.0; reasons.append("🔥 Late sniper (≥85¢)")
    elif price <= 0.15: score += 1.5; reasons.append("💎 Contrarian (≤15¢)")
    elif 0.45 <= price <= 0.55: score += 0.5; reasons.append("⚖️ Near 50/50")
    return round(score, 2), " | ".join(reasons) or "Standard trade"

async def run():
    token_ids, name_map = await fetch_markets()
    sub = json.dumps({"type":"market","assets_ids": token_ids})

    while True:
        try:
            log.info(f"Connecting to WebSocket ({len(token_ids)} tokens)...")
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=30) as ws:
                await ws.send(sub)
                log.info("✅ Subscribed. Listening for trades...")
                async for raw in ws:
                    if raw == "ping": await ws.send("pong"); continue
                    try:
                        events = json.loads(raw)
                        if not isinstance(events, list): events = [events]
                        for ev in events:
                            etype = ev.get("event_type") or ev.get("type","")
                            # Handle orderbook price change events
                            asset_id = ev.get("asset_id","")
                            market_name = name_map.get(asset_id, asset_id[:16])

                            # trade events
                            if etype in ("trade","TRADE"):
                                price = float(ev.get("price",0))
                                size  = float(ev.get("size",0))
                                side  = ev.get("side","UNKNOWN")
                                usd   = price * size
                                if usd < MIN_USD or price <= 0: continue
                                score, alert = score_trade(usd, price)
                                t = dict(timestamp=datetime.utcnow().isoformat(),
                                         market_id=asset_id, market_name=market_name,
                                         outcome="YES" if price > 0.5 else "NO",
                                         price=price, size=size, usd_value=round(usd,2),
                                         side=side, score=score, alert=alert)
                                insert_trade(t)
                                if score >= 3.0:
                                    log.info(f"🚨 {alert} | {market_name[:40]} | ${usd:,.0f}")

                            # price_change gives us last trade info
                            elif etype in ("price_change","last_trade_price") or "price" in ev:
                                price = float(ev.get("price") or ev.get("last_trade_price") or 0)
                                size  = float(ev.get("size") or ev.get("amount") or 0)
                                if price <= 0 or size <= 0: continue
                                side  = ev.get("side","BUY")
                                usd   = price * size
                                if usd < MIN_USD: continue
                                score, alert = score_trade(usd, price)
                                t = dict(timestamp=datetime.utcnow().isoformat(),
                                         market_id=asset_id, market_name=market_name,
                                         outcome="YES" if price > 0.5 else "NO",
                                         price=price, size=size, usd_value=round(usd,2),
                                         side=side, score=score, alert=alert)
                                insert_trade(t)
                                if score >= 3.0:
                                    log.info(f"🚨 {alert} | {market_name[:40]} | ${usd:,.0f}")

                    except (json.JSONDecodeError, ValueError, KeyError):
                        continue

        except (websockets.ConnectionClosed, OSError) as e:
            log.warning(f"Disconnected: {e} — reconnecting in {RECONNECT_DELAY}s")
            await asyncio.sleep(RECONNECT_DELAY)
        except Exception as e:
            log.error(f"Error: {e} — reconnecting in {RECONNECT_DELAY}s")
            await asyncio.sleep(RECONNECT_DELAY)

if __name__ == "__main__":
    init_db()
    log.info("🚀 PolyInsider Sonar starting...")
    asyncio.run(run())
