# Shadow Daily Ops

This document defines the PR1 protocol files and the PR2 weekly retrain wiring.

## Scope

- Daily runner remains a protocol skeleton.
- Weekly retrain now calls real training through `scripts/run_train.py`.
- No daily inference, rebalancing, MiniQMT, systemd, UI, or research-framework changes are part of this flow.

## Run identity

- daily run_id: `shadow_YYYY-MM-DD_HHMMSS`
- weekly retrain run_id: `shadow_retrain_YYYY-MM-DD_HHMMSS`
- run directory: `runs/YYYY-MM-DD/<run_id>/`

## Allowed status values

- `pending`
- `running`
- `success`
- `failed`
- `skipped`
- `fallback`

## Daily stages

- `data_sync`
- `feature_refresh`
- `maybe_retrain`
- `select_model`
- `inference`
- `shadow_rebalance`
- `archive_report`

## Weekly retrain stages

- `prepare_training`
- `run_training`
- `update_model_pointer`
- `archive_report`

## Weekly retrain semantics

The weekly retrain entrypoint is `scripts/ops/run_shadow_retrain_weekly.py`.

Behavior:

- default `mainline_object_name` is fixed to `feature_173`
- training is delegated to `scripts/run_train.py --model qlib_lgbm --bundle_id bundle_feature_173`
- the runner reuses the normal training outputs under the model directory resolved by `cfg.get_path("root") / "models" / <model_name>`
- after a verified success, the runner writes `models/latest_shadow_model.json`
- if training fails and the existing latest model pointer is usable, the runner keeps that pointer and marks the retrain as `fallback`
- if training fails and no usable latest model pointer exists, the retrain is `failed`
- a failed training run never overwrites the previous latest model pointer

## Manifest contract

Each run writes `manifest.json` under its run directory.

Required top-level fields:

- `run_id`
- `run_type` (`shadow_daily` or `shadow_retrain_weekly`)
- `trade_date`
- `mainline_object_name`
- `bundle_id`
- `model_name`
- `model_snapshot_path`
- `latest_model_pointer`
- `stage_status`
- `overall_status`
- `data_snapshot`
- `fallback_summary`
- `started_at`
- `ended_at`
- `notes`

Each `stage_status[stage_name]` entry contains:

- `status`
- `started_at`
- `ended_at`
- `message`
- `artifact_pointers`

Weekly retrain writes real artifact pointers for:

- `training_report_path`
- `training_summary_path`
- `config_snapshot_path`
- `decisions_path`
- `model_path`
- `latest_model_pointer_path`

The manifest also records:

- `model_name`
- `model_snapshot_path`
- `latest_model_pointer`

## Summary contract

Daily runs write `daily_summary.json` with these fields:

- `trade_date`
- `run_id`
- `run_type`
- `data_status`
- `feature_status`
- `train_status`
- `model_used`
- `inference_status`
- `rebalance_status`
- `shadow_order_count`
- `degradation_level`
- `decision_status`
- `notes`

Weekly retrain still writes `daily_summary.json` so both runners share one summary artifact shape.
For weekly retrain, the summary reflects:

- training stage result in `train_status`
- chosen model details in `model_used`
- fallback usage in `model_used.fallback`
- pointer outcome in `decision_status`
- retained previous-model notes when fallback is used

## Latest pointers

Project pointers are stored under `runs/` and atomically overwritten:

- `runs/latest_shadow_daily.json`
- `runs/latest_shadow_retrain.json`

Required `runs/latest_shadow_daily.json` fields:

- `run_id`
- `trade_date`
- `overall_status`
- `manifest_path`
- `daily_summary_path`
- `updated_at`

Required `runs/latest_shadow_retrain.json` fields:

- `run_id`
- `trade_date`
- `overall_status`
- `manifest_path`
- `updated_at`

The latest model pointer lives at `models/latest_shadow_model.json` and contains:

- `model_name`
- `model_path`
- `mainline_object_name`
- `bundle_id`
- `train_run_id`
- `trained_at`
- `status`

A latest model pointer is considered usable only when all required fields exist and `model_path` still exists.

## Helper API

The protocol helper layer in `qsys/ops/` supports:

- initializing a run
- updating stage status
- finalizing a run
- atomically writing latest pointers
- reading and validating the latest shadow model pointer
- writing the latest shadow model pointer
- invoking the real weekly training flow

## Idempotency and re-entry

- A fixed `run_id` always resolves to the same run directory.
- Re-running with the same `run_id` rewrites protocol files in place.
- Latest pointers are updated with atomic replace.
- `finalize_run()` derives `overall_status` from `stage_status`.
- Weekly retrain updates `models/latest_shadow_model.json` only after successful training artifact validation.

## Runners

- `scripts/ops/run_shadow_daily.py`
- `scripts/ops/run_shadow_retrain_weekly.py`

The daily runner is still stub-only.
The weekly retrain runner now executes the real training path and archives traceable protocol artifacts for the run directory.
