"""
PolyInsider — terminal.py
Bloomberg-style Streamlit dashboard for live Polymarket trade intelligence.
Run: streamlit run terminal.py
"""

import sqlite3
import time
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

DB_PATH = Path(__file__).parent / "insider.db"

# ─── Page Config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="PolyInsider Terminal",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Dark Terminal Styling ─────────────────────────────────────────────────────
st.markdown("""
<style>
    body, .stApp { background-color: #0a0a0f; color: #e0e0e0; }
    .stMetric { background: #111120; border: 1px solid #222240; border-radius: 8px; padding: 12px; }
    .stMetric label { color: #8888aa !important; font-size: 11px; letter-spacing: 1px; text-transform: uppercase; }
    .stMetric [data-testid="metric-value"] { color: #00ff88 !important; font-family: monospace; font-size: 1.6rem; }
    .whale-row { background: rgba(255, 50, 50, 0.15) !important; }
    div[data-testid="stDataFrame"] { border: 1px solid #1a1a3a; }
    .block-container { padding-top: 1rem; }
    h1, h2, h3 { color: #00ff88; font-family: monospace; }
    .stSidebar { background: #080810; }
    .section-header {
        font-family: monospace;
        font-size: 11px;
        letter-spacing: 2px;
        color: #555577;
        text-transform: uppercase;
        border-bottom: 1px solid #1a1a3a;
        padding-bottom: 4px;
        margin-bottom: 8px;
    }
</style>
""", unsafe_allow_html=True)

# ─── Data Loading ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=5)
def load_trades(hours: int = 24, min_usd: float = 0) -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    con = sqlite3.connect(DB_PATH)
    since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    df = pd.read_sql("""
        SELECT timestamp, market_name, outcome, price, size, usd_value, side, score, alert
        FROM trades
        WHERE timestamp >= ? AND usd_value >= ?
        ORDER BY timestamp DESC
    """, con, params=(since, min_usd))
    con.close()
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df

@st.cache_data(ttl=30)
def load_markets() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    con = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT * FROM markets ORDER BY volume_24h DESC", con)
    con.close()
    return df

# ─── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📡 PolyInsider")
    st.markdown('<div class="section-header">FILTERS</div>', unsafe_allow_html=True)
    hours      = st.slider("Time window (hours)", 1, 72, 24)
    min_usd    = st.number_input("Min trade size ($)", 0, 50000, 0, step=50)
    min_score  = st.slider("Min score filter", 0.0, 5.0, 0.0, 0.5)
    auto_refresh = st.toggle("Auto-refresh (10s)", value=True)
    st.divider()
    st.markdown('<div class="section-header">LEGEND</div>', unsafe_allow_html=True)
    st.markdown("🐳 **Whale** — >$10k")
    st.markdown("🦈 **Large** — >$2k")
    st.markdown("🔥 **Late Sniper** — price ≥85¢")
    st.markdown("💎 **Contrarian** — price ≤15¢")

# ─── Header ────────────────────────────────────────────────────────────────────
st.markdown("# 📡 POLYINSIDER TERMINAL")
st.markdown(f"`{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}` — Live Prediction Market Intelligence")

# ─── Load Data ────────────────────────────────────────────────────────────────
df = load_trades(hours=hours, min_usd=min_usd)
if not df.empty and min_score > 0:
    df = df[df["score"] >= min_score]

# ─── Top Metrics ──────────────────────────────────────────────────────────────
st.divider()
col1, col2, col3, col4, col5 = st.columns(5)

total_vol    = df["usd_value"].sum() if not df.empty else 0
trade_count  = len(df)
whale_count  = len(df[df["score"] >= 5.0]) if not df.empty else 0
avg_price    = df["price"].mean() if not df.empty else 0
top_market   = df["market_name"].value_counts().index[0][:30] if not df.empty else "—"

col1.metric("💰 Total Volume",   f"${total_vol:,.0f}")
col2.metric("📊 Trades",         f"{trade_count:,}")
col3.metric("🐳 Whale Trades",   f"{whale_count}")
col4.metric("📈 Avg Price",      f"{avg_price:.2f}")
col5.metric("🔥 Top Market",     top_market)

