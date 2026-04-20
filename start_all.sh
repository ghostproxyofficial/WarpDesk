I#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PY_AGENT_DIR="$ROOT_DIR/agent"
VENV_DIR="$PY_AGENT_DIR/.venv"
PORT="${WARPDESK_PORT:-8443}"

echo "[WarpDesk] Python startup (shell)"
echo ""

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required but was not found in PATH."
    exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "[1/4] Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

echo "[2/4] Installing Python dependencies..."
"$VENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null
"$VENV_DIR/bin/pip" install -r "$PY_AGENT_DIR/requirements.txt" >/dev/null

if command -v lsof >/dev/null 2>&1; then
    PIDS="$(lsof -ti tcp:"$PORT" || true)"
    if [ -n "$PIDS" ]; then
        echo "[3/4] Releasing port $PORT..."
        kill -9 $PIDS || true
    fi
fi

echo "[4/4] Starting WarpDesk agent backend..."
export WARPDESK_PORT="$PORT"
export WARPDESK_CODEC="h264"
export WARPDESK_AUDIO_SOURCE="system"
export WARPDESK_ALLOW_MIC_FALLBACK="0"
export WEBRTC_ENCODER="auto"
export PYTHONUNBUFFERED="1"

cd "$PY_AGENT_DIR"
"$VENV_DIR/bin/python" -u app.py
