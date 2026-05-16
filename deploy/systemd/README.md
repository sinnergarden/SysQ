# Qsys systemd samples

These are deployment examples only.

## Placeholders

- `QSYS_ROOT`: replace with the absolute SysQ checkout path
- `QSYS_PYTHON`: replace with the absolute Python interpreter path, e.g. `.envs/test/bin/python`

## Install

Copy the service and timer files into your systemd unit directory, then replace the placeholders before enabling them.

## Suggested schedule

- daily shadow run: after close, e.g. `15:30` or `16:00`
- nightly full-universe backfill: evening off-hours, e.g. `20:30`
- weekly retrain: weekend morning, or early Monday before market open

## Manual run

```bash
PYTHONPATH=QSYS_ROOT QSYS_PYTHON QSYS_ROOT/scripts/ops/run_shadow_daily.py --base-dir QSYS_ROOT --triggered-by manual
PYTHONPATH=QSYS_ROOT QSYS_PYTHON QSYS_ROOT/scripts/ops/run_shadow_retrain_weekly.py --base-dir QSYS_ROOT --triggered-by manual
PYTHONPATH=QSYS_ROOT QSYS_PYTHON QSYS_ROOT/scripts/ops/run_full_universe_backfill.py --base-dir QSYS_ROOT --apply --batch-size 50 --max-batches 4 --triggered-by manual
```

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
