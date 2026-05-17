#!/usr/bin/env bash
# Send a Telegram notification via direct Bot API call
# Usage: notify_telegram.sh <message>
set -uo pipefail

ENV_FILE="/home/liuming/.openclaw/.env"
MESSAGE="${1:-}"

if [ -z "$MESSAGE" ]; then
    MESSAGE=$(cat)
fi
if [ -z "$MESSAGE" ]; then
    echo "Usage: notify_telegram.sh <message>"
    exit 1
fi

# Load env vars (includes proxy settings if set)
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

BOT_TOKEN="${QSYS_TELEGRAM_BOT_TOKEN:-}"
CHAT_ID="${QSYS_TELEGRAM_ALLOWED_CHAT_ID:-}"

if [ -z "$BOT_TOKEN" ] || [ -z "$CHAT_ID" ]; then
    echo "Telegram: skipped - bot token or chat_id not set"
    exit 0
fi

# Use proxy from env, default OpenClaw gateway proxy
if [ -n "${HTTPS_PROXY:-}" ]; then
    PROXY_ARG="--proxy ${HTTPS_PROXY}"
elif [ -n "${HTTP_PROXY:-}" ]; then
    PROXY_ARG="--proxy ${HTTP_PROXY}"
else
    PROXY_ARG="--proxy http://172.31.144.1:12334"
fi

RESULT=$(curl -s -X POST --max-time 10 $PROXY_ARG \
    "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${CHAT_ID}" \
    --data-urlencode "text=${MESSAGE}" \
    --data-urlencode "parse_mode=HTML" \
    -w "\n%{http_code}")

HTTP_CODE=$(echo "$RESULT" | tail -1)
BODY=$(echo "$RESULT" | head -n -1)

if [ "$HTTP_CODE" = "200" ]; then
    echo "Telegram: sent to chat_id=${CHAT_ID}"
else
    echo "Telegram: HTTP ${HTTP_CODE} - $(echo "$BODY" | head -c 200)"
fi
