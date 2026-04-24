# Shadow Daily Ops

This document defines the PR1 file protocol skeleton for shadow daily operations.

## Scope

PR1 only adds protocol files and stub runners. It does not call real training,
inference, rebalancing, MiniQMT, systemd, or UI code.

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

Weekly retrain also writes `daily_summary.json` in PR1 so both runners share one
summary artifact shape while business logic is still stubbed.

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

## Helper API

The protocol helper layer in `qsys/ops/` supports:

- initializing a run
- updating stage status
- finalizing a run
- atomically writing latest pointers
- writing the latest shadow model pointer

## Idempotency and re-entry

- A fixed `run_id` always resolves to the same run directory.
- Re-running with the same `run_id` rewrites protocol files in place.
- Latest pointers are updated with atomic replace.
- `finalize_run()` derives `overall_status` from `stage_status`.

## Stub runners

- `scripts/ops/run_shadow_daily.py`
- `scripts/ops/run_shadow_retrain_weekly.py`

These scripts only write protocol artifacts and stub JSON payloads so the ops
control surface can be integrated before any real execution logic is connected.
