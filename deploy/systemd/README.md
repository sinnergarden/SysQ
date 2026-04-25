# Qsys systemd samples

These are deployment examples only.

## Placeholders

- `QSYS_ROOT`: replace with the absolute SysQ checkout path
- `QSYS_PYTHON`: replace with the absolute Python interpreter path, e.g. `.envs/test/bin/python`

## Install

Copy the service and timer files into your systemd unit directory, then replace the placeholders before enabling them.

## Suggested schedule

- daily shadow run: after close, e.g. `15:30` or `16:00`
- weekly retrain: weekend morning, or early Monday before market open

## Manual run

```bash
PYTHONPATH=QSYS_ROOT QSYS_PYTHON QSYS_ROOT/scripts/ops/run_shadow_daily.py --base-dir QSYS_ROOT --triggered-by manual
PYTHONPATH=QSYS_ROOT QSYS_PYTHON QSYS_ROOT/scripts/ops/run_shadow_retrain_weekly.py --base-dir QSYS_ROOT --triggered-by manual
```

## Status check

```bash
PYTHONPATH=QSYS_ROOT QSYS_PYTHON QSYS_ROOT/scripts/ops/check_shadow_status.py --base-dir QSYS_ROOT --format json
PYTHONPATH=QSYS_ROOT QSYS_PYTHON QSYS_ROOT/scripts/ops/check_shadow_status.py --base-dir QSYS_ROOT --format text
PYTHONPATH=QSYS_ROOT QSYS_PYTHON QSYS_ROOT/scripts/ops/check_shadow_status.py --base-dir QSYS_ROOT --format json --write-latest
```

The latest status snapshot is written to `runs/latest_ops_status.json` when `--write-latest` is used.

## Note

OpenClaw cron can still be used as a personal helper, but it is not the production scheduling path.
