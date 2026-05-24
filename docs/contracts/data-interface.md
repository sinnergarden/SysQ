# Data Interface Contract

## 1. Core Principle

**No future data leakage.** Every data access must be bounded by a cutoff date
that is explicit and appropriate for the context. Daily inference, training,
evaluation, and backtest must each state their data cutoff.

---

## 2. Date Semantics

| Date | Definition | Used In |
|------|------------|---------|
| `trade_date` | The calendar trading day being processed. The "as of" date. | All stages |
| `data_date` | The last date for which feature / price data is available. Usually ≤ `trade_date`. | Preopen |
| `reference_date` | When the plan was built (data observable at plan time). Usually = `data_date`. | Plan meta |
| `execution_date` | When the plan is executed. Usually = `trade_date`. | Postclose |
| `label_end_date` | The last date included in forward-return label computation. Must be > `trade_date`. | Training |
| `train_start` / `train_end` | Training window bounds. Historical only. | Training |
| `valid_start` / `valid_end` | Validation window bounds. ≤ `train_end` for walk-forward. | Training |

### Rules

- **Preopen**: Uses data available *before* the trading day opens. The data cutoff
  is typically the previous trading day's close.
- **Postclose**: Uses the execution day's open prices for fill simulation and
  close prices for MTM. This is legitimate — the execution "happens" at open and
  the snapshot is taken at close.
- **Backtest**: Must explicitly model when each datum would have been observable.
  Using a date-ordered loop with expanding window is required.

### Calendar semantics (``qsys.data.calendar``)

Framework default: **asof** mode — a trading day resolves to itself; a
non-trading day (weekend / holiday) rolls back to the previous trading day.

| Mode | Semantics | Example |
|------|-----------|---------|
| ``"asof"`` (default) | Latest trading day ≤ *trade_date* | ``resolve_data_date("2026-05-18") == "2026-05-18"`` |
| ``"previous"`` | Latest trading day < *trade_date* | ``resolve_data_date("2026-05-18", mode="previous") == "2026-05-15"`` |

Strategies should **not** override ``resolve_data_date`` unless they have
explicitly documented data-availability constraints that differ from the
framework default (e.g. delayed data feed).  Use
``BaseStrategyAdapter`` (from ``qsys.strategy.runtime_base``) to inherit
the default implementation.

---

## 3. Market Snapshot Contract

Public API: `qsys.ops.market_snapshot.fetch_market_snapshot`

### Signature

```python
def fetch_market_snapshot(
    trade_date: str,
    instruments: list[str],
    price_col: str = "close",
) -> tuple[dict[str, float], pd.DataFrame]:
```

### Return

| Return Value | Type | Description |
|---|---|---|
| `current_prices` | `dict[str, float]` | `{instrument → price}` for every requested instrument |
| `market_status` | `pd.DataFrame` | Indexed by instrument. Columns: `is_suspended`, `is_limit_up`, `is_limit_down` |

### Rules

- **Instrument list must be explicit**: the caller provides the full list.
  The function must not add or remove instruments.
- **Missing data**: must raise `ShadowRebalanceError`, not silently fill with 0 or NaN.
- **Price column**: must be `"close"` or `"open"` unless explicitly extended.
- **Market status**: every instrument in the input list must have a status row.
  If an instrument is missing from qlib data, it should be treated as suspended.

---

## 4. Feature Frame Contract

The `FeatureProvider` abstraction is not fully extracted yet (see §7). The
intended future API is documented here as a contract target.

### Intended Signature

```python
def get_feature_frame(
    feature_set: str,
    universe: str | list[str],
    end_date: str,
    lookback: int,
    fields: list[str] | None = None,
) -> pd.DataFrame:
```

### Rules

- `feature_set` must be versioned or named. A strategy references feature config
  by name; the feature provider resolves the config.
- Output must include `instrument` and `datetime` / `trade_date` columns.
- No rows after `end_date`. The provider must enforce the cutoff.
- Missing value handling must be documented:
  - Strategy-owned: each strategy decides how to handle NaN features.
  - Feature-provider-owned: the provider documents its NaN policy.
- `schema_version` should be visible in artifacts for audit.

### Current Implementation

Currently, each strategy calls `QlibAdapter.get_features()` directly.
Standard call pattern:

```python
adapter = QlibAdapter()
adapter.init_qlib()
frame = adapter.get_features(universe, feature_config,
                              start_time=trade_date, end_time=trade_date)
```

---

## 5. Label Contract

- Labels are **only for training and evaluation**. They must never enter
  inference feature data.
- Forward-return labels must ensure `label_end_date > trade_date`, but this
  data is only usable in training *after* the full window is historically known
  (i.e., `label_end_date ≤ datetime.now()` when training runs).
- Label computation is strategy-owned. The training pipeline provides the
  label config, but each strategy may compute labels differently.

---

## 6. Data Cutoff Rule

When running replay, backtest, or any historical simulation:

**The system must simulate observable data at each date.**
It must not read future features or future labels when generating predictions
for a given `trade_date`.

### Implementation Rules

1. Training must accept an explicit `--end-date` parameter (or equivalent).
2. Without `--end-date`, the default must be `datetime.now()`, safe for
   production but dangerous for replay.
3. Replay must fail if `--end-date` is not explicitly set for training.
4. Inference must use the data cutoff appropriate for the date being processed.
5. Feature engineering must not use price/returns data from after the cutoff.

---

## 7. Known Current Limitations

### 7.1. QlibAdapter Direct Usage

Currently, `QlibAdapter` is used directly by strategy adapters in some paths.
This couples strategies to qlib's data model and initialization. The intended
evolution is:

1. Extract a `FeatureProvider` interface that wraps qlib.
2. Each strategy references features by `feature_set` name.
3. DailyRunner injects the feature provider (or the strategy creates one).

### 7.2. FeatureProvider Not Yet Extracted

The `get_feature_frame()` API in §4 is aspirational. Current strategy adapters:

- `alpha_v1`: Uses `QlibAdapter.get_features()` with the mainline feature config.
- `alpha_v2`: Uses `QlibAdapter.get_features()` with momentum features.

### 7.3. `OrderGenerator` Non-Determinism

`OrderGenerator.generate_orders` builds
`all_symbols = set(account.positions.keys()) | set(target_weights.keys())`,
then sorts by side (sells first) but relies on Python set iteration for
within-side ordering. Python's hash seed randomization (`PYTHONHASHSEED`)
causes different iteration orders across separate processes.

**Mitigation**: Run comparisons with `PYTHONHASHSEED=0`.

**Follow-up**: Sort `all_symbols` before iteration for deterministic ordering.
