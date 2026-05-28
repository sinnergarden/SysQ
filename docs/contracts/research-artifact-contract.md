# Research Artifact Contract

## Purpose

Define the minimum schema, manifest fields, and recommended directory
layout for all research artifacts in Qsys Framework Stable 2.0. This
contract governs what every artifact must carry, so that downstream
consumers (evaluators, backtest runners, report generators) can rely on
consistent fields.

---

## 0. Recommended Directory Layout

```
data/research/
  labels/
    <label_id>/
      manifest.json
      labels.parquet

  models/
    <model_id>/
      <model_version>/
        manifest.json
        model.bin
        train_metrics.json

  signals/
    <signal_id>/
      <signal_run_id>/
        manifest.json
        predictions.parquet
        eval/
          summary.json
          ic_daily.parquet
          group_returns.parquet

  strategies/
    <strategy_template_id>/
      manifest.yaml

  backtests/
    <strategy_run_id>/
      <backtest_id>/
        manifest.json
        daily_summary.parquet
        orders.parquet
        fills.parquet
        positions.parquet
        metrics.json

  experiments/
    <experiment_id>/
      manifest.json
      signal_eval_index.parquet
      backtest_index.parquet
      signal_rankings.parquet
      strategy_rankings.parquet
      summary.md
```

---

## 1. FeatureSet

A named, versioned collection of feature definitions used for model
training and inference.

**Minimum manifest fields** (JSON or YAML):

```yaml
feature_set_id: str            # unique name, e.g. "csi300_daily_v3"
version: str                   # semver or date, e.g. "2026-05-01"
description: str
universe: str                  # "csi300" | "csi800" | list
features:
  - name: str
    source: str                # e.g. "qlib://$close", "custom://alpha_v1"
    dtype: str                 # "float64", "int64", "category"
```

**Not stored**: Feature values. A FeatureSet is a schema + definition,
not a data store.

---

## 2. Label

A named, versioned label definition plus its computed values.

**LabelSpec** (JSON sidecar, always paired with parquet):

```json
{
  "label_id": "forward_return_5d",
  "kind": "forward_return",
  "horizon": 5,
  "description": "5-day forward return"
}
```

**Required parquet columns** (per `check_label_schema.py`):

| Column | Type | Description |
|---|---|---|
| `trade_date` | str (YYYY-MM-DD) | Trading date of prediction point |
| `instrument` | str | Instrument code |
| `label_value` | float64 | Label value (forward return, binary, etc.) |
| `horizon` | int | Forward horizon in trading days |
| `label_id` | str | FK to LabelSpec |

---

## 3. ModelArtifact

A trained model and its metadata.

**Minimum manifest**:

```yaml
model_id: str                  # unique, e.g. "alpha_v1_20260525"
model_type: str                # e.g. "xgboost", "lightgbm", "linear"
feature_set_id: str            # FK to FeatureSet
label_id: str                  # FK to LabelSpec
training_range:                # training data window
  train_start: str
  train_end: str
metrics:                       # evaluation on held-out period
  validation_range:
    start: str
    end: str
  sharpe: float
  ic: float
  ric: float
```

---

## 4. RawSignal

Model output before any expression or blending. One row per (date, instrument).

### signal_id vs signal_run_id

**signal_id** identifies the signal **definition / recipe**.

Examples:
- `lgbm_raw_return_5d_v1`
- `lgbm_industry_rank_5d_v1`
- `combo_raw_industry_tail_v1`

**signal_run_id** identifies one concrete generated **prediction artifact**.

Examples:
- `rolling_20210101_20260531_tw504_step5_a13f9c`
- `fixed_20260531_pred_20210101_20260531_a13f9c`
- `expr_20260601_a13f9c`

The same `model_id` + `feature_set_id` + `label_id` combination does
**not** uniquely identify one signal run, because different runs may
differ in:

- Rolling train window boundaries
- Prediction date range
- Model version (retrained)
- Random seed
- Data version
- Code commit
- Postprocess logic / expression
- Bug fixes (rerun)

**Every signal file must carry both fields.**

### Required parquet columns

