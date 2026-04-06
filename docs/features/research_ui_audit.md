# Research UI Audit

## Scope

Audit target for the planned Research UI / Debug Cockpit:

1. raw / fq bar data
2. instrument metadata
3. feature registry
4. feature run outputs
5. signal run outputs
6. backtest outputs
7. order / position ledger
8. run manifest

This audit only records capabilities that already exist in SysQ today. It does not assume notebook-only outputs or missing backfill logic.

## Current Findings

### 1. Raw / fq bar data

Status: partially available and usable.

Current sources:
- `qsys.data.storage.StockDataStore` stores raw per-instrument daily bars as feather under raw daily storage.
- `qsys.dataview.research.ResearchDataView` can load raw bars and derive forward-adjusted fields on read using `adj_factor`.
- `qsys.data.adapter.QlibAdapter` exposes qlib-ready daily fields and semantic features from `data/qlib_bin/`.

Observed characteristics:
- Raw bars are the long-term source of truth for per-instrument history.
- Forward-adjusted price is currently derived at query time rather than persisted as a separately versioned artifact.
- The cockpit can support `price_mode=raw|fq`, but the current system does not expose a stable API schema for that yet.

Gaps:
- No dedicated stable bar API contract exists.
- No stable instrument-date snapshot bundle exists for bar + signal + orders in one payload.
- Raw vs fq semantics are implicit in code, not explicit in JSON schema.

Risk / instability:
- FQ is computed lazily from `adj_factor`; if a row lacks `adj_factor`, fallback behavior depends on current code path.
- There is no explicit version tag for the price adjustment method.

### 2. Instrument metadata

Status: available.

Current sources:
- `data/meta.db` table `stock_basic`
- `data/meta.db` table `trade_cal`
- `data/meta/industry_map.json`
- qlib instrument files under `data/qlib_bin/instruments/*.txt`

Observed characteristics:
- Basic metadata exists: `ts_code`, `symbol`, `name`, `area`, `industry`, `market`, `list_date`.
- Trading calendar exists.
- Universe membership is partly represented through qlib instrument files.

Gaps:
- No unified `Instrument` JSON schema yet.
- No stable API that merges stock basic metadata, industry, universe membership, and listing status.

Risk / instability:
- Universe membership is currently inferred from qlib instrument files, not exposed through a typed contract.

### 3. Feature registry

Status: partially available.

Current sources:
- `qsys.feature.registry.FEATURE_GROUPS`
- `qsys.feature.config`
- model metadata such as `data/models/*/feature_selection.yaml`

Observed characteristics:
- Feature groups and group-level flags exist.
- There is a semantic feature vocabulary in code.
- There is no single persisted registry entry per feature with business meaning, source layer, dependencies, and expected value shape.

Gaps:
- Missing stable `FeatureRegistryEntry` schema.
- Missing persisted registry artifact for front-end lookup.
- Missing explicit mapping of feature -> source layer (`raw`, `derived`, `qlib-native`, `semantic-derived`).

Risk / instability:
- Registry currently reflects code constants, not a versioned contract.
- Feature naming is better than before, but some historical aliases still appear in reports and model metadata.

### 4. Feature run outputs

Status: partially available.

Current sources:
- `qsys.data.health.inspect_qlib_data_health()`
- qlib feature store under `data/qlib_bin/features/`
- tests validating semantic coverage and feature health

Observed characteristics:
- The system can compute feature health for a requested date.
- Data health output already includes useful fields: row count, column count, missing ratio, per-column missing ratios, PIT/margin warnings, alignment status.
- There is no persisted feature-run summary list for browsing historical runs.

Gaps:
- Missing stable feature run artifact list, run_id, and run manifest linkage.
- Missing persisted feature snapshot payload for instrument-date debugging.
- Missing stable feature health summary schema for the UI.

Risk / instability:
- Current health report is generated on demand and shaped as internal Python dataclass output.
- No durable feature-run index exists.

