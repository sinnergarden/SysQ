---
description: Run evaluation with the strict Qsys split and reporting contract
argument-hint: "[model path or model name] [date range]"
---

Use `train-split-discipline` to enforce the approved evaluation contract before interpreting any result.

Preferred underlying entrypoints:
- `scripts/run_backtest.py`
- `scripts/run_strict_eval.py`

Required output:
- evaluation window
- top_k and cost assumptions
- key metrics
- baseline comparison
- risk flags
- promote / hold / investigate suggestion
