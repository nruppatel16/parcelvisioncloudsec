#!/bin/bash
set -euo pipefail

APP_DIR="/opt/lobbyops/lobbyops"
SERVICE="lobbyops"

echo "Pulling latest from main..."
git -C /opt/lobbyops pull origin main

echo "Installing dependencies..."
pip3 install -q -r "$APP_DIR/requirements.txt"

echo "Running database init..."
cd "$APP_DIR"
python3 -c "import queue_store; queue_store.init_db()"

echo "Reloading service..."
systemctl reload-or-restart "$SERVICE"

echo "Last 20 journal lines:"
journalctl -u "$SERVICE" -n 20 --no-pager
