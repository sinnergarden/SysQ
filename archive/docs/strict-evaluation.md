# Strict Evaluation Contract

## Overview

P0.2 implements a unified evaluator that makes baseline vs extended evaluation reproducible with explicit windows and `top_k=5` defaults.

## Evaluation Windows

From ROADMAP consensus:

| Window | Period | Purpose |
|--------|--------|---------|
| Main | 2025-01-01 to latest | Primary performance evaluation |
| Aux (2026 YTD) | 2026-01-01 to latest | Style-shift detection |

## Default Parameters

- `top_k=5`: All backtests use 5 stocks
- Train/valid/test split: Explicit separation to avoid data leakage
- No overlap between training and test periods

## Usage

### CLI

```bash
# Default: compares baseline vs extended with top_k=5
python scripts/run_strict_eval.py

# Custom models
python scripts/run_strict_eval.py \
    --baseline data/models/qlib_lgbm_phase123 \
    --extended data/models/qlib_lgbm_phase123_extended

# Custom end date
python scripts/run_strict_eval.py --end 2025-06-30
```

### Python API

```python
from qsys.evaluation import StrictEvaluator

evaluator = StrictEvaluator(top_k=5)
report = evaluator.run_comparison(
    baseline_model_path="data/models/qlib_lgbm_phase123",
    extended_model_path="data/models/qlib_lgbm_phase123_extended"
)

# Print markdown table
print(report.to_markdown())

# Save to CSV
evaluator.save_report(report, "experiments/results.csv")

# Summary comparison
summary = report.summary_table()
print(summary)
```

## Output

The evaluator produces a structured report with:
- Total return
- Annual return
- Annual volatility  
- Sharpe ratio
- Maximum drawdown
- Trade count

### Example Output

```
| Period           | Model     | Total Return | Annual Return | Sharpe | Max DD  | Trades |
|------------------|-----------|--------------|---------------|--------|---------|--------|
| 2025-01~2025-03  | Baseline  | 5.23%        | 21.45%        | 1.234  | -3.21%  | 45     |
| 2025-01~2025-03  | Extended  | 6.87%        | 28.12%        | 1.456  | -2.89%  | 52     |
| 2026 YTD         | Baseline  | 2.15%        | 25.80%        | 1.123  | -4.56%  | 18     |
| 2026 YTD         | Extended  | 1.98%        | 23.76%        | 0.987  | -5.12%  | 21     |
```

## Design Rationale

1. **Explicit windows**: No ambiguity about what period was used
2. **top_k=5**: Conservative setting for realistic position sizing
3. **Unified entrypoint**: Single script for all strict evaluations
4. **Structured output**: CSV for automated processing, markdown for human review