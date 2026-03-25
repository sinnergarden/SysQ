#!/usr/bin/env bash
set -euo pipefail

# Install two OpenClaw cron jobs for Qsys ops.
#
# Default behavior:
# - creates jobs in DISABLED state for safe review
# - use ENABLE_NOW=1 to create them enabled immediately
#
# Usage:
#   cd /home/liuming/.openclaw/workspace
#   bash SysQ/scripts/setup_openclaw_qsys_cron.sh
#   ENABLE_NOW=1 bash SysQ/scripts/setup_openclaw_qsys_cron.sh
#
# Notes:
# - Jobs target the default `main` agent in the main session.
# - Delivery uses `--announce` on the last active channel, matching current OpenClaw CLI defaults.
# - Daily job runs on weekdays at 07:00 Asia/Shanghai; the agent must still no-op on exchange holidays.
# - Weekly job runs Saturday 10:00 Asia/Shanghai and must never auto-promote production.

ROOT="/home/liuming/.openclaw/workspace"
cd "$ROOT"

DISABLED_FLAG=(--disabled)
if [[ "${ENABLE_NOW:-0}" == "1" ]]; then
  DISABLED_FLAG=()
fi

DAILY_MESSAGE=$(cat <<'EOF'
Qsys daily ops.

If today is not a China A-share trading day, reply with a brief skip note and stop.
If it is a trading day, run the full Qsys pre-market workflow starting at 07:00 Asia/Shanghai and deliver the final stock recommendations before market open.

Execution requirements:
- Treat this as one indivisible chain: raw data pull -> qlib/bin conversion -> readiness/health validation -> model serving/daily workflow -> stock recommendations.
- Do not stop after raw data; qlib/bin sync and availability checks are mandatory.
- Use existing Qsys/OpenClaw commands only; do not invent commands.
- Prefer the operator path for operational execution if routing is needed.
- Require update success and raw/qlib alignment before serving.
- Use current production model selection logic; do not change production manifest.
- Use the small-account production path outside SysQ/data:
  - db_path=/home/liuming/.openclaw/workspace/positions/qsys_real_account.db
  - output_dir=/home/liuming/.openclaw/workspace/orders
  - report_dir=/home/liuming/.openclaw/workspace/daily
- Output must include: data status, signal_date, execution_date, executable plan vs target plan, risk notes, and final recommended stocks.
- Keep the run auditable and report blockers immediately.
EOF
)

WEEKLY_MESSAGE=$(cat <<'EOF'
Qsys weekly model ops.

Run the weekly retrain/eval pipeline for Qsys.

Requirements:
- Refresh/validate data first if needed.
- Train a candidate with the existing training entrypoint.
- Run strict evaluation comparing candidate vs current production / baseline as appropriate.
- Summarize metrics, risks, and whether promotion is recommended.
- Do NOT auto-promote production.
- Do NOT edit production_manifest.yaml unless the user explicitly approves.
- Use existing Qsys/OpenClaw commands only; keep outputs auditable.
EOF
)

openclaw cron add \
  --name qsys-daily-preopen \
  --description "Qsys pre-open daily ops; weekdays 07:00 Asia/Shanghai, holiday-aware in-agent" \
  --agent main \
  --session main \
  --cron '0 7 * * 1-5' \
  --tz Asia/Shanghai \
  --announce \
  --expect-final \
  --thinking low \
  "${DISABLED_FLAG[@]}" \
  --message "$DAILY_MESSAGE"

openclaw cron add \
  --name qsys-weekly-retrain-eval \
  --description "Qsys weekly retrain/eval; Saturday 10:00 Asia/Shanghai; no auto-promotion" \
  --agent main \
  --session main \
  --cron '0 10 * * 6' \
  --tz Asia/Shanghai \
  --announce \
  --expect-final \
  --thinking low \
  "${DISABLED_FLAG[@]}" \
  --message "$WEEKLY_MESSAGE"

printf '\nInstalled Qsys cron jobs. Current status:\n\n'
openclaw cron list
