# Framework Stability Contract

## 1. Purpose

This document defines the stable framework invariants after the framework-stability
closure phase.  These invariants must be preserved by all future changes.  Violating
them requires explicit review and update of this contract.

## 2. Protected Core

The following modules and entrypoints are part of the protected core.  Changes to
these require extra review (see §5).

| Module / Entrypoint | Role |
|---|---|
| `qsys/strategy/spec.py` — `StrategySpec` | Static strategy identity, config, lifecycle |
| `qsys/strategy/validators.py` | Stage-specific config validation |
| `qsys/strategy/registry.py` | Strategy ID → adapter class map |
| `qsys/strategy/*/adapter.py` | StrategyAdapter runtime implementations |
| `qsys/data/calendar.py` | Centralised calendar semantics |
| `qsys/ops/daily_runner.py` | DailyRunner orchestration |
| `qsys/ops/plan_builder.py` | Public plan-building API |
| `qsys/ops/shadow_execution.py` | Public shadow execution API |
| `qsys/ops/market_snapshot.py` | Market data snapshot API |
| `qsys/backtest/*` | BacktestRunner and portfolio logic |
| `qsys/trader/*` | Account, matcher/fill, portfolio models |
| `scripts/run_daily.py` | Single-strategy entrypoint |
| `scripts/run_daily_batch.py` | Stage-aware batch dispatch |
| `scripts/check_framework_stability.py` | Aggregate stability guardrail |
| `docs/architecture/framework-stability-contract.md` | This document |

## 3. Stable Invariants

### Identity and Lifecycle

- `strategy_id` is the single source of truth for strategy identity.
- `stage` is the lifecycle state (research → candidate → production → archived).
- Research-stage strategies are never scheduled by daily batch.
- Candidate-stage strategies are shadow-running strategies with a registered
  `StrategyAdapter`, shadow account, and daily artifacts.
- Production-stage strategies require explicit `--allow-production` to dispatch.
  Production risk controls are not yet fully implemented.

### Calendar Semantics

- Default calendar mode is `asof` (a trading day resolves to itself).
- `resolve_data_date()` on `BaseStrategyAdapter` provides the default resolution.
- Trading day resolution is monotonic and deterministic for a given input.

### Backtest Semantics

- `BacktestRunner` default execution mode is open-price execution + close-price MTM.
- Close-price execution is legacy compatibility only.
- DR=BT equivalence is required for alpha_v1 weekly replay.
- Deterministic replay: identical inputs produce identical outputs.
- Forward-testing constraint: no future data leakage into training windows.

### API Contracts

- Public plan/execution APIs (`plan_builder`, `shadow_execution`, `market_snapshot`)
  must be used instead of private helpers from `shadow_rebalance`.
- Artifact schemas (predictions CSV, target_weights, order_intents, rebalance_audit,
  plan_meta, execution_summary, ledger_rows) must remain stable.
- Batch summary JSON schema (`batch_<stage>_<mode>.json`) must remain stable.

### Runtime Safety

- Each strategy dispatch is isolated in a subprocess.
- A single strategy failure does not block other strategies (default `--continue-on-error`).
- Unregistered strategies are skipped with a clear warning.
- Production dispatch is blocked without `--allow-production`.

## 4. Required Checks Before PR Merge

Before merging any PR that touches the protected core, the following checks must pass:

1. **Calendar tests**: `python -m pytest tests/data/test_calendar.py -q`
2. **Strategy contract tests**: `python -m pytest tests/contracts/test_strategy_calendar_contract.py -q`
3. **StrategySpec tests**: `python -m pytest tests/strategy/test_strategy_spec.py -q`
4. **Batch runner tests**: `python -m pytest tests/scripts/test_run_daily_batch.py -q`
5. **Validator tests**: `python -m pytest tests/strategy/test_strategy_validators.py -q`
6. **Batch dry-run**: `python scripts/run_daily_batch.py --stage candidate --mode preopen --trade-date YYYY-MM-DD --dry-run`
7. **DR=BT equivalence** (if touching trading/matching/portfolio logic):
   `python scripts/check_dr_bt_equivalence.py --strategy alpha_v1 --start-date ... --end-date ... --initial-capital 1000000 --rebalance-freq weekly`

These checks can be run together via:

```bash
python scripts/check_framework_stability.py --quick
```

## 5. What Requires Extra Review

Changes touching any of the following require +1 from someone familiar with
the framework stability contract and explicit mention in the PR description:

- `qsys/data/calendar.py` — calendar resolution changes
- `qsys/strategy/*/adapter.py` — strategy runtime behaviour
- `qsys/backtest/*` — backtest trading semantics
- `qsys/ops/plan_builder.py` — plan artifact schema
- `qsys/ops/shadow_execution.py` — execution artifact schema
- `qsys/trader/*` — matcher, fill, cost, T+1 logic
- `qsys/backtest/portfolio.py` — portfolio construction
- `scripts/run_daily.py` — single-strategy dispatch
- `scripts/run_daily_batch.py` — batch dispatch
- `scripts/check_framework_stability.py` — stability guardrail
- This contract document

## 6. What Is Not Stable Yet

The following areas are explicitly **not covered** by this contract and may
change without notice:

- High-performance backtest cache
- Feature panel cache and optimisation
- Production broker / QMT execution
- Production risk engine and limits
- Transformer / deep-learning research path
- Large-scale strategy portfolio management UI
- Rolling retrain pipeline
- `run_alpha_v1_*.py` legacy scripts (deprecated, see deprecation warnings)
