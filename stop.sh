#!/bin/bash
echo "🛑 Stopping PolyInsider..."
screen -S poly_engine  -X quit 2>/dev/null && echo "  stopped: poly_engine"
screen -S poly_alerts  -X quit 2>/dev/null && echo "  stopped: poly_alerts"
screen -S poly_signals -X quit 2>/dev/null && echo "  stopped: poly_signals"
screen -S poly_terminal -X quit 2>/dev/null && echo "  stopped: poly_terminal"
echo "✅ All stopped."