| Column | Type | Description |
|---|---|---|
| `trade_date` | str (YYYY-MM-DD) | Trading date of prediction |
| `data_date` | str (YYYY-MM-DD) | Data cutoff used for prediction |
| `instrument` | str | Instrument code |
| `signal_id` | str | FK to signal definition |
| `signal_run_id` | str | FK to the run that produced this artifact |
| `score` | float64 | Model output score |

**Constraint**: `data_date <= previous_trading_day(trade_date)`.

---

## 5. DerivedSignal / SignalExpression

A transformation of one or more RawSignals.

**SignalExpression record**:

```yaml
expression_id: str
expression: str               # formula string
input_signal_ids: [str]       # FK to RawSignal or DerivedSignal
parameters: {str: any}
```

**Output parquet**: Same schema as RawSignal, with `signal_id` set to
the `expression_id` and `signal_run_id` set to the expression run ID.

---

## 6. StrategyTemplate

A reusable strategy configuration, not a run.

**Manifest**:

```yaml
strategy_template_id: str
description: str
portfolio_fn: str              # reference to registered function
parameters:
  top_n: int
  buffer_hold: int
  buffer_buy: int
  single_stock_cap: float
```

---

## 7. StrategyRun

A single execution of a strategy on a specific date range.

**Run manifest** (inherits from `RunManifest`):

```json
{
  "run_id": "YYYYMMDD_<hex>",
  "run_name": "...",
  "created_at": "ISO-8601",
  "model_id": "str",
  "feature_set_id": "str",
  "label_id": "str",
  "strategy_template_id": "str",
  "universe": "csi300",
  "date_range": { "start": "...", "end": "..." }
}
```

---

## 8. BacktestRun

A **backtest** is one atomic account simulation over a date range from
one fully specified set of signal inputs.

- It owns `daily_summary`, `orders`, `fills`, `positions`, `metrics`.
- It should be independently reproducible and queryable.
- No rolling training occurs during backtest — training is the upstream
  caller's responsibility.
- Input is cached signals (parquet), not live model inference.
- `model_id` in manifest records which model generated the signals.

**Manifest**:

```json
{
  "backtest_id": "alpha_v1_base_tw504_step5_v2",
  "backtest_type": "fixed_model" | "rolling_train",
  "signal_run_id": "str",
  "model_id": "str | null",
  "slippage": 0.001,
  "date_range": { "start": "...", "end": "..." }
}
```

---

## 9. Experiment

A **research collection / study batch** that groups multiple SignalRuns,
SignalEvaluations, StrategyRuns, and BacktestRuns under one research
question.

- An Experiment does **not** duplicate all backtest data — it references
  backtests and signal evals through index files.
- It owns comparison tables and research conclusions.

Example:

```
alpha_v1_strategy_variants_20260601  (Experiment)
  references:
    - alpha_v1_base.backtest          (BacktestRun)
    - alpha_v1_cap5.backtest
    - alpha_v1_top30_cap5.backtest
    - alpha_v1_top_heavy.backtest
```

```
alpha_v2_signal_tournament_20260601   (Experiment)
  references:
    - alpha_v2_models                 (SignalRun)
    - alpha_v2_combo                  (SignalRun)
    - benchmark_base                  (BacktestRun)
    - signal_rankings (index)
    - strategy_rankings (index)
```

**Manifest**:

```json
{
  "experiment_id": "alpha_v1_strategy_variants_20260601",
  "description": "Compare 8 strategy variants on alpha_v1 signals",
  "created_at": "ISO-8601",
  "signal_run_ids": ["..."],
  "backtest_ids": ["..."],
  "conclusion": "split_5d20d selected as primary candidate"
}
```

---

## 10. EvaluationReport

A structured evaluation result from comparing signals to labels.

**Output fields** at minimum:

| Field | Description |
|---|---|
| `evaluation_id` | Unique run ID |
| `signal_id` | Evaluated signal |
| `signal_run_id` | Evaluated signal run |
| `label_id` | Reference label |
| `date_range` | Evaluation period |
| `ic` | Information coefficient |
| `rank_ic` | Rank IC |
| `sharpe` | Signal Sharpe |
| `annual_return` | Annualized return of long-short decile portfolio |
| `turnover` | Average decile turnover |
| `n_dates` | Number of trading dates evaluated |
| `n_instruments` | Average instruments per date |
