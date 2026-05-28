# Time Semantics Contract

## Purpose

Define the precise meaning of date fields used across Qsys to prevent
lookahead leakage, data-date confusion, and subtle timing bugs.

---

## 1. Core Definitions

### `trade_date`

The trading date a research run or production pipeline is **about**.
- Format: `YYYY-MM-DD`
- Always refers to a real trading day (non-holiday, non-weekend).
- A run for `trade_date = T` answers "what would we know / do on T?"

### `data_date`

The most recent trading date for which **market data is available** in
qlib at the time of computation.
- Resolved by `resolve_data_date(trade_date, mode="asof")` in production.
- In backtest, resolved from the loaded cache's calendar.
- Always satisfies `data_date <= trade_date`.

### `reference_date`

Alias for `data_date` in prediction contexts. Used in plan artifacts to
record the data vintage that produced the prediction.

---

## 2. Production Pipeline Semantics

### Preopen Prediction (`trade_date = T`)

```
data_date = previous_trading_day(T)
feature_start = data_date - lookback
feature_end = data_date
```

- Prediction uses data observable before market open on T.
- `resolve_data_date(T, mode="previous")` returns the last trading day
  strictly before T.
- This is enforced by `BaseStrategyAdapter.resolve_preopen_data_date()`.

### Postclose / MTM (`trade_date = T`)

```
data_date = resolve_data_date(T, mode="asof")   -- latest trading day with data in qlib
close_price query = qlib.get_features(..., start_time=data_date, end_time=data_date)
```

- MTM uses the best available close prices at computation time.
- `data_date` may equal `T` (if daily sync has completed) or
  `previous_trading_day(T)` (if data is T+1).
- Both `trade_date` and `data_date` are recorded in the MTM snapshot.

### Daily Data Sync (`target = resolve_target_date()`)

```
target = latest trading day <= today
```

- On a trading day after market close, target = today.
- The sync tool's `_resolve_target_date()` uses `d <= today` logic
  against the local `trade_cal` table.
- `pre_check` then verifies per-stock whether fetch is needed.

---

## 3. Research / Backtest Semantics

### Label Window

```
label for trade_date T: forward_return starting at T+1, ending at T+horizon
label_start = T + 1 trading day
label_end   = T + horizon trading days
```

- Labels are **future data** relative to trade_date T.
- They must never enter prediction features for trade_date T.

### Rolling Train Window

```
train_start = prediction_start - train_window_days - 1
train_end   = prediction_start - 1
```

- The last training date must be **strictly before** the first prediction
  date.
- No data from prediction_start or later may enter the training set.

### Fixed-Model Backtest (from cached signals)

```
signal row:
  trade_date = T
  data_date <= previous_trading_day(T)
```

- For signal-based backtest (no rolling train), the signal cache is
  treated as a fixed artifact.
- Every signal row must satisfy `data_date <= previous_trading_day(trade_date)`.
- This prevents lookahead: no signal can use data not yet observable on T.

### Backtest-from-Signal

```
input: cached signals with (trade_date, data_date, instrument, score)
portfolio construction: per trade_date only
```

- No rolling training occurs during backtest — that is the caller's
  responsibility.
- Backtest engine reads signals at trade_date, not data_date.
- A backtest run is always at least one day behind the signal generation
  run (backtest requires completed trading days only).

---

## 4. Constraints (Enforced by Checkers)

| Rule | Enforced By |
|---|---|
| `data_date <= previous_trading_day(trade_date)` for signals | `check_no_lookahead.py` |
| `train_end < prediction_start` for rolling train | Pipeline validation |
| `resolve_preopen_data_date == previous_trading_day` | Contract test |
| MTM snapshot records both `trade_date` and `data_date` | MTM output |
| Label rows use window semantics, not asof | `check_label_schema.py` |
| Signal rows carry `trade_date`, `data_date`, `signal_id`, `signal_run_id` | `check_signal_schema.py` |
