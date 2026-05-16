# Qsys systemd samples

These are deployment examples only.

## Placeholders

- `QSYS_ROOT`: replace with the absolute SysQ checkout path
- `QSYS_PYTHON`: replace with the absolute Python interpreter path, e.g. `.envs/test/bin/python`

## Install

Copy the service and timer files into your systemd unit directory, then replace the placeholders before enabling them.

## Suggested schedule

- csi800 daily data sync: after close, e.g. `21:30` (triggers `qsys-csi800-daily-sync.service`)
- daily shadow run: after close, e.g. `15:30` or `16:00`
- nightly full-universe backfill: evening off-hours, e.g. `20:30`
- weekly retrain: weekend morning, or early Monday before market open

## Manual run

```bash
# CSI800 daily data sync
PYTHONPATH=QSYS_ROOT QSYS_PYTHON QSYS_ROOT/scripts/ops/sync_csi800_daily.py --apply

# Daily shadow run
PYTHONPATH=QSYS_ROOT QSYS_PYTHON QSYS_ROOT/scripts/ops/run_shadow_daily.py --base-dir QSYS_ROOT --triggered-by manual
PYTHONPATH=QSYS_ROOT QSYS_PYTHON QSYS_ROOT/scripts/ops/run_shadow_retrain_weekly.py --base-dir QSYS_ROOT --triggered-by manual
PYTHONPATH=QSYS_ROOT QSYS_PYTHON QSYS_ROOT/scripts/ops/run_full_universe_backfill.py --base-dir QSYS_ROOT --apply --batch-size 50 --max-batches 4 --triggered-by manual
```

## CSI800 Daily Data Sync

The `qsys-csi800-daily-sync.service` + `.timer` replace the earlier `setup_openclaw_qsys_cron.sh` approach.

Flow:
1. Resolve the last completed trading day
2. Fetch latest CSI800 constituents via `index_weight` API
3. Pre-check: skip stocks that already have the target date (read feather `trade_date` column)
4. Batch fetch missing data (daily, daily_basic, adj_factor, moneyflow, margin, stk_limit)
5. Convert to Qlib bin (incremental, fallback to fix)
6. Refresh csi300 + csi800 instrument files
7. Run readiness check (6 core field null rates, active count >= 750)
8. Write structured audit JSON to `data/audit/`

Audit records are per-day JSON files: `data/audit/sync_csi800_{YYYYMMDD}.json`. Contents include step timing, constituent count, and readiness check results.

## Status check

```bash
PYTHONPATH=QSYS_ROOT QSYS_PYTHON QSYS_ROOT/scripts/ops/check_shadow_status.py --base-dir QSYS_ROOT --format json
PYTHONPATH=QSYS_ROOT QSYS_PYTHON QSYS_ROOT/scripts/ops/check_shadow_status.py --base-dir QSYS_ROOT --format text
PYTHONPATH=QSYS_ROOT QSYS_PYTHON QSYS_ROOT/scripts/ops/check_shadow_status.py --base-dir QSYS_ROOT --format json --write-latest
```

The latest status snapshot is written to `runs/latest_ops_status.json` when `--write-latest` is used.

## Full-universe backfill

The `qsys-full-universe-backfill.*` timer/service is meant to chip away at all-A raw completeness without forcing a single huge `update_data_all.py` run.

Recommended usage:

- keep daily/shadow production flows on their own timers
- let full-universe backfill run off-hours in bounded batches
- inspect `runs/latest_full_universe_backfill.json` after each run
- once raw completeness is close to done, you can raise `--max-batches` or disable the timer

## Note

OpenClaw cron can still be used as a personal helper, but it is not the production scheduling path.
