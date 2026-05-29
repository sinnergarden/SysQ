# Real Cross Matrix Research User Guide

## 1. What This Workflow Is For

The real cross matrix research workflow runs a **matrix experiment** through
the full Framework Stable 2.0 research pipeline:

- Runs multiple real signal generators (model-based, data-derived).
- Applies signal transforms (normalisation, z-score).
- Combines multiple transformed signals into a new derived SignalRun.
- Evaluates all signals against labelled data (IC, RankIC, ICIR).
- Backtests all final SignalRuns against multiple strategy configs.
- Collects every artifact reference into an ExperimentIndex.

**Important design constraints:**

- Strategy/backtest still consumes **one final SignalRun**.  Cross-signal
  combination happens in the signal layer, before the strategy layer.
- This is **not** an AutoML / grid search system.  It is a controlled
  framework boundary validation: proving that generators, transforms,
  combinations, and strategies can be swapped independently without
  changing the other layers.

---

## 2. Mental Model

```
real generators
  -> transformed SignalRuns
  -> combined SignalRuns
  -> SignalEvaluator
  -> BacktestRunner.run_from_signal_cache
  -> ExperimentIndex
  -> DuckDB query helper
```

**Layer responsibilities:**

| Layer | Role |
|---|---|
| **generator** | Produces a raw signal from a model or data source.  Must return `(trade_date, data_date, instrument, signal_id, signal_run_id, score)`.  `data_date` must be the previous trading day before `trade_date`. |
| **transform** | Normalises or reshapes one signal.  Example: `daily_zscore` computes cross-sectional z-score per trade_date. |
| **combination** | Combines multiple transformed signals into a new SignalRun.  Uses an indexed join (default: inner join) on `(trade_date, data_date, instrument)`. |
| **strategy** | Converts one final signal into target weights and runs a backtest. |
| **experiment** | Collects all signal-run, evaluation, and backtest references into indexed CSVs and a summary. |

---

## 3. How to Run

```bash
python scripts/research/run_rolling_research.py \
  --config configs/research/real_cross_matrix_smoke.yaml \
  --root data/research \
  --overwrite-all
```

The current smoke config (`real_cross_matrix_smoke.yaml`) uses:

| Axis | Values |
|---|---|
| Calendar | 2026-05-18 to 2026-05-22, train_window_days=100, predict_window_days=2 |
| Generators | `alpha_v1_existing`, `technical_composite` |
| Transforms | `raw` (identity), `daily_zscore` |
| Combination | `blend_alpha_tech_70_30` (linear blend, 0.7 alpha + 0.3 tech) |
| Strategies | `top20` (rank_weight_top20), `top50_capped` (rank_weight_top50_capped, max_weight=0.03) |
| Labels | `forward_return_5d` |

**Expected output summary:**
```
status=passed
generator_count=2
transform_count=2
combination_count=1
strategy_count=2
signal_run_count=5   (4 base + 1 combined)
signal_eval_count=5
backtest_count=10
```

---

## 4. Output Directory Structure

### Experiment directory

```
data/research/experiments/real_cross_matrix_smoke/
  manifest.json                 # experiment metadata
  rolling_research_manifest.json  # full run manifest (mode, counts, matrix_purpose)
  rolling_windows.csv            # rolling window definitions
  matrix_jobs.csv                # every (generator, transform, strategy) job with status
  cross_signal_index.csv         # combined signal lineage
  signal_run_refs.csv            # references to saved SignalRuns
  signal_eval_refs.csv           # references to signal evaluations
  backtest_refs.csv              # references to backtest runs
  signal_run_index.csv           # resolved signal-run index table
  signal_eval_index.csv          # resolved eval metrics (IC, RankIC, ICIR)
  backtest_index.csv             # resolved backtest metrics (total_return, turnover, etc.)
  summary.md                     # human-readable summary
```

### Signal artifacts

```
data/research/signals/<signal_id>/<signal_run_id>/
  predictions.parquet
  manifest.json
  combination_manifest.json        # only for combined signals
```

### Backtest artifacts

```
data/research/backtests/<strategy_run_id>/<backtest_id>/
  manifest.json
  metrics.json
  daily_summary.csv
```

---

## 5. How to Read Results

### summary.md (quick human-readable report)

Opened in any Markdown viewer.  Shows signal runs, evaluation metrics
(sorted by RankICIR), and backtest results (sorted by total return).

### signal_eval_index.csv (IC / RankIC / coverage)

```csv
signal_id,rank_icir,icir,n_obs,n_days,coverage_mean
```

Higher `rank_icir` means the signal consistently predicts forward returns.
Negative values indicate the signal is anti-correlated with realised returns.

### backtest_index.csv (total_return / turnover / final_value)

```csv
signal_id,strategy_template_id,total_return,final_value,avg_turnover
```

Use `total_return` to compare strategy performance across signals.
`avg_turnover` indicates how often the portfolio rebalances.

### matrix_jobs.csv (every signal-strategy job)

