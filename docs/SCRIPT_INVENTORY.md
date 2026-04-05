# Script Inventory

> Last updated: 2026-04-07
> Scope: safe repo cleanup for current daily ops mainline

## Main entrypoints

Current daily ops only support these CLI entrypoints:

- `scripts/run_daily_trading.py`: pre-open daily plan and report generation
- `scripts/run_post_close.py`: post-close reconciliation and follow-up reports

Legacy aliases `scripts/run_plan.py` and `scripts/run_reconcile.py` have been removed in this cleanup. Docs and future automation should call the main entrypoints directly.

## Kept scripts

| Group | Scripts | Why kept |
|--------|---------|----------|
| Daily ops | `run_daily_trading.py`, `run_post_close.py`, `run_signal_quality.py`, `run_intent_staging_example.py`, `run_minimal_kernel.py` | Current production and staging flow |
| Data pipeline | `run_update.py`, `update_data_all.py`, `create_instrument_csi300.py`, `dump_bin.py` | Active data refresh and qlib bin maintenance |
| Training / research | `run_train.py`, `run_backtest.py`, `run_strict_eval.py`, `run_compare.py`, `run_feature_build.py`, `run_feature_experiment.py`, `run_feature_ablation.py`, `run_feature_backtest_report.py`, `run_feature_readiness_audit.py` | Current model and feature workflow |
| Debug / manual utilities | `debug_data_quality.py`, `debug_model_performance.py`, `check_amount.py`, `rebuild_qlib_bin.py`, `setup_openclaw_qsys_cron.sh` | Still useful for manual diagnostics or maintenance; not on the daily ops critical path |

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
| `scripts/check_amount.py` | Thin, but still a direct manual qlib raw-data probe; low risk to revisit later |
| `scripts/rebuild_qlib_bin.py` | Destructive rebuild helper with clearer intent than `dump_bin.py`; keep until a safer unified CLI exists |
| `docs/features/new_feature.md` | Required feature-doc template referenced by repo workflow docs |
| `data/` samples and `runs/examples/` | Not fully audited as unused; may still support docs, tests, or demos |

## Cleanup rule going forward

- New daily ops docs should only point to `scripts/run_daily_trading.py` and `scripts/run_post_close.py`.
- Generated logs, caches, and screenshots should stay out of git and be removed after local debugging.
- Thin wrappers should be deleted only after their manual use case is either documented elsewhere or folded into an existing supported CLI.
