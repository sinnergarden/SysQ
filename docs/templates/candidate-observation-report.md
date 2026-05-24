# Candidate Observation Report

## Strategy

| Field | Value |
|-------|-------|
| **strategy_id** | `<strategy_id>` |
| **display_name** | `<display_name>` |
| **family** | `<family>` |
| **stage** | candidate |
| **universe** | `<universe>` |
| **account_id** | `<account_id>` |

## Observation Period

| Field | Value |
|-------|-------|
| **Start date** | `YYYY-MM-DD` |
| **End date** | `YYYY-MM-DD` |
| **Trading days** | N |
| **Report date** | `YYYY-MM-DD` |

## Hypothesis

> <Copy hypothesis from StrategySpec.>

## Feature / Model / Signal Summary

| Component | Detail |
|-----------|--------|
| **Feature set** | `<feature_set>` |
| **Model version** | `<model_version>` |
| **Signal version** | `<signal_version>` |
| **Portfolio method** | `<portfolio_method>` |
| **Top N** | N |
| **Rebalance frequency** | `<freq>` |

## Daily Run Success Rate

| Metric | Value |
|--------|-------|
| **Scheduled runs** | N |
| **Successful runs** | N |
| **Failed runs** | N |
| **Success rate** | N% |

### Failed runs detail

| Date | Mode | Error |
|------|------|-------|
| `YYYY-MM-DD` | preopen/postclose | `<error>` |

## MTM Summary

| Metric | Value |
|--------|-------|
| **Start MTM** | ¥ |
| **End MTM** | ¥ |
| **Total PnL** | ¥ |
| **Return** | % |
| **Benchmark return** | % |
| **Excess return** | % |

## Turnover Summary

| Metric | Value |
|--------|-------|
| **Avg daily turnover** | % |
| **Max daily turnover** | % |
| **Avg order count** | N |
| **Max order count** | N |

## Drawdown

| Metric | Value |
|--------|-------|
| **Max drawdown** | % |
| **Max drawdown period** | `YYYY-MM-DD` → `YYYY-MM-DD` |
| **Recovery days** | N |

## Abnormal Days

| Date | Event | Impact |
|------|-------|--------|
| `YYYY-MM-DD` | <unexpected PnL, data gap, etc.> | <description> |

## Data Issues

| Issue | Dates Affected | Status |
|-------|---------------|--------|
| <stale data, missing fields, etc.> | `YYYY-MM-DD` – `YYYY-MM-DD` | resolved / ongoing |

## Execution Issues

| Issue | Dates Affected | Status |
|-------|---------------|--------|
| <timeout, mismatch, etc.> | `YYYY-MM-DD` – `YYYY-MM-DD` | resolved / ongoing |

## Decision

- [ ] **Continue candidate** — maintain observation
- [ ] **Revise** — adjust config/adapter and re-observe
- [ ] **Reject** — move to `stage: rejected`
- [ ] **Promote to production** — initiate production readiness review

### Rationale

<Brief explanation of the decision.>

## Evidence Links

| Artifact | Path |
|----------|------|
| Batch summaries | `runs/YYYY-MM-DD/batch_candidate_preopen.json` |
| Batch summaries | `runs/YYYY-MM-DD/batch_candidate_postclose.json` |
| DR=BT check | `/tmp/qsys_framework_stability/framework_stability_check.json` |
| Evaluation report | `<path>` |
| Backtest result | `<path>` |