Every row is one (generator x transform x strategy) combination with
`status=completed` or `status=failed`.  Combined signals appear with
`generator_id=<combine_id>` and `transform_id=combined`.

### cross_signal_index.csv (combined signal lineage)

Shows the input signals, weights, and output signal for each combination.
Use this to verify that combinations are sourcing the correct transformed
signals.

### combination_manifest.json (detailed combination metadata)

Located at `signals/<output_signal_id>/<output_signal_run_id>/combination_manifest.json`.
Contains:

- `combine_id`, `combine_type`
- `join_policy` — default `"inner"` (only rows covered by all inputs)
- `input_row_counts` — row count of each input SignalRun
- `output_row_count` — row count after join
- `dropped_by_join` — rows lost due to non-overlapping instruments/dates
- `date_range` — min/max trade_date in the combined result

---

## 6. How to Query with DuckDB

### Default query

Joins `signal_eval_index` and `backtest_index` by `(signal_id, signal_run_id)`,
ordered by `total_return` descending:

```bash
python scripts/research/query_experiment_duckdb.py \
  --experiment-dir data/research/experiments/real_cross_matrix_smoke
```

### Custom SQL

```bash
python scripts/research/query_experiment_duckdb.py \
  --experiment-dir data/research/experiments/real_cross_matrix_smoke \
  --sql "
select
  b.signal_id,
  b.signal_run_id,
  e.rank_icir,
  b.total_return,
  b.turnover_total
from backtest_index b
left join signal_eval_index e
  on b.signal_id = e.signal_id
 and b.signal_run_id = e.signal_run_id
order by b.total_return desc
limit 20
"
```

Available views are loaded automatically from CSV files in the experiment
directory: `signal_run_index`, `signal_eval_index`, `backtest_index`,
`matrix_jobs`, `cross_signal_index`.

---

## 7. How to Add a New Generator

**Minimal steps:**

1. **Implement** the generator in `qsys/research/generators/`.
   - Must return a DataFrame with columns:
     `trade_date`, `data_date`, `instrument`, `signal_id`, `signal_run_id`, `score`
   - `data_date` must be the previous trading day before `trade_date`
     (never same-day, never weekend).
   - Must conform to the `RollingSignalGenerator` protocol (see `base.py`).

2. **Register** the generator type in `_create_generator_from_config`
   in `rolling_runner.py`.

3. **Add** it to the YAML config:

```yaml
generators:
  - generator_id: my_new_gen
    type: my_generator_type
    params:
      param1: value1
```

---

## 8. How to Add a New Signal Combination

### Supported combination types

| Type | Behaviour |
|---|---|
| `linear_blend` | Weighted sum of input scores, normalised by total weight |
| `equal_weight` | Equal-weighted average of input scores |
| `confirm_filter` | Primary score kept where secondary score > 0, otherwise primary score * 0.5 |

### Join policy

Default: **`inner`** — only rows covered by **all** input signals are kept.
Rows with non-overlapping instruments or trade dates are dropped.

Optional: `outer_zero_fill` — outer join with `fillna(0)` for missing scores.
Use with caution; it creates artificial coverage.

### Example YAML

```yaml
signal_combinations:
  - combine_id: blend_alpha_tech_70_30
    type: linear_blend
    inputs:
      - source_generator_id: alpha_v1
        source_transform_id: daily_zscore
        weight: 0.7
      - source_generator_id: tech_comp
        source_transform_id: daily_zscore
        weight: 0.3
```

The `source_generator_id` and `source_transform_id` point to an existing
generator + transform pair in the same config.  The framework resolves
them to the actual `signal_id` and `signal_run_id` automatically.

---

## 9. Common Mistakes

- **Do not** let a strategy consume multiple raw signals directly.
  Combination should happen at the signal layer, producing one final SignalRun.

- **Do not** use same-day `data_date` for preopen-style signals.
  Always enforce `data_date < trade_date` (previous trading day).

- **Do not** use `outer_zero_fill` unless you are certain you want missing
  signals treated as zero.  The default `inner` join is safer.

- **Do not** compare raw scores across generators without normalisation.
  Use `daily_zscore` or similar transforms before combining.

- **Check** `cross_signal_index.csv` before trusting combined results.
  Verify that the correct input signals are being combined.

- **Check** `dropped_by_join` in `combination_manifest.json`.
  A high drop count may indicate instrument universe mismatch between
  input signals.

---

## 10. File Locations

| File | Purpose |
|---|---|
| `qsys/research/rolling_runner.py` | Orchestrator: v1 single-signal + v2 matrix |
| `qsys/research/generators/` | Signal generator implementations |
| `qsys/research/signal_combine.py` | Signal combination (join + blend) |
| `configs/research/real_cross_matrix_smoke.yaml` | Real 2x2x2+1 smoke config |
| `scripts/research/run_rolling_research.py` | CLI entry point |
| `scripts/research/query_experiment_duckdb.py` | DuckDB query helper |
| `docs/research/real-cross-matrix-guide.md` | This guide |
