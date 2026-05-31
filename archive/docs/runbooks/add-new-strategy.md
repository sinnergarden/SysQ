# New Strategy Development Runbook

This runbook walks through the process of creating a new strategy from research
idea to candidate observation.

---

## 1. Define StrategySpec

Create a YAML config file at `configs/strategies/<strategy_id>.yaml`.

Start with `stage: research`.  Research-stage strategies have minimal requirements
and are not executed by the daily batch.

Minimal research config:

```yaml
strategy_id: my_new_strategy
stage: research
family: momentum          # or value, ml, statistical-arbitrage, …
display_name: My New Strategy
owner: your_name
universe: csi300
feature_set: my_features

hypothesis: >
  One-paragraph description of the strategy hypothesis:
  what signal you are capturing, why it should predict returns,
  and in what market regime it is expected to perform.

label:
  horizons: [5, 20]
  type: forward_return

model:
  type: lightgbm_rank
  params:
    num_leaves: 31
    learning_rate: 0.05

signal:
  method: rank
  top_n: 20

portfolio:
  top_n: 20
  buffer_hold: 60
  buffer_buy: 40
  single_stock_cap: 0.07
  rebalance_freq: weekly
```

## 2. Validate Spec

```bash
# Run spec validation against all configs
python -m pytest tests/strategy/test_strategy_spec.py -q

# Or use the framework stability check
python scripts/check_framework_stability.py --quick
```

## 3. Research and Backtest

Use `BacktestRunner` or a strategy-specific evaluation script.

Outputs:
- `evaluation_report.json` — evaluation metrics
- `backtest_result.json` — backtest PnL and statistics

Forward-testing constraint:
**Never use a model trained on data that includes the test period.**
When backtesting, pass `--end-date` explicitly so the training window
cuts off before the test period.

See: `docs/runbooks/weekly-replay-regression.md`

## 4. Promote Research → Candidate

Requirements for promotion:

- [ ] Evaluation report exists
- [ ] No future-data violation in backtest
- [ ] `StrategyAdapter` exists (see `qsys/strategy/alpha_v2/adapter.py` for reference)
- [ ] Registry entry exists (add to `qsys/strategy/registry.py`)
- [ ] Config updated: `stage: candidate`, `account_id: shadow_<id>`
- [ ] Debug-run passes for at least one trade date:

```bash
python scripts/run_daily.py \
    --strategy my_new_strategy \
    --mode preopen \
    --trade-date YYYY-MM-DD \
    --debug-run \
    --no-notify
```

- [ ] Batch dry-run includes it:

```bash
python scripts/run_daily_batch.py \
    --stage candidate \
    --mode preopen \
    --trade-date YYYY-MM-DD \
    --dry-run
```

### Candidate config requirements

When moving to `stage: candidate`, ensure the config includes:

```yaml
stage: candidate
account_id: shadow_<strategy_id>    # must start with shadow_
model_version: <version>            # or model.version
signal_version: <version>           # or signal.version

paths:
  model_dir: experiments/<id>_models/latest
  predictions_dir: experiments/<id>_shadow_predictions
  ledger_db: data/trade.db

lifecycle:
  created: YYYY-MM-DD
  stage_history:
    - stage: research
      at: YYYY-MM-DD
    - stage: candidate
      at: YYYY-MM-DD
```

## 5. Candidate Observation

Once promoted to candidate, the strategy runs automatically via:

```bash
python scripts/run_daily_batch.py --stage candidate --mode preopen
python scripts/run_daily_batch.py --stage candidate --mode postclose
```

Track the following during the observation period:

- Daily run success rate (should be 100%)
- MTM and PnL vs benchmark
- Turnover and order count
- Data staleness or failures
- Drift from expected behaviour

Use the candidate observation report template:
`docs/templates/candidate-observation-report.md`

## 6. Promote Candidate → Production

**Not yet fully supported.** Production promotion requires:

- [ ] Risk limits defined (`risk_limits` in config)
- [ ] Capital allocation defined (`capital_allocation` in config)
- [ ] Broker/execution policy defined (`broker_policy` or `execution_policy`)
- [ ] Approval policy documented (`approval_policy` in config)
- [ ] Manual review and sign-off
- [ ] Explicit `--allow-production` flag when dispatching

```bash
python scripts/run_daily_batch.py \
    --stage production \
    --mode preopen \
    --trade-date YYYY-MM-DD \
    --dry-run \
    --allow-production
```

## Checklist (Agent / Developer)

- [ ] `configs/strategies/<strategy_id>.yaml` created with `stage: research`
- [ ] Spec validates (`python -m pytest tests/strategy/test_strategy_spec.py -q`)
- [ ] Backtest run and evaluation saved
- [ ] `StrategyAdapter` implemented
- [ ] Registry entry added (`qsys/strategy/registry.py`)
- [ ] Config updated to `stage: candidate`
- [ ] `--debug-run` passes for preopen
- [ ] Batch dry-run includes the strategy
- [ ] Observation report started
- [ ] At least 20 trading days of observation before production consideration
