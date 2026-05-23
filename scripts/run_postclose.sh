#!/usr/bin/env bash
# systemd post-close wrapper (22:00 Mon-Fri)
# 盘后: 读取 21:30 CSI800 sync 后的数据 → 开盘价执行交易计划 → 收盘价 MTM → 通知
# 真实接入线上交易时: 9:25 集合竞价 + 9:30 确认买入（需额外脚本）
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

if [ -n "$REAL_SYNC" ]; then
    $PYTHON scripts/run_post_close.py \
      --date "$TODAY" \
      --real_sync "$REAL_SYNC" \
      --no_report \
      "$@"
    EXIT_CODE=$?
else
    echo "WARNING: --real_sync not found for $TODAY, skipping broker reconciliation"
    EXIT_CODE=0
fi

# Alpha V1 post-close notification (rich PnL summary)
if [ $EXIT_CODE -eq 0 ]; then
    $PYTHON scripts/run_alpha_v1_daily.py \
      --trade-date "$TODAY" \
      --mode postclose \
      || true
fi

exit $EXIT_CODE
