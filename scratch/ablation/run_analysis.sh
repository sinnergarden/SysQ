#!/bin/bash
# Orchestrate ablation analysis once all backtests are done.
# Run from the MAIN repo cwd.
set -euo pipefail

MAIN=/home/liuming/.openclaw/workspace/SysQ
WT=/home/liuming/.openclaw/workspace/SysQ-execution-ledger
RUNS_ROOT=$WT/data/research/ablation/execution_policy
EP_ROOT=/tmp/ablation_episodes
SCRATCH=$WT/scratch/ablation
RUNS="${RUNS:-A0_none,A1_hard_stop,A2_score_delta,A3_winner_trailing,A4_stale,A5_all}"

mkdir -p "$EP_ROOT"
cd "$MAIN"

echo "### 1/3 derive episodes"
for r in $(echo "$RUNS" | tr ',' ' '); do
  [ -f "$RUNS_ROOT/$r/metrics.json" ] || { echo "SKIP $r (not done)"; continue; }
  python "$SCRATCH/derive_episodes.py" \
    --run-dir "$RUNS_ROOT/$r" \
    --out "$EP_ROOT/$r.json" 2>/dev/null
done

echo "### 2/3 analyze layers 1-3"
python "$SCRATCH/analyze_layers.py" \
  --runs-root "$RUNS_ROOT" \
  --episodes-root "$EP_ROOT" \
  --out /tmp/ablation_analysis.json \
  --runs "$RUNS"

echo "### 3/3 analyze layer4 + report"
python "$SCRATCH/analyze_layer4.py" \
  --runs-root "$RUNS_ROOT" \
  --episodes-root "$EP_ROOT" \
  --out /tmp/ablation_layer4.json \
  --runs "$RUNS"

echo "### report"
python "$SCRATCH/report_synthesis.py" \
  --analysis /tmp/ablation_analysis.json \
  --layer4 /tmp/ablation_layer4.json \
  --runs-root "$RUNS_ROOT" \
  --focus-run "${FOCUS_RUN:-A5_all}" 2>&1 | tee /tmp/ablation_report.txt
