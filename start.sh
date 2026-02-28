#!/bin/bash
# PolyInsider — Full Stack Launcher
cd "$(dirname "$0")"
source venv/bin/activate

echo "🚀 Starting PolyInsider Full Stack..."

# Kill any existing sessions
screen -S poly_engine -X quit 2>/dev/null
screen -S poly_alerts -X quit 2>/dev/null
screen -S poly_signals -X quit 2>/dev/null
screen -S poly_terminal -X quit 2>/dev/null

sleep 1

# 1. Data engine — pulls live trades from Polymarket WebSocket
screen -dmS poly_engine bash -c "python sonar_insider.py 2>&1 | tee logs/engine.log"
echo "✅ Data engine started (screen: poly_engine)"

sleep 2

# 2. Telegram trade alerter — fires on qualifying trades
screen -dmS poly_alerts bash -c "python telegram_watcher.py 2>&1 | tee logs/alerts.log"
echo "✅ Telegram alerter started (screen: poly_alerts)"

sleep 1

# 3. Signal engine — advanced pattern detection
screen -dmS poly_signals bash -c "python signals.py 2>&1 | tee logs/signals.log"
echo "✅ Signal engine started (screen: poly_signals)"

sleep 1

# 4. Streamlit terminal — web dashboard
screen -dmS poly_terminal bash -c "streamlit run terminal.py --server.port 8501 --server.address 0.0.0.0 2>&1 | tee logs/terminal.log"
echo "✅ Streamlit terminal started (screen: poly_terminal)"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🎯 PolyInsider running!"
echo "📊 Terminal: http://$(hostname -I | awk '{print $1}'):8501"
echo "📱 Telegram alerts: active"
echo ""
echo "📺 View logs:"
echo "   screen -r poly_engine"
echo "   screen -r poly_alerts"
echo "   screen -r poly_signals"
echo "   screen -r poly_terminal"
echo ""
echo "🛑 Stop all: bash stop.sh"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
