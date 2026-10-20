#!/usr/bin/env bash
# Fired by systemd OnFailure= when supplydemand_bot.service enters the failed
# state. Restart=on-failure does the recovery; this only notifies (5-min cooldown).
set -euo pipefail
APP_DIR="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
ENV_FILE="$APP_DIR/.env"
COOLDOWN_FILE="$APP_DIR/.failure_alert_last_${1:-supplydemand_bot}"
if [ -f "$COOLDOWN_FILE" ] && [ $(( $(date +%s) - $(cat "$COOLDOWN_FILE") )) -lt 300 ]; then exit 0; fi
TOKEN=$(grep -E '^TELEGRAM_BOT_TOKEN=' "$ENV_FILE" | cut -d= -f2-)
CHAT_ID=$(grep -E '^TELEGRAM_CHAT_ID=' "$ENV_FILE" | cut -d= -f2-)
UNIT="${1:-supplydemand_bot}"
MSG="[S/D] ALERT
${UNIT}.service FAILED on $(hostname) at $(date -u '+%Y-%m-%d %H:%M:%S UTC').
systemd will auto-restart it. Check: journalctl -u ${UNIT} -n 50"
curl -s -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${CHAT_ID}" --data-urlencode "text=${MSG}" > /dev/null || true
date +%s > "$COOLDOWN_FILE"