# ─── Live Wire ────────────────────────────────────────────────────────────────
st.divider()
st.markdown("### ⚡ LIVE WIRE — Recent Trades")

if df.empty:
    st.info("⏳ No trades yet. Make sure `sonar_insider.py` is running.")
else:
    wire = df.head(50).copy()
    wire["time"]   = wire["timestamp"].dt.strftime("%H:%M:%S")
    wire["$value"] = wire["usd_value"].apply(lambda x: f"${x:,.0f}")
    wire["price"]  = wire["price"].apply(lambda x: f"{x:.3f}")

    def tag(row):
        if row["score"] >= 5.0: return f"🐳 {row['alert']}"
        if row["score"] >= 3.0: return f"🦈 {row['alert']}"
        return row["alert"]
    wire["signal"] = wire.apply(tag, axis=1)

    st.dataframe(
        wire[["time", "market_name", "outcome", "price", "$value", "side", "signal"]],
        use_container_width=True,
        hide_index=True,
        column_config={
            "time":        st.column_config.TextColumn("TIME", width=80),
            "market_name": st.column_config.TextColumn("MARKET", width=300),
            "outcome":     st.column_config.TextColumn("SIDE", width=60),
            "price":       st.column_config.TextColumn("PRICE", width=70),
            "$value":      st.column_config.TextColumn("VALUE", width=90),
            "side":        st.column_config.TextColumn("BUY/SELL", width=80),
            "signal":      st.column_config.TextColumn("SIGNAL", width=280),
        }
    )

# ─── Charts Row ───────────────────────────────────────────────────────────────
st.divider()
col_a, col_b = st.columns(2)

with col_a:
    st.markdown("### 📊 Volume by Market")
    if not df.empty:
        top10 = df.groupby("market_name")["usd_value"].sum().nlargest(10).reset_index()
        top10["market_name"] = top10["market_name"].str[:40]
        fig = px.bar(top10, x="usd_value", y="market_name", orientation="h",
                     color="usd_value", color_continuous_scale="Teal",
                     labels={"usd_value": "USD Volume", "market_name": ""},
                     template="plotly_dark")
        fig.update_layout(showlegend=False, plot_bgcolor="#0a0a0f", paper_bgcolor="#0a0a0f",
                          margin=dict(l=0, r=0, t=0, b=0), height=300)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Waiting for data...")

with col_b:
    st.markdown("### ⏱ Trade Volume Over Time")
    if not df.empty and len(df) > 1:
        ts = df.set_index("timestamp").resample("5min")["usd_value"].sum().reset_index()
        fig2 = px.area(ts, x="timestamp", y="usd_value",
                       labels={"usd_value": "USD", "timestamp": ""},
                       template="plotly_dark", color_discrete_sequence=["#00ff88"])
        fig2.update_layout(plot_bgcolor="#0a0a0f", paper_bgcolor="#0a0a0f",
                           margin=dict(l=0, r=0, t=0, b=0), height=300)
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("Waiting for data...")

# ─── Whale Alert Feed ─────────────────────────────────────────────────────────
st.divider()
st.markdown("### 🐳 WHALE ALERT FEED")

whales = df[df["score"] >= 3.0].head(20) if not df.empty else pd.DataFrame()
if whales.empty:
    st.info("No high-score trades yet.")
else:
    for _, row in whales.iterrows():
        emoji = "🐳" if row["score"] >= 5 else "🦈"
        ts    = row["timestamp"].strftime("%H:%M:%S")
        st.markdown(
            f"`{ts}` {emoji} **{row['market_name'][:50]}** — "
            f"`{row['outcome']}` @ **{row['price']:.3f}** — "
            f"**${row['usd_value']:,.0f}** — _{row['alert']}_"
        )

# ─── Markets Table ────────────────────────────────────────────────────────────
st.divider()
st.markdown("### 🎯 Tracked Markets")
markets_df = load_markets()
if not markets_df.empty:
    markets_df["volume_24h"] = markets_df["volume_24h"].apply(lambda x: f"${x:,.0f}")
    st.dataframe(markets_df[["name", "volume_24h", "last_seen"]],
                 use_container_width=True, hide_index=True)

# ─── Auto Refresh ─────────────────────────────────────────────────────────────
if auto_refresh:
    time.sleep(10)
    st.rerun()
