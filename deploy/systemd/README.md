# Qsys systemd samples

These are deployment examples only.

## Runtime contract

The production CSI1800 PIT unit runs from the clean, stable checkout
`/home/liuming/.openclaw/workspace/SysQ-runtime`. Do not point this unit at a
development worktree with uncommitted changes. It uses the canonical
`scripts/data_sync.py` entrypoint and writes only PIT-scoped data-sync
artifacts; signal inference is deliberately a separate operation.

The unit binds configuration and data explicitly with `QSYS_SETTINGS_FILE`
and `QSYS_DATA_ROOT`. Code therefore comes from the clean runtime checkout,
while canonical data, Qlib, immutable PIT snapshots, and audit JSON continue
to use the existing production SOT under the main workspace. Do not remove
these bindings or replace them with a symlinked `data/` directory.

The other examples in this directory use `QSYS_ROOT` and `QSYS_PYTHON` as
placeholders when copied to another host.

## Install

Copy the service and timer files into your systemd unit directory, then replace the placeholders before enabling them.

## Suggested schedule

- CSI1800 PIT daily data sync: weekdays at 19:00 (`qsys-csi1800-pit-daily-sync.timer`)
- csi800 daily data sync: after close, e.g. `21:30` (triggers `qsys-csi800-daily-sync.service`)
- daily shadow run: after close, e.g. `15:30` or `16:00`
- nightly full-universe backfill: evening off-hours, e.g. `20:30`
- weekly retrain: weekend morning, or early Monday before market open

## Manual run

```bash
# CSI1800 PIT daily data sync
PYTHONPATH=/home/liuming/.openclaw/workspace/SysQ-runtime \
  /home/liuming/.openclaw/workspace/.mamba/envs/dl/bin/python \
  /home/liuming/.openclaw/workspace/SysQ-runtime/scripts/data_sync.py \
  --universe csi1800 --apply

# CSI800 daily data sync
PYTHONPATH=QSYS_ROOT QSYS_PYTHON QSYS_ROOT/scripts/data_sync.py --universe csi800 --apply
PYTHONPATH=QSYS_ROOT QSYS_PYTHON QSYS_ROOT/scripts/run_daily.py --strategy financial_rc --mode infer --signal-date auto --top-k 200

# Daily shadow run
PYTHONPATH=QSYS_ROOT QSYS_PYTHON QSYS_ROOT/scripts/ops/run_shadow_daily.py --base-dir QSYS_ROOT --triggered-by manual
PYTHONPATH=QSYS_ROOT QSYS_PYTHON QSYS_ROOT/scripts/ops/run_shadow_retrain_weekly.py --base-dir QSYS_ROOT --triggered-by manual
PYTHONPATH=QSYS_ROOT QSYS_PYTHON QSYS_ROOT/scripts/ops/run_full_universe_backfill.py --base-dir QSYS_ROOT --apply --batch-size 50 --max-batches 4 --triggered-by manual
```

## CSI1800 PIT Daily Data Sync

The `qsys-csi1800-pit-daily-sync.service` + `.timer` pair is the production
data-sync unit for the PIT CSI1800 universe (CSI800 + CSI1000). It runs only
`data_sync.py --universe csi1800 --apply`; it does not run inference or use a
mutable `latest` pointer.

Install or update the pair:

```bash
install -m 0644 deploy/systemd/qsys-csi1800-pit-daily-sync.service \
  ~/.config/systemd/user/qsys-csi1800-pit-daily-sync.service
install -m 0644 deploy/systemd/qsys-csi1800-pit-daily-sync.timer \
  ~/.config/systemd/user/qsys-csi1800-pit-daily-sync.timer
systemctl --user daemon-reload
systemctl --user disable --now qsys-csi800-daily-sync.timer
systemctl --user stop qsys-csi800-daily-sync.service
systemctl --user show qsys-csi800-daily-sync.service -p ActiveState -p SubState
systemctl --user show qsys-csi800-daily-sync.service -p ActiveState | grep -qx 'ActiveState=inactive'
systemctl --user reset-failed qsys-csi800-daily-sync.service
systemctl --user enable --now qsys-csi1800-pit-daily-sync.timer
```

Do not enable or start the CSI1800 timer until the old service reports
`ActiveState=inactive`. Stopping a timer alone does not quiesce a service that
was already running.

Verify the timer and one controlled service run:

```bash
systemctl --user list-timers --all | grep qsys-csi1800-pit-daily-sync
systemctl --user status qsys-csi1800-pit-daily-sync.timer
systemctl --user start qsys-csi1800-pit-daily-sync.service
journalctl --user -u qsys-csi1800-pit-daily-sync.service -n 100 --no-pager
tail -n 100 /home/liuming/.openclaw/logs/sync_csi1800_pit_daily.log
```

Rollback is explicit and reversible:

```bash
systemctl --user disable --now qsys-csi1800-pit-daily-sync.timer
systemctl --user stop qsys-csi1800-pit-daily-sync.service
systemctl --user show qsys-csi1800-pit-daily-sync.service -p ActiveState -p SubState
systemctl --user show qsys-csi1800-pit-daily-sync.service -p ActiveState | grep -qx 'ActiveState=inactive'
systemctl --user enable --now qsys-csi800-daily-sync.timer
```

Do not run both daily-sync timers concurrently: they overlap in raw-data
fetching and can contend for API quota and local resources.

## CSI800 Daily Data Sync

The `qsys-csi800-daily-sync.service` + `.timer` replace the earlier `setup_openclaw_qsys_cron.sh` approach. Keep this pair only for explicit CSI800 compatibility or rollback; the production PIT universe is handled by the CSI1800 pair above.

Flow:
1. Resolve the last completed trading day
2. Fetch latest CSI800 constituents via `index_weight` API
3. Pre-check: skip stocks that already have the target date (read feather `trade_date` column)
4. Batch fetch missing T-day data (daily, daily_basic, adj_factor, moneyflow, stk_limit)
5. Resolve the exact previous open session and repair margin history through T-1
6. Convert/refresh affected Qlib symbols
7. Refresh csi300 + csi800 instrument files
8. Run readiness checks, write structured audit JSON, then generate financial_rc Top200

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
