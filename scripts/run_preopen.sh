#!/usr/bin/env bash
# systemd pre-open wrapper (08:00 Mon-Fri)
# 盘前: 生成交易计划 → 21:30 CSI800 数据同步 → 22:00 postclose 开盘价执行 + 收盘价 MTM
set -euo pipefail

echo "============================================================"
echo "  ⚠ DEPRECATED: run_preopen.sh"
echo "  This is a legacy compatibility entrypoint."
echo "  Prefer:"
echo "    python scripts/run_daily_batch.py --stage candidate --mode preopen"
echo "============================================================"

cd "$(cd "$(dirname "$0")/.." && pwd)"

PYTHON="/home/liuming/.openclaw/workspace/.mamba/envs/dl/bin/python"
SIGNAL_DATE=$($PYTHON -c "
import sys; sys.path.insert(0, '.')
from qsys.data.adapter import QlibAdapter
from scripts.run_daily_trading import previous_trading_day
QlibAdapter().init_qlib()
print(previous_trading_day('$(date +%Y-%m-%d)'))
")

$PYTHON scripts/run_daily_trading.py \
  --date "$SIGNAL_DATE" \
  --execution_date "$(date +%Y-%m-%d)" \
  --no_report \
  "$@"
EXIT_CODE=$?

NOTIFY_SCRIPT="$(cd "$(dirname "$0")" && pwd)/notify_telegram.sh"
if [ $EXIT_CODE -eq 0 ]; then
    # 成功：alpha_v1 inference + plan + rich notification
    $PYTHON scripts/run_alpha_v1_daily.py \
      --trade-date "$(date +%Y-%m-%d)" \
      --mode preopen \
      || true
else
    # 失败：简单通知
    bash "$NOTIFY_SCRIPT" "Pre-open $SIGNAL_DATE -> $(date +%Y-%m-%d) FAILED (exit $EXIT_CODE)" || true
fi

exit $EXIT_CODE
