#!/bin/bash
# Rotate the /valet/* API key.
# Run at personnel offboarding, on suspected compromise, or on a 90-day schedule.
# After rotation, update the key in all clients (mobile shortcut, inject_tab environment).
set -euo pipefail

ENV_FILE="/opt/lobbyops/lobbyops/.env"
SERVICE="lobbyops"

NEW_KEY=$(openssl rand -hex 32)

sed -i "s/^API_KEY=.*/API_KEY=${NEW_KEY}/" "$ENV_FILE"

systemctl reload-or-restart "$SERVICE"

echo "New API key: ${NEW_KEY}"
echo "This is printed once. Update all clients immediately."
