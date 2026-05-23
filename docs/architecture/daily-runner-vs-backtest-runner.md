# DailyRunner vs BacktestRunner

## Core principle

The ``BacktestRunner`` is **daily-equivalent by default**: it simulates
historical daily visibility using a visible-date mask, preserving
``DailyRunner``-equivalent strategy semantics.

The ``BacktestRunner`` is **NOT** a loop around ``DailyRunner``.

---

## Why not loop DailyRunner?

``DailyRunner`` owns **production runtime IO**:

- Telegram notifications
- ``COMMITTING`` / ``COMMITTED`` marker files
- Production ledger commit (``write_execution_to_ledger``)
- Force-rerun logic
- Daily forensic artifact explosion (``predictions/``, ``plan/``,
  ``execution/``, ``mtm/``, ADR-007 sidecars)

Wrapping ``DailyRunner`` in a date loop would:

1. Spam notifications for every historical day.
2. Create thousands of marker files.
3. Pollute the production ledger with historical noise.
4. Multiply artifact I/O by hundreds of days.

---

## Shared semantics

| Concern | Shared? | Notes |
|---------|---------|-------|
| ``strategy_id`` / ``StrategySpec`` | Yes | Same identity model |
| ``StrategyAdapter`` hooks | Yes | Same predict/plan/execute semantics |
| Feature / model / signal logic | Yes | Wherever possible |
| Portfolio construction | Yes | Same ``build_rank_weight_portfolio`` |
| ``OrderGenerator`` (deterministic) | Yes | Same sorted ordering |
| ``MatchEngine`` / matcher | Yes | Same T+1, cost, fill semantics |
| Visible-date mask | Yes | No future data in either |
| ``Account`` / ``Position`` | Yes | Same state model |

## Non-shared

| Concern | DailyRunner | BacktestRunner |
|---------|-------------|----------------|
| Telegram | Sends notifications | No notifications |
| ``COMMITTING`` / ``COMMITTED`` | Creates markers | No markers |
| Production ledger | Commits to DB | No ledger writes |
| Force-rerun | Detects and skips | Not applicable |
| Artifacts | Full directory per day | ``artifact_mode="summary"`` |
| ADR-007 sidecars | Generated per artifact | Optional |

---

## BacktestRunner modes

### ``strict_daily_equivalent``

Exact date-by-date visible-mask semantics.  Every day loads only data
observable at that point in time.  Most faithful to production, but also
the slowest.  Use for final validation.

### ``cached_daily_equivalent``

Allows batch / cached data loading when mathematically equivalent under
the same visible-data mask and execution semantics.

Future optimisation path:

1. **Batch data loading** — load the full feature panel once instead of
   per-day qlib calls.
2. **Model cache** — load model once, reuse across days.
3. **Feature panel cache** — reuse computed features across dates.
4. **Batch prediction** — predict all dates at once (if the model and
   feature set allow it).
5. **Sequential execution** — still iterate day by day for portfolio
   state, orders, fills.
6. **Minimal artifact mode** — only keep summary metrics, not per-day
   CSVs.

---

## Rolling schedules

Backtest must model the same schedules as the daily runtime:

| Schedule | Example |
|----------|---------|
| Daily signal / weekly trade | Predict every day, rebalance on Friday |
| Weekly retrain / daily inference | Train Monday, predict all week |
| Monthly rebalance | First trading day of month |

These schedules are strategy-owned via the ``StrategySpec`` portfolio and
training configs.

---

## Performance direction

```
Data loading:     per-day qlib  ──►  batch panel
Model loading:    per-day load  ──►  cache once
Feature compute:  per-day calc  ──►  panel transform
Prediction:       per-day infer ──►  batched predict
Execution:        sequential    ──►  sequential (same)
Artifacts:        full per-day  ──►  summary only
```
