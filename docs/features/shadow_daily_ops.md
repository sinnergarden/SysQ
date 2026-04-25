# Shadow Daily Ops

This document defines the PR1 protocol shell, the PR2 weekly retrain wiring, the PR3A daily latest-model inference loop, and the PR3B shadow rebalance + ledger minimum loop.

## Scope

- Daily runner now consumes `models/latest_shadow_model.json`, performs lightweight data/readiness checks, runs inference, and writes shadow rebalance artifacts.
- Weekly retrain calls real training through `scripts/run_train.py`.
- Daily runner still does not retrain, touch MiniQMT, place real orders, call `update_data_all.py`, or run `BacktestEngine.run()`.
- Shadow rebalance is paper-only: it writes intents, account state, positions, and ledger updates under `shadow/`.
- No systemd, UI, or research-framework changes are part of this flow.

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

## Daily runner semantics

The daily entrypoint is `scripts/ops/run_shadow_daily.py`.

Behavior:

- `data_sync` is a lightweight freshness/environment check only; it does not call heavy update scripts
- `data_sync=failed` forces `select_model` / `inference` / `shadow_rebalance` to `skipped`, writes failed `04_inference/inference_summary.json` and `05_shadow/execution_summary.json`, and does not modify persistent `shadow/` state
- `feature_refresh` is a lightweight readiness check only; it does not materialize features in bulk
- `feature_refresh=failed` or blocked forces the same downstream skip-and-freeze behavior; `extended_warn` may continue
- `maybe_retrain` is always `skipped` by design; daily runner still does not train
- `select_model` reads `models/latest_shadow_model.json` via `read_latest_shadow_model()` and gates execution with `latest_shadow_model_is_usable()`
- a latest model pointer is loadable only when required fields exist, `status == "success"`, `model_path` is a directory, and `config_snapshot.json`, `training_summary.json`, `decisions.json`, `meta.yaml`, and `model.pkl` all exist
- when no usable latest model exists, `select_model=failed`, `inference=skipped`, `shadow_rebalance=skipped`, and the daily run finishes `failed`
- when inference fails after model selection, `shadow_rebalance=skipped`, `05_shadow/execution_summary.json` is still written with `status=failed`, and the daily run finishes `failed`
- when inference succeeds, the runner writes `03_model/selected_model.json`, `04_inference/predictions.csv`, `04_inference/inference_summary.json`, then runs shadow rebalance
- shadow rebalance is paper-only: it generates target weights, order intents, matched shadow fills, and persistent shadow state; it never sends real orders
- shadow execution currently uses end-of-day mark prices from the inference trade date and records `price_mode=shadow_mark_price`
- successful shadow rebalance finishes with `shadow_rebalance=success`, `decision_status=shadow_rebalanced`, and `overall_status=success`
- failed shadow rebalance finishes with `shadow_rebalance=failed`, `decision_status=failed`, and `overall_status=failed`

## Shadow state contract

Persistent shadow state lives under `shadow/`:

- `shadow/account.json`
- `shadow/positions.csv`
- `shadow/ledger.csv`

`shadow/account.json` minimum fields:

- `trade_date`
- `cash`
- `total_value`
- `market_value`
- `available_cash`
- `last_run_id`

`shadow/positions.csv` minimum fields:

- `instrument`
- `quantity`
- `cost_price`
- `last_price`
- `market_value`

The implementation also persists `sellable_quantity` so the next shadow run can reuse the previous state.

`shadow/ledger.csv` minimum fields:

- `run_id`
- `trade_date`
- `instrument`
- `side`
- `quantity`
- `price`
- `amount`
- `fee`
- `status`
- `reason`

If no prior shadow state exists, the runner initializes paper capital with `1_000_000`.

## Shadow rebalance artifacts

Each successful daily rebalance writes under `05_shadow/`:

- `target_weights.csv`
- `order_intents.csv`
- `execution_summary.json`
- `account_after.json`
- `positions_after.csv`
- `06_notification/notification_result.json`

These CSV artifacts keep fixed headers even when a given run produces zero rows.

`target_weights.csv` minimum fields:

- `trade_date`
- `instrument`
- `score`
- `target_weight`
- `model_name`
- `mainline_object_name`
- `strategy_variant`

`order_intents.csv` minimum fields:

- `trade_date`
- `instrument`
- `side`
- `target_weight`
- `current_weight`
- `target_value`
- `current_value`
- `diff_value`
- `requested_qty`
- `reason`

`execution_summary.json` minimum fields:

- `trade_date`
- `run_id`
- `status`
- `strategy_variant`
- `top_k`
- `turnover_buffer`
- `price_mode`
- `order_count`
- `buy_count`
- `sell_count`
- `skipped_count`
- `filled_count`
- `rejected_count`
- `cash_before`
- `cash_after`
- `market_value_before`
- `market_value_after`
- `total_value_before`
- `total_value_after`
- `turnover`
- `notes`

Current minimum strategy settings are fixed for PR3B:

- `mainline_object_name=feature_173`
- `top_k=5`
- `strategy_variant=top5_equal_weight`
- `turnover_buffer=0.0`
- `rebalance_mode=daily`
- `price_mode=shadow_mark_price`

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

Daily runs now archive traceable artifact pointers for:

- `data_status_path`
- `feature_status_path`
- `selected_model_path`
- `predictions_path`
- `inference_summary_path`
- `execution_summary_path`
- `notification_result_path`
- `daily_summary_path`

Weekly retrain writes real artifact pointers for:

- `training_report_path`
- `training_summary_path`
- `config_snapshot_path`
- `decisions_path`
- `model_path`
- `latest_model_pointer_path`

## Summary contract

Daily runs write `daily_summary.json` with these fields:

- `trade_date`
- `run_id`
- `run_type`
- `overall_status`
- `data_status`
- `feature_status`
- `train_status`
- `model_used`
- `inference_status`
- `rebalance_status`
- `shadow_order_count`
- `filled_count`
- `rejected_count`
- `turnover`
- `cash_after`
- `total_value_after`
- `strategy_variant`
- `price_mode`
- `degradation_level`
- `decision_status`
- `notification_status`
- `notes`

Daily status semantics:

- no usable model -> `decision_status=failed`
- inference failed -> `decision_status=failed`
- rebalance failed -> `decision_status=failed`
- rebalance success -> `decision_status=shadow_rebalanced`

Weekly retrain still writes `daily_summary.json` so both runners share one summary artifact family.

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

## Notification config

Enterprise WeChat webhook config is read through `qsys.config.cfg`.
Lookup order:

- `ops.notification.wecom_webhook_url`
- `notification.wecom_webhook_url`
- legacy top-level `webhook_url`

If none is configured, notification is recorded as `skipped` and the daily/weekly run status is unchanged.

## Helper API

The protocol helper layer in `qsys/ops/` supports:

- initializing a run
- updating stage status
- finalizing a run
- atomically writing latest pointers
- reading and validating the latest shadow model pointer
- writing the latest shadow model pointer
- invoking the real weekly training flow
- invoking the daily latest-model inference flow
- running the paper-only shadow rebalance flow and persisting `shadow/` state
- sending a post-finalize Enterprise WeChat webhook notification without changing the run status

## Idempotency and re-entry

- A fixed `run_id` always resolves to the same run directory.
- Re-running with the same `run_id` rewrites protocol files in place.
- Latest pointers are updated with atomic replace.
- `finalize_run()` derives `overall_status` from `stage_status`.
- Weekly retrain updates `models/latest_shadow_model.json` only after successful training artifact validation.
- Daily rebalance reuses `shadow/account.json`, `shadow/positions.csv`, and appends to `shadow/ledger.csv`.

## Runners

- `scripts/ops/run_shadow_daily.py`
- `scripts/ops/run_shadow_retrain_weekly.py`

The daily runner now consumes the latest usable shadow model, writes traceable inference artifacts, produces shadow rebalance intents, updates the paper shadow ledger, then attempts a post-finalize Enterprise WeChat webhook notification without touching real brokerage paths.
The weekly retrain runner executes the real training path, archives traceable protocol artifacts for the run directory, and applies the same post-finalize notification rule.