### 5. Signal run outputs

Status: available but split across several artifacts.

Current sources:
- `daily/{date}/pre_open/signals/signal_basket_{signal_date}.csv`
- `daily/{date}/pre_open/order_intents/order_intents_{execution_date}_{account}.json`
- `daily/{date}/pre_open/plans/plan_{signal_date}_{account}.csv`
- `daily/{date}/pre_open/reports/daily_ops_digest_{execution_date}.json`
- `daily/{date}/pre_open/manifests/daily_ops_manifest_{execution_date}.json`

Observed characteristics:
- Signal basket exists and is the closest thing to a case-workspace input.
- Order intents already carry execution assumptions and price basis metadata.
- Plans and order intents are separate artifacts; that is good for operations, but the research cockpit still needs a unified read model.

Gaps:
- Missing stable signal-run summary schema.
- Missing unified case bundle linking bars, feature snapshot, signal row, order intents, and replay context.

Risk / instability:
- Signal artifacts are still partly path-oriented and daily-package-oriented.
- Some daily digest/index content still references older archive semantics like `daily/ops/...`.

### 6. Backtest outputs

Status: available but too thin for cockpit drill-down.

Current sources:
- `experiments/reports/backtest_*.json`
- referenced daily result csv paths from report artifacts
- `qsys.reports.backtest.BacktestReport`

Observed characteristics:
- A backtest report exists with run-level performance summary.
- Current summary includes totals such as return, sharpe, max drawdown, trade count, and days.
- The existing report is enough for a run list and top-level summary.

Gaps:
- Missing stable `BacktestRunSummary` schema that front-end can rely on.
- Missing stable daily timeseries payload for equity / drawdown / turnover / IC / RankIC.
- Missing replay-grade daily detail that can link a backtest day to decisions and single-instrument case views.

Risk / instability:
- Existing artifacts reference file paths directly.
- Existing backtest report schema does not yet guarantee drill-down fields.
- Historical reports still include old model naming like phase123 / 158-era aliases.

### 7. Order / position ledger

Status: available.

Current sources:
- `data/trade.db` via `qsys.trader.database.TradeLedger`
- `data/meta/real_account.db` via `qsys.live.account.RealAccount`
- daily reconciliation csvs and snapshots

Observed characteristics:
- `trade.db` has `pipeline_runs`, `position_snapshots`, `orders`, `fills`, `daily_metrics`.
- `real_account.db` has `balance_history`, `position_history`, `trade_log`.
- This is enough to support position history, orders, fills, and reconciliation-oriented replay.

Gaps:
- Missing unified read model for previous positions / final orders / fills / exclusions by run_id + trade_date.
- Missing explicit decision replay artifact connecting candidate pool to final orders.

Risk / instability:
- There are two ledgers with overlapping semantics: production pipeline ledger and account-state ledger.
- Account names and run identifiers are not yet normalized into one front-end schema.

### 8. Run manifest

Status: available but fragmented.

Current sources:
- `qsys.live.ops_manifest.update_manifest()`
- `daily/{date}/pre_open/manifests/daily_ops_manifest_{execution_date}.json`
- `daily/{date}/snapshot_index.json`
- `qsys.reports.base.RunReport`

Observed characteristics:
- Daily ops manifest exists and already carries `execution_date`, `signal_date`, `stages`, `artifacts`, `data_status`, `model_info`, `blockers`, `notes`.
- Snapshot index exists and provides archived artifact lookup.
- Generic run reports exist for backtest and other workflows.

Gaps:
- Missing one stable manifest schema shared by ops, feature runs, signal runs, and backtests.
- Missing normalized artifact references that hide internal local paths from UI clients.

Risk / instability:
- There are at least three related but different structures: `RunReport`, daily ops manifest, and snapshot index.
- `snapshot_index.json` still shows older archive-root semantics not fully aligned with the current `DATA_LAYOUT.md` contract.

