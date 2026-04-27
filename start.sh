#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# SwingQuant — Lance les deux serveurs en parallèle
#   • FastAPI backend (port 8000) — /home/swing/swingquant/backend
#   • Vite React frontend (port 5173) — /home/swing/swingquant/frontend
#
# Usage : bash /home/swing/start.sh
# ─────────────────────────────────────────────────────────────────
set -e

BACKEND_DIR="/home/swing/swingquant/backend"
FRONTEND_DIR="/home/swing/swingquant/frontend"
PYTHON_BIN="$BACKEND_DIR/venv/bin/python"

echo "SwingQuant — Démarrage..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# 1. FastAPI Backend
echo "FastAPI backend → http://localhost:8000"
cd "$BACKEND_DIR"
"$PYTHON_BIN" -m uvicorn api:app --host 0.0.0.0 --port 8000 --reload &
API_PID=$!
echo "   PID: $API_PID"

# 2. Vite Frontend
echo "Vite frontend   → http://localhost:5173"
cd "$FRONTEND_DIR"
npm run dev &
VITE_PID=$!
echo "   PID: $VITE_PID"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Les deux serveurs tournent."
echo ""
echo "   Interface  → http://localhost:5173"
echo "   API Docs   → http://localhost:8000/docs"
echo ""
echo "   Ctrl+C pour arrêter les deux."

trap "kill $API_PID $VITE_PID 2>/dev/null; echo 'Serveurs arrêtés.'" SIGINT SIGTERM
wait
