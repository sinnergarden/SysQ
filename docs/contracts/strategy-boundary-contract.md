# Strategy Boundary Contract

## Purpose

Define the boundary between what a **strategy** owns and what the
**framework core** owns. This prevents strategy-specific concerns from
leaking into shared infrastructure and vice versa.

---

## 1. Layer Model

```
   Strategy Layer
   ┌──────────────────────────────────────┐
   │  Signal selection / blending         │  <- strategy owns
   │  Allocation / target construction    │
   │  PlanBuilder (target → orders)       │
   └──────────┬───────────────────────────┘
              │ target_weights
              ▼
   Core Execution Layer
   ┌──────────────────────────────────────┐
   │  OrderGenerator (intents → orders)   │  <- core owns
   │  MatchEngine (orders → fills)        │
   │  Account / Cash management           │
   │  Ledger (fills → positions)          │
   │  MTM (positions → PnL)              │
   └──────────────────────────────────────┘
```

---

## 2. What Strategy Package Owns

Each strategy (e.g., `qsys.strategy.alpha_v1`) is responsible for:

- **Model loading and inference** (via adapter)
- **Signal logic** (score computation, blending, rank)
- **Portfolio construction** (top-N selection, buffer rules)
- **Plan building** (`target_weights` → `order_intents`)
- **Strategy-specific config** (YAML under `configs/strategies/`)
- **Strategy-specific notification formatting**
- **Account ID and identity**

A strategy **may** define its own:
- Portfolio function
- Plan builder
- Notification templates
- Feature transforms (if truly strategy-specific)

---

## 3. What Framework Core Owns

The following modules under `qsys.core`, `qsys.ops`, `qsys.trader`,
`qsys.ledger` are shared infrastructure:

| Module | Owns |
|---|---|
| `qsys.data.calendar` | Trading calendar, date resolution |
| `qsys.trader.matcher` | Order matching engine |
| `qsys.trader.diff` | Order generation from target vs current |
| `qsys.ops.daily_runner` | Production daily pipeline orchestration |
| `qsys.ops.mtm` | Mark-to-market computation |
| `qsys.ledger` | Position and account ledger |
| `qsys.backtest.engine` | Backtest engine (match + account + order) |

---

## 4. Model Is Not Strategy

A **model** is:
- A trained artifact (XGBoost, LightGBM, etc.)
- Bound to a specific `feature_set_id` and `label_id`
- Capable of inference given feature input

A **strategy** is:
- A model *plus* signal processing, portfolio construction, and plan
  execution
- A strategy may compose multiple models
- A strategy may add non-model signals (e.g. index regime overlay)

**Implementation rule**: Model loading and inference must be separable
from strategy packaging. Model artifacts live in `qsys.model.zoo`.
Strategy adapters load models from the zoo.

---

## 5. Signal Is Model-to-Strategy Coupling Point

The **signal** is the contract between model output and strategy input:

```
Model → RawSignal (score) → SignalExpression → DerivedSignal → Strategy
```

- A strategy sees only `DerivedSignal` (or `RawSignal` if no expression).
- Models never see strategy-specific parameters.
- Signals are versioned and carry lineage.

---

## 6. BacktestRunner Is Not RollingTrainer

- `BacktestRunner` executes trades from cached signals on a fixed set of
  dates. No training occurs.
- `RollingTrainer` trains models on rolling windows and optionally passes
  predictions to BacktestRunner.
- A backtest run must declare its type: `fixed_model` or `rolling_train`.
- A `fixed_model` backtest must not modify signal values during the run.

**These roles may share code but must be distinct entry points.**

---

## 7. Red-Zone Files (Must Not Change in Strategy-Only PRs)

| File | Reason |
|---|---|
| `qsys/data/calendar.py` | Date semantics, affects all pipelines |
| `qsys/ops/daily_runner.py` | Production orchestration |
| `qsys/trader/matcher.py` | Order execution core |
| `qsys/trader/diff.py` | Order generation |
| `qsys/ledger/*.py` | Position/account integrity |
| `qsys/ops/mtm.py` | PnL computation |
| `qsys/ops/commit_guard.py` | Execution safety |
| `qsys/backtest/engine.py` | Backtest execution core |

**Strategy-only PRs** may change:
- Config files in `configs/strategies/`
- Strategy adapter in `qsys/strategy/<name>/`
- Tests for that strategy

---

## 8. Contract Violations

Modifying a red-zone file in a strategy-only PR requires:
1. Explicit justification in the PR body
2. A reviewer acknowledgment comment
3. A dedicated test proving the change
4. Notification in the `#framework` channel
