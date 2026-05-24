# Semantic Guard Checks

Executable invariant checks that validate strategy and backtest correctness.

## Unit tests

```bash
# Calendar resolution (mock provider, no qlib needed)
python -m pytest tests/data/test_calendar.py -q

# All strategies follow framework date semantics
python -m pytest tests/contracts/test_strategy_calendar_contract.py -q

# Equivalence script logic
python -m pytest tests/scripts/test_check_dr_bt_equivalence.py -q
```

## DR=BT equivalence check (alpha_v1)

```bash
python scripts/check_dr_bt_equivalence.py \
    --strategy alpha_v1 \
    --start-date 2026-05-16 \
    --end-date 2026-05-22 \
    --initial-capital 1000000 \
    --rebalance-freq weekly \
    --output-dir /tmp/qsys_dr_bt_check
```

This runs BacktestRunner twice in self-check mode and asserts deterministic
output.  Runs against real qlib data and model artifacts.

Exit code 0 = pass.  Non-zero exit code on mismatch.

## Batch runner checks

```bash
# Unit tests
python -m pytest tests/scripts/test_run_daily_batch.py -q

# Dry run — verify strategy selection without dispatching
python scripts/run_daily_batch.py \
    --stage candidate --mode preopen \
    --trade-date 2026-05-22 --dry-run
```

## When to run

Before merging PRs that touch:

- `qsys/data/calendar.py`
- `qsys/strategy/*/adapter.py`
- `qsys/backtest/*`
- `qsys/ops/plan_builder.py`
- `qsys/ops/shadow_execution.py`
- `qsys/trader/*`
- `qsys/backtest/portfolio.py`
- `scripts/run_daily.py`
- `scripts/run_daily_batch.py`
- `deploy/systemd/*`

## Known invariant: alpha_v1 weekly replay

- start: 2026-05-16
- end: 2026-05-22
- effective trading: 2026-05-18 to 2026-05-22
- capital: 1,000,000
- initial account: empty
- BacktestRunner open-mode: final **total_value = 1,019,341.33**

This value is the canonical baseline.  Any change must be intentional and
documented.
