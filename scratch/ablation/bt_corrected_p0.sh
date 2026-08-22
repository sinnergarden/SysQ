#!/usr/bin/env bash
# Re-run PIT audit P0 with corrected label.
# Flags byte-identical to run_raw_rank_phases.py BASE + NEVER (frozen Stage-6),
# only signal_run_id / output-dir differ (per docs/PIT_UNIVERSE_AUDIT.md §10.3).
set -euo pipefail
cd /home/liuming/.openclaw/workspace/SysQ
OUT=data/research/ablation/pit_audit/RR_P0_raw__pitv1_corrected_label
python scripts/research/backtest_from_signal.py \
  --signal-id fwd_ret_180d_raw_pit__daily_zscore \
  --signal-run-id fwd_ret_180d_raw_pit__daily_zscore__rr_p0__rawrank__financial_rc_180d_rolling_5y_to_202607_v3_pit \
  --research-root /home/liuming/.openclaw/workspace/SysQ/data/research \
  --score-column score \
  --top-n 5 \
  --commission 0.0003 \
  --stamp-duty 0.001 \
  --min-commission 5.0 \
  --slippage 0.001 \
  --rebalance-freq 20d \
  --rebalance-offset 0 \
  --holding-policy posterior_confirmed \
  --strategy-template-id posterior_confirmed_top5_financial_rc_50_50_v1 \
  --start-date 2021-01-04 \
  --end-date 2026-07-31 \
  --initial-capital 10000000 \
  --score-delta-min-observations 1000000000 \
  --posterior-stop-loss 0.999 \
  --winner-activation-return 0.9999 \
  --winner-trailing-stop 0.999 \
  --stale-after-days 10000 \
  --replacement-rank-gap 1000000 \
  --rank-exit \
  --overwrite \
  --output-dir "$OUT"
