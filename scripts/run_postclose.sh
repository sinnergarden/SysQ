#!/usr/bin/env bash
# systemd post-close wrapper (15:30 Mon-Fri)
# REAL_SYNC_PATH env var or --real-sync CLI arg overrides automatic file discovery
set -u
cd "$(cd "$(dirname "$0")/.." && pwd)"

PYTHON="/home/liuming/.openclaw/workspace/.mamba/envs/dl/bin/python"
TODAY=$(date +%Y-%m-%d)

# Check if --real-sync passed explicitly
REAL_SYNC=""
for arg in "$@"; do
    if [ -n "$REAL_SYNC" ]; then
        REAL_SYNC="$arg"
        break
    fi
    if [ "$arg" = "--real_sync" ]; then
        REAL_SYNC="__NEXT_ARG__"
    fi
done

# Fallback: REAL_SYNC_PATH env var
if [ -z "$REAL_SYNC" ] || [ "$REAL_SYNC" = "__NEXT_ARG__" ]; then
    REAL_SYNC="${REAL_SYNC_PATH:-}"
fi

# Fallback: conventional paths
if [ -z "$REAL_SYNC" ]; then
    for path in \
        "/home/liuming/.openclaw/workspace/orders/miniqmt_readback_${TODAY}.json" \
        "/home/liuming/.openclaw/workspace/orders/real_sync_${TODAY}.csv" \
        "/home/liuming/.openclaw/broker/miniqmt_readback_${TODAY}.json" \
        "/home/liuming/.openclaw/broker/real_sync_${TODAY}.csv"; do
        if [ -f "$path" ]; then
            REAL_SYNC="$path"
            break
        fi
    done
fi

if [ -z "$REAL_SYNC" ]; then
    echo "Post-close: no real_sync file for $TODAY (shadow mode)"
    # Try shadow plan summary path
    SHADOW_SUMMARY="/home/liuming/.openclaw/workspace/SysQ/experiments/alpha_v1_backtest_csi800/shadow_plan_${TODAY}.json"
    if [ -f "$SHADOW_SUMMARY" ]; then
        echo "Shadow plan exists at $SHADOW_SUMMARY"
    fi
    # Send shadow-mode notification
    NOTIFY_SCRIPT="$(cd "$(dirname "$0")" && pwd)/notify_telegram.sh"
    bash "$NOTIFY_SCRIPT" "📋 <b>Post-close $TODAY</b>

No real orders today (shadow mode, ¥500k).
Shadow Alpha V1 plan will run at 08:00 tomorrow." 2>/dev/null || true
    exit 0
fi

$PYTHON scripts/run_post_close.py \
  --date "$TODAY" \
  --real_sync "$REAL_SYNC" \
  --no_report \
  "$@"
EXIT_CODE=$?

# Notify via Telegram (non-blocking)
NOTIFY_SCRIPT="$(cd "$(dirname "$0")" && pwd)/notify_telegram.sh"
if [ $EXIT_CODE -eq 0 ]; then
    bash "$NOTIFY_SCRIPT" "Post-close $TODAY completed" 2>/dev/null || true
else
    bash "$NOTIFY_SCRIPT" "Post-close $TODAY FAILED (exit $EXIT_CODE)" 2>/dev/null || true
fi

exit $EXIT_CODE
