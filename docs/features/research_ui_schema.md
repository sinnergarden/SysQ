# Research UI Stable Schema

This document defines the stable contract layer for the Research UI / Debug Cockpit.

The goal is to align these source layers without leaking their file layout directly to the front-end:
- daily ops evidence under `daily/{date}`
- qlib / feature state under `data/raw` and `data/qlib_bin`
- model predictions and backtest reports under `experiments/`
- trade and position state in sqlite ledgers

Implementation lives in:
- `qsys/research_ui/schema.py`
- `qsys/research_ui/assembler.py`

## Contract Rules

- All aggregate and detail pages must be keyed by stable ids, not file paths.
- All core objects must be traceable by `run_id`, `trade_date`, and `instrument_id` where relevant.
- Raw and fq price mode must be explicit in case-oriented payloads.
- Daily ops, feature health, model prediction outputs, and trade records are normalized into one contract layer before they reach API or UI.
- New cockpit features should extend this schema layer first, then API, then UI.
- API responses should use a stable envelope: `api_version`, `meta`, and either `data` or `items`.
- Query context used to build a payload should be echoed in `meta` rather than inferred by the caller.
- Front-end code may consume `items` / `data`, but should not depend on raw artifact paths or undocumented side fields.

## Core Objects

### RunManifest
Unified manifest for daily ops, feature runs, backtests, decision replay, and case bundles.

Fields:
- `run_id`
- `run_type`
- `status`
- `signal_date`
- `execution_date`
- `trade_date`
- `created_at`
- `updated_at`
- `account_name`
- `model_info`
- `data_status`
- `scope`
- `warnings`
- `blockers`
- `notes`
- `artifacts[]`
- `links`

### RunArtifactRef
Logical reference to an artifact.

Fields:
- `artifact_id`
- `kind`
- `logical_path`
- `title`
- `media_type`
- `stage`

### FeatureRegistryEntry
Stable feature lookup entry for Feature Health and Case Workspace.

Fields:
- `feature_id`
- `feature_name`
- `display_name`
- `group_name`
- `source_layer`
- `dtype`
- `value_kind`
- `description`
- `formula`
- `dependencies[]`
- `supports_snapshot`
- `tags[]`
- `status`

### FeatureHealthSummary
Aggregate feature health result for one trade date / universe scope.

Fields:
- `run_id`
- `trade_date`
- `universe`
- `price_mode_context`
- `feature_count`
- `instrument_count`
- `overall_missing_ratio`
- `features[]`
- `warnings[]`
- `blockers[]`
- `manifest_ref`

### BacktestRunSummary
Stable backtest run contract for list/detail pages.

Fields:
- `run_id`
- `run_type`
- `model_name`
- `feature_set`
- `universe`
- `train_range`
- `test_range`
- `top_k`
- `price_mode`
- `metrics`
- `artifacts[]`
- `manifest_ref`

### DecisionReplay
Stable调仓解释 contract.

Fields:
- `run_id`
- `trade_date`
- `signal_date`
- `execution_date`
- `account_name`
- `previous_positions[]`
- `candidate_pool[]`
- `scored_candidates[]`
- `constraints`
- `selected_targets[]`
- `final_orders[]`
- `exclusions[]`
- `summary`
- `manifest_ref`

### CaseBundle
Single instrument research bundle for Case Workspace.

Fields:
- `case_id`
- `run_id`
- `instrument_id`
- `trade_date`
- `signal_date`
- `execution_date`
- `price_mode`
- `bars[]`
- `signal_snapshot`
- `feature_snapshot`
- `orders[]`
- `positions[]`
- `annotations[]`
- `links[]`

## Source Alignment

Current alignment policy in the assembler:
- daily ops manifest -> `RunManifest`
- feature registry groups -> `FeatureRegistryEntry`
- `inspect_qlib_data_health()` output -> `FeatureHealthSummary`
- `experiments/reports/backtest_*.json` -> `BacktestRunSummary`
- `order_intents_{execution_date}_{account}.json` + account history -> `DecisionReplay`
- bars + signal basket + feature snapshot + replay slice -> `CaseBundle`

## API Envelope

List endpoints should return:
- `api_version`
- `meta`
- `items[]`
- `count`

Detail endpoints should return:
- `api_version`
- `meta`
- `data`

Recommended `meta` fields:
- `resource`
- `run_id` when applicable
- `instrument_id` when applicable
- `trade_date` / `execution_date` when applicable
- query filters such as `feature_names`, `price_mode`, `limit`

## Forward Compatibility Rule

Any new daily ops artifact, feature audit artifact, prediction artifact, or ledger table added for the cockpit should be mapped through `ResearchCockpitRepository` first.
Do not let front-end code bind directly to raw csv/json/sqlite layouts.
