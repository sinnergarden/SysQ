# Artifact Contract

## 1. Purpose

This document defines the standard artifact layout and minimum schema for every
DailyRunner run. All strategies must produce artifacts that conform to these
schemas. ADR-007 defines the long-form dataclass contracts; this document
defines the per-file CSV / JSON schemas that every strategy's `build_plan()`,
`execute_plan()`, and `mark_to_market()` must satisfy.

---

## 2. Run Root Layout

Every DailyRunner stage creates a run directory with the following structure.
Not every mode produces every artifact.

```
run_root/
│
├── run_meta.json                         # Always created
│
├── predictions/                          # Preopen only
│   ├── predictions_<trade_date>.csv
│   └── predictions_<trade_date>.adr7.json
│
├── plan/                                 # Preopen only (when rebalancing)
│   ├── target_weights.csv
│   ├── order_intents.csv
│   ├── rebalance_audit.csv
│   ├── plan_meta.json
│   └── manifest.adr7.json
│
├── execution/                            # Postclose only (when executed)
│   ├── COMMITTING                        # Marker file (exists during staging)
│   ├── COMMITTED                          # Marker file (committed)
│   ├── staging/
│   │   ├── account_before.json
│   │   ├── positions_before.csv
│   │   ├── account_after.json
│   │   ├── positions_after.csv
│   │   ├── execution_summary.json
│   │   ├── ledger_rows.csv
│   │   └── ledger_payload.json
│   ├── account_before.json
│   ├── positions_before.csv
│   ├── account_after.json
│   ├── positions_after.csv
│   ├── execution_summary.json
│   ├── ledger_rows.csv
│   └── ledger_payload.json
│
├── mtm/                                  # Postclose only
│   ├── mtm_snapshot.json
│   └── stale_check.json
│
└── training_result.json                  # Train only
```

### Per-Mode Requirements

| Mode | Required Artifacts |
|------|-------------------|
| `train` | `training_result.json` |
| `preopen` | `run_meta.json`, `predictions/*`, `plan/*` (if rebalancing) |
| `postclose` | `run_meta.json`, `execution/*`, `mtm/*` (if executable) |
| `notify-only` | Reads existing artifacts, writes none |

---

## 3. Per-File Schemas

### 3.1. Predictions CSV (`predictions/predictions_<trade_date>.csv`)

**Required columns**:

| Column | Type | Description |
|--------|------|-------------|
| `trade_date` | string | Trade date (`YYYY-MM-DD`) |
| `instrument` | string | Instrument code |
| `score` | float | Prediction score |
| `model_name` | string | Model identifier |
| `mainline_object_name` | string | Mainline object name |

### 3.2. Target Weights CSV (`plan/target_weights.csv`)

**Required columns**:

| Column | Type | Description |
|--------|------|-------------|
| `trade_date` | string | Trade date |
| `instrument` | string | Instrument code |
| `score` | float | Prediction score |
| `rank` | int | Rank by score (1 = best) |
| `target_weight` | float | Target portfolio weight |
| `strategy_id` | string | Strategy identifier |
| `strategy_version` | string | Strategy/model version |
| `portfolio_method` | string | Portfolio construction method |
| `model_name` | string | Model identifier |
| `mainline_object_name` | string | Mainline object name |

### 3.3. Order Intents CSV (`plan/order_intents.csv`)

**Required columns**:

| Column | Type | Description |
|--------|------|-------------|
| `trade_date` | string | Trade date |
| `instrument` | string | Instrument code |
| `side` | string | `buy` / `sell` |
| `target_weight` | float | Target weight for this instrument |
| `current_weight` | float | Current weight |
| `target_value` | float | Target value |
| `current_value` | float | Current value |
| `diff_value` | float | Difference (target − current) |
| `requested_qty` | int | Quantity requested (always positive) |
| `reason` | string | Intent reason (`rebalance_to_target_weight`, etc.) |

### 3.4. Rebalance Audit CSV (`plan/rebalance_audit.csv`)

**Required columns**:

| Column | Type | Description |
|--------|------|-------------|
| `trade_date` | string | Trade date |
| `instrument` | string | Instrument code |
| `score` | float | Prediction score |
| `target_weight` | float | Target weight |
| `current_weight` | float | Current weight |
| `target_value` | float | Target value |
| `current_value` | float | Current value |
| `diff_value` | float | Difference |
| `requested_qty` | int | Requested quantity |
| `action` | string | Action taken (`buy`, `sell`, `hold`, `skip`) |
| `reason` | string | Reason for the action |

### 3.5. Plan Meta JSON (`plan/plan_meta.json`)

**Required fields**:

| Field | Type | Description |
|-------|------|-------------|
| `trade_date` | string | Trade date |
| `reference_date` | string or null | Data reference date |
| `strategy_id` | string | Strategy identifier |
| `strategy_version` | string | Strategy/model version |
| `portfolio_method` | string | Portfolio construction method |
| `top_n` | int | Top-N selection count |
| `buffer_hold` | int | Hold buffer rank |
| `buffer_buy` | int | Buy buffer rank |
| `single_stock_cap` | float | Single stock cap |
| `cash_before` | float | Cash before rebalance |
| `market_value_before` | float | Market value before |
| `total_value_before` | float | Total value before |
| `buy_count` | int | Number of buy orders |
| `sell_count` | int | Number of sell orders |
| `total_orders` | int | Total order count |
| `build_ts` | string | Build timestamp |

### 3.6. Execution Summary JSON (`execution/execution_summary.json`)

**Required fields**:

| Field | Type | Description |
|-------|------|-------------|
| `trade_date` | string | Trade date |
| `run_id` | string | Run ID |
| `status` | string | `success` / `failed` / `skipped` |
| `strategy_id` | string | Strategy identifier |
| `strategy_version` | string | Strategy/model version |
| `portfolio_method` | string | Portfolio construction method |
| `order_count` | int | Total orders |
| `buy_count` | int | Buy order count |
| `sell_count` | int | Sell order count |
| `filled_count` | int | Filled order count |
| `rejected_count` | int | Rejected order count |
| `cash_before` | float | Cash before execution |
| `cash_after` | float | Cash after execution |
| `market_value_before` | float | Market value before |
| `market_value_after` | float | Market value after |
| `total_value_before` | float | Total value before |
| `total_value_after` | float | Total value after |
| `turnover` | float | Total turnover |
| `no_real_orders` | bool | True for shadow/non-live runs |

### 3.7. MTM Snapshot JSON (`mtm/mtm_snapshot.json`)

**Required fields**:

| Field | Type | Description |
|-------|------|-------------|
| `trade_date` | string | Trade date |
| `account_id` | string | Account identifier |
| `cash` | float | Cash balance |
| `market_value` | float | Market value of positions |
| `total_value` | float | Total (cash + market_value) |
| `cumulative_pnl` | float | Cumulative P&L |
| `cumulative_pnl_pct` | float | Cumulative P&L percentage |
| `daily_pnl` | float | Daily P&L |

### 3.8. Training Result JSON (`training_result.json`)

**Required fields**:

| Field | Type | Description |
|-------|------|-------------|
| `strategy_id` | string | Strategy identifier |
| `model_version` | string | Model version |
| `model_dir` | string | Model directory path |
| `status` | string | `success` / `failed` |
| `metrics` | object | Training metrics (strategy-specific) |
| `artifacts` | object | Discovered artifact paths |

---

## 4. Replay Rules

When comparing replay outputs between baseline and candidate:

### Allowed Metadata-Only Differences

- `timestamps` (e.g. `created_at`, `updated_at`, `build_ts`)
- `git_commit` values
- `run_id` (if explicitly different)
- Age-related metrics that depend on `datetime.now()`

### Must Be Byte-Identical

- All trading-critical numeric fields
- All instrument lists
- All order quantities and prices
- All weight values
- All status fields
- All reason fields

### Non-Determinism Note

`OrderGenerator.generate_orders` uses Python `set()` iteration
(`all_symbols = set(...) | set(...)`), which depends on `PYTHONHASHSEED`.
Within the same hash seed, output is reproducible. Across different seeds,
ledger row write order may differ → ≤1e-10 floating-point cash differences.

For deterministic replay comparison, use `PYTHONHASHSEED=0`.
