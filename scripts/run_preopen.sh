#!/usr/bin/env bash
# systemd pre-open wrapper (08:30 Mon-Fri)
# Computes signal_date = previous trading day, execution_date = today
set -euo pipefail
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

# Notify via Telegram (non-blocking)
NOTIFY_SCRIPT="$(cd "$(dirname "$0")" && pwd)/notify_telegram.sh"
if [ $EXIT_CODE -eq 0 ]; then
    bash "$NOTIFY_SCRIPT" "Pre-open $SIGNAL_DATE -> $(date +%Y-%m-%d) completed" 2>/dev/null || true
else
    bash "$NOTIFY_SCRIPT" "Pre-open $SIGNAL_DATE -> $(date +%Y-%m-%d) FAILED (exit $EXIT_CODE)" 2>/dev/null || true
fi

exit $EXIT_CODE
