# sysq-daily

## Purpose
Daily operational workflow for SysQ: data readiness, label maturity, retrain eligibility, inference readiness, candidate output.

## Inputs
- trade_date
- strategy_id
- horizons
- optional force_retrain=false

## Required reads
- `AGENTS.md`
- `docs/requirements/harness_map.yaml`
- relevant UC blocks: UC_DAILY_OPS, UC_MODEL_TRAINING
- model registry / pointer docs or code
- latest data readiness artifact if available

## Workflow
1. Resolve trade_date.
2. Check data readiness.
3. Check label maturity per horizon.
4. Decide retrain eligibility.
5. Verify model pointer.
6. Decide inference eligibility.
7. Produce candidate list only from standard artifacts.
8. Report checks and risks.

## Never
- invent stocks
- train with immature labels
- use latest model for historical inference unless explicitly allowed
- modify model code during daily decision
- confuse research artifacts with daily/shadow/prod artifacts

## Required checks
```bash
python harness/checks/check_label_maturity.py --trade-date <date> --horizon <h> --train-end <date>
python harness/checks/check_daily_inference_ready.py --trade-date <date> --strategy-id <strategy>
```
