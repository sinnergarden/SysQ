---
description: Generate a pre-open plan with explicit target vs executable views
argument-hint: "[signal_date] [execution_date]"
---

Use `trading-calendar-guard` first to validate date semantics and data readiness.
Then use `shadow-execution-planner` to separate target portfolio from executable portfolio.

Preferred underlying entrypoint:
- `scripts/run_daily_trading.py`

Required output:
- data status
- model version
- target portfolio
- executable portfolio
- blocked symbols and reasons
- assumptions on fees, slippage, and T+1
- next action
