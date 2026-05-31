# Strategy Allocation Boundary

## Purpose

Define the clean separation between signal generation, strategy allocation,
plan building, and execution.  This boundary lets us develop and test each
layer independently.

## Layer Diagram

```
SignalRun / Predictions
  score by (trade_date, instrument)
         │
         ▼
  Allocation Layer (strategy)
  build_rank_weight_targets()
  score ranked → top_n → target_weights
         │
         ▼
  TargetWeights
  (trade_date, instrument, target_weight)
         │
         ▼
  PlanBuilder
  build_plan_from_targets()
  target_weights + account → order_intents
         │
         ▼
  OrderIntents
  (instrument, side, quantity, order_type, price)
         │
         ▼
  Execution / Account / MTM
  match_engine → fills → positions → PnL
```

## 1. Signal Is Not Strategy

A **signal** is model output or a derived expression result.
It is a score per (trade_date, instrument).
It does not know about portfolio size, buffer rules, or cash.

Signal lives in ``qsys.signal``.

## 2. Model Is Not Strategy

A **model** is a trained artifact (XGBoost, LightGBM, etc.).
It produces raw predictions.
It does not know about allocation, buffer rules, or plan building.

Models live in ``qsys.model.zoo``.

## 3. Allocation Is Strategy-Layer Logic

Allocation converts signals into target_weights.

It encapsulates:
- Score ranking and selection
- Weight assignment (rank-weight, equal-weight, etc.)
- Top-N buffer and hold rules
- Single-stock cap and redistribution

Allocation lives in ``qsys.strategy.allocation``.

**Key design rule**: Allocation functions take a **DataFrame** of predictions,
not a model, not a SignalStore, not an Account.  Account state is handled
upstream (in PlanBuilder) or downstream (in Execution).

## 4. Portfolio / Account Is Execution State

Portfolio state (current positions, cash) is **execution layer** data.
Allocation does not need to know current positions for target construction
(though ``PlanBuilder`` does, to compute deltas from current).

``qsys.trader.account`` and ``qsys.trader.diff`` are execution layer.

## 5. TargetWeights Is the Boundary

``TargetWeights`` is the **contract** between allocation and planning.

Schema:

| Column | Type | Required | Description |
|---|---|---|---|
| ``trade_date`` | str | Yes | Trading date |
| ``instrument`` | str | Yes | Instrument code |
| ``target_weight`` | float | Yes | Target allocation weight |
| ``score`` | float | No | Score used for ranking |
| ``rank`` | int | No | Rank within selection |
| ``allocation_method`` | str | No | Method label |
| ``strategy_id`` | str | No | Strategy identifier |
| ``signal_id`` | str | No | Signal identifier |
| ``signal_run_id`` | str | No | Signal run identifier |

Validation rules (``validate_target_weights``):
- Required columns present
- No null values in required columns
- No duplicate (trade_date, instrument) rows
- target_weight must be finite
- target_weight >= 0 (long-only constraint)
- Per-date sum of target_weight <= 1.0 (+ small tolerance)
- Empty frame is an error unless ``allow_empty=True``

## 6. PlanBuilder Consumes TargetWeights

``PlanBuilder`` takes target_weights + current account state
and produces order intents.

It does **not** understand model or signal semantics.
It does **not** rank or re-rank instruments.

## 7. Execution Consumes OrderIntents

Execution (MatchEngine, Ledger, MTM) operates on order intents and fills.
It does **not** see signals, scores, or allocation logic.
