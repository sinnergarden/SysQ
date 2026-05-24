# Daily Ops — Framework Dispatch

This runbook describes how to run daily operations using the framework-semantics
entrypoints: ``run_daily.py`` (single strategy) and ``run_daily_batch.py``
(stage-aware batch dispatch).

---

## Quick Reference

| Operation | Command |
|---|---|
| Single strategy preopen | `python scripts/run_daily.py --strategy alpha_v1 --mode preopen --trade-date YYYY-MM-DD` |
| Candidate batch preopen | `python scripts/run_daily_batch.py --stage candidate --mode preopen --trade-date YYYY-MM-DD` |
| Candidate batch postclose | `python scripts/run_daily_batch.py --stage candidate --mode postclose --trade-date YYYY-MM-DD` |
| Production batch preopen | `python scripts/run_daily_batch.py --stage production --mode preopen --trade-date YYYY-MM-DD` |
| Train all candidates | `python scripts/run_daily_batch.py --stage candidate --mode train` |
| Notify only | `python scripts/run_daily_batch.py --stage candidate --mode notify-only --trade-date YYYY-MM-DD` |
| Dry run (no dispatch) | `python scripts/run_daily_batch.py --stage candidate --mode preopen --trade-date YYYY-MM-DD --dry-run` |
| Debug run (no side effects) | `python scripts/run_daily_batch.py --stage candidate --mode preopen --trade-date YYYY-MM-DD --debug-run --no-notify` |

---

## Single Strategy: ``run_daily.py``

```bash
# Preopen — generate predictions + build plan
python scripts/run_daily.py --strategy alpha_v1 --mode preopen --trade-date 2026-05-22

# Postclose — execute plan + MTM
python scripts/run_daily.py --strategy alpha_v1 --mode postclose --trade-date 2026-05-22

# Train
python scripts/run_daily.py --strategy alpha_v1 --mode train

# Notify-only — rebuild notification from existing artifacts
python scripts/run_daily.py --strategy alpha_v1 --notify-only --trade-date 2026-05-22

# Debug mode — no side effects on shadow state
python scripts/run_daily.py --strategy alpha_v1 --mode preopen --trade-date 2026-05-22 --debug-run
```

---

## Batch Dispatch: ``run_daily_batch.py``

The batch runner dispatches **all strategies** matching a lifecycle stage
through run_daily.py, collects results, and writes a machine-readable summary.

### Candidate batch

```bash
# Preopen — all candidate strategies
python scripts/run_daily_batch.py \
    --stage candidate \
    --mode preopen \
    --trade-date 2026-05-22

# Postclose — all candidate strategies
python scripts/run_daily_batch.py \
    --stage candidate \
    --mode postclose \
    --trade-date 2026-05-22

# Train — all candidate strategies that require training
python scripts/run_daily_batch.py \
    --stage candidate \
    --mode train
```

### Production batch

```bash
python scripts/run_daily_batch.py \
    --stage production \
    --mode preopen \
    --trade-date 2026-05-22
```

> **Note**: Production execution requires additional risk controls not yet
> implemented. Currently both candidate and production use the same dispatch
> path via ``run_daily.py`` — separation is structural via stage filtering.

### Filtering

```bash
# Run only specific strategies within a stage
python scripts/run_daily_batch.py \
    --stage candidate \
    --mode preopen \
    --trade-date 2026-05-22 \
    --strategy alpha_v1

# Exclude specific strategies
python scripts/run_daily_batch.py \
    --stage candidate \
    --mode preopen \
    --trade-date 2026-05-22 \
    --exclude alpha_v2
```

### Dry run

```bash
# Show what would be dispatched without running anything
python scripts/run_daily_batch.py \
    --stage candidate \
    --mode preopen \
    --trade-date 2026-05-22 \
    --dry-run
```

---

## Batch Summary Artifact

The batch runner writes a summary JSON to:

```
<output_root>/<trade_date>/batch_<stage>_<mode>.json
```

For example:

- ``runs/2026-05-22/batch_candidate_preopen.json``
- ``runs/2026-05-22/batch_candidate_postclose.json``
- ``runs/2026-05-22/batch_production_preopen.json``

Default output root: ``runs/`` (relative to project root).

Example:

```json
{
  "stage": "candidate",
  "mode": "preopen",
  "trade_date": "2026-05-22",
  "started_at": "2026-05-22T07:59:00",
  "finished_at": "2026-05-22T08:05:00",
  "duration_sec": 300.0,
  "status": "partial_failed",
  "selected_count": 2,
  "success_count": 1,
  "failed_count": 1,
  "strategies": [
    {
      "strategy_id": "alpha_v1",
      "stage": "candidate",
      "status": "success",
      "run_root": "runs/2026-05-22/alpha_v1",
      "duration_sec": 120.0,
      "command": "python scripts/run_daily.py ...",
      "error": null
    },
    {
      "strategy_id": "alpha_v2",
      "stage": "candidate",
      "status": "failed",
      "run_root": null,
      "duration_sec": 5.0,
      "command": "python scripts/run_daily.py ...",
      "error": "ModuleNotFoundError: No module named 'qlib'"
    }
  ]
}
```

### Exit codes

- **0** — all selected strategies succeeded or were intentionally skipped
- **1** — one or more strategies failed

---

## Strategy Lifecycle and Dispatch Rules

| Stage | Daily Batch | Registry Required | Notes |
|---|---|---|---|
| ``research`` | ❌ | No | Evaluated manually / via BacktestRunner |
| ``candidate`` | ✅ | Yes | Shadow-running strategies |
| ``production`` | ✅ | Yes | Live/quasi-live (risk controls TBD) |
| ``rejected`` | ❌ | — | Never dispatched |
| ``archived`` | ❌ | — | Never dispatched |

### What happens at each stage

- **research-stage**: not run by daily batch; evaluated manually or via
  BacktestRunner. May not have a StrategyAdapter.
- **candidate-stage**: shadow strategies that produce artifacts, MTM, and
  ledger entries through the DailyRunner pipeline.
- **production-stage**: same dispatch path as candidate for now. Future
  iterations will add risk checks, broker dispatch, and capital allocation.
- **rejected / archived**: excluded from batch — their configs are retained
  for auditability.

---

## Failure Handling

```bash
# Default: continue on error — run all strategies, report failures
python scripts/run_daily_batch.py \
    --stage candidate \
    --mode preopen \
    --trade-date 2026-05-22 \
    --continue-on-error

# Fail fast — stop at first failure
python scripts/run_daily_batch.py \
    --stage candidate \
    --mode preopen \
    --trade-date 2026-05-22 \
    --fail-fast
```

With ``--continue-on-error`` (default):
- Each strategy runs in an isolated subprocess
- A single strategy failure does not block others
- Final exit code is non-zero if any strategy failed
- Summary JSON records per-strategy status

With ``--fail-fast``:
- Stop dispatching after the first strategy failure
- Earlier strategies may still have run

---

## Legacy Scripts

The following scripts remain as **compatibility entrypoints** only.
New ops should use ``run_daily.py`` or ``run_daily_batch.py``:

- ``scripts/run_alpha_v1_daily.py`` — legacy single-strategy ops entrypoint
- ``scripts/run_alpha_v1_weekly_train.py`` — legacy training entrypoint
- ``scripts/run_preopen.sh`` — legacy shell wrapper
- ``scripts/run_postclose.sh`` — legacy shell wrapper

These scripts are not removed to avoid breaking existing systemd schedules.
Migration to ``run_daily_batch.py``-based systemd templates is the recommended path.
