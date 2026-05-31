# Script Inventory

> Last updated: 2026-05-16
> Scope: safe repo cleanup for current daily ops mainline

## Main entrypoints

- `scripts/run_daily.py`: (target) pre-open / postclose / train unified entry
- `scripts/run_daily_batch.py`: (target) stage-aware batch dispatcher
- `scripts/run_daily_trading.py`: (legacy, systemd) pre-open daily plan and report generation
- `scripts/run_post_close.py`: (legacy, systemd) post-close reconciliation and follow-up reports

> Note: this inventory is partially outdated. See `docs/REPO_SLIMMING_PLAN.md` for current audit.

## Kept scripts

| Group | Scripts | Why kept |
|--------|---------|----------|
| Daily ops | `run_daily_trading.py`, `run_post_close.py`, `run_signal_quality.py`, `run_intent_staging_example.py`, `run_minimal_kernel.py` | Current production and staging flow |
| Data pipeline | `scripts/ops/sync_csi800_daily.py` | CSI800 daily incremental sync (main entry, systemd timer) |
| Legacy data pipeline | `run_update.py`, `update_data_all.py`, `create_instrument_csi300.py`, `dump_bin.py` | Mostly superseded by sync_csi800_daily.py; kept for manual diagnostics |
| Ops scripts | `scripts/ops/fetch_csi800_full.py`, `scripts/ops/backfill_csi800_history.py` | Full re-fetch helpers; see `deploy/systemd/` for timer setup |
| Training / research | `run_train.py`, `run_backtest.py`, `run_strict_eval.py`, `run_compare.py`, `run_feature_build.py`, `run_feature_experiment.py`, `run_feature_ablation.py`, `run_feature_backtest_report.py`, `run_feature_readiness_audit.py` | Current model and feature workflow |
| Debug / manual utilities | `rebuild_qlib_bin.py`, `setup_openclaw_qsys_cron.sh` | Still useful for manual diagnostics or maintenance; not on the daily ops critical path |

## Removed in this cleanup

| Path | Reason |
|------|--------|
| `scripts/run_plan.py` | Deprecated pre-open alias; current mainline is `run_daily_trading.py` |
| `scripts/run_reconcile.py` | Deprecated post-close alias; current mainline is `run_post_close.py` |
| `tests/test_plot_success.png` | Unreferenced test artifact; no test or doc depends on it |
| root `*.log` cleanup files | One-off compile/test outputs, already ignored by `.gitignore` |
| local `__pycache__/` directories | Generated Python cache noise, already ignored by `.gitignore` |

## Explicitly retained for now

| Path | Why not removed |
|------|-----------------|
| `scripts/rebuild_qlib_bin.py` | Destructive rebuild helper with clearer intent than `dump_bin.py`; keep until a safer unified CLI exists |
| `docs/features/new_feature.md` | Required feature-doc template referenced by repo workflow docs |
| `data/` samples and `runs/examples/` | Not fully audited as unused; may still support docs, tests, or demos |

## Cleanup rule going forward

- New daily ops docs should only point to `scripts/run_daily_trading.py` and `scripts/run_post_close.py`.
- Generated logs, caches, and screenshots should stay out of git and be removed after local debugging.
- Thin wrappers should be deleted only after their manual use case is either documented elsewhere or folded into an existing supported CLI.