## Missing Content

Missing stable products required by the cockpit:
- persisted feature registry artifact for UI lookup
- persisted feature run list / feature snapshot artifact
- stable backtest daily summary payload with drill-down fields
- stable decision replay artifact
- stable case bundle artifact
- unified run manifest contract across workflows
- read-only API layer
- front-end application shell

## Unstable Content

Main unstable areas today:
- `snapshot_index.json` path semantics vs current `DATA_LAYOUT.md`
- daily archive references still expose raw internal paths
- feature health is an internal dataclass output, not a published schema
- backtest reports are run-level only and do not guarantee daily drill-down JSON
- raw/fq choice exists in code but is not a stable external contract
- ledger data is split across `trade.db`, `real_account.db`, and daily evidence packages

## Recommended Stable Schemas

These schemas should be added before the front-end MVP.

### FeatureRegistryEntry
One record per feature for UI lookup, filtering, and explanation.

Suggested semantics:
- `feature_id`
- `feature_name`
- `display_name`
- `group_name`
- `source_layer` (`raw`, `qlib_native`, `semantic_derived`, `daily_derived`)
- `dtype`
- `value_kind` (`scalar`, `boolean`, `category`, `series`)
- `description`
- `formula`
- `dependencies`
- `supports_snapshot`
- `tags`
- `status`

### FeatureHealthSummary
One run/date scoped health result suitable for aggregate pages.

Suggested semantics:
- `run_id`
- `trade_date`
- `universe`
- `price_mode_context`
- `feature_count`
- `instrument_count`
- `overall_missing_ratio`
- `features` with per-feature `coverage_ratio`, `nan_ratio`, `inf_ratio`, `status`
- `warnings`
- `blockers`
- `manifest_ref`

### BacktestRunSummary
One backtest run summary for list/detail pages.

Suggested semantics:
- `run_id`
- `run_type`
- `model_name`
- `feature_set`
- `universe`
- `train_range`
- `test_range`
- `top_k`
- `price_mode`
- `metrics` including `total_return`, `annual_return`, `sharpe`, `max_drawdown`, `turnover`, `ic`, `rank_ic`
- `artifacts`
- `manifest_ref`

### DecisionReplay
One trade-date scoped decision explanation payload.

Suggested semantics:
- `run_id`
- `trade_date`
- `signal_date`
- `execution_date`
- `account_name`
- `previous_positions`
- `candidate_pool`
- `scored_candidates`
- `constraints`
- `selected_targets`
- `final_orders`
- `exclusions`
- `summary`

### CaseBundle
One instrument + date bundle for the case workspace.

Suggested semantics:
- `case_id`
- `run_id`
- `instrument_id`
- `trade_date`
- `signal_date`
- `execution_date`
- `price_mode`
- `bars`
- `signal_snapshot`
- `feature_snapshot`
- `orders`
- `positions`
- `annotations`
- `links`

### RunManifest
One workflow-agnostic manifest layer used by API and UI.

Suggested semantics:
- `run_id`
- `run_type`
- `status`
- `signal_date`
- `execution_date`
- `trade_date`
- `created_at`
- `updated_at`
- `model_info`
- `data_status`
- `scope`
- `artifacts` as logical refs rather than local paths
- `warnings`
- `blockers`
- `links`

## Recommended Build Order

1. normalize and version the schemas above
2. add a read-only data-access layer that builds these schemas from existing artifacts
3. expose read-only APIs
4. build front-end pages on top of schema-driven APIs only
5. add decision replay and case drill-down links

## Key Constraint For Implementation

The cockpit should treat these as source layers, in this order:
- long-term source data: `data/raw`, `data/qlib_bin`, sqlite ledgers
- daily evidence: `daily/{date}`
- research outputs: `experiments/reports`
- normalized UI contracts: new schema + API layer

The front-end should never read CSV, feather, sqlite, or temporary notebook outputs directly.
