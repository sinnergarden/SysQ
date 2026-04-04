# Qsys Workflow Contracts

This file defines the minimum output shape expected from the first batch of Qsys workflow adapters.

## Shared Top-Level Contract

```json
{
  "task_name": "preopen-plan",
  "status": "ok",
  "decision": "ready",
  "blocker": null,
  "input_params": {},
  "data_status": {},
  "model_info": {},
  "artifacts": {},
  "summary": {},
  "risk_flags": [],
  "next_action": null,
  "markdown_summary": "..."
}
```

## Command-Specific Notes

### `preopen-plan`

`summary` should include:
- `signal_date`
- `execution_date`
- `target_portfolio`
- `executable_portfolio`
- `blocked_symbols`
- `signal_quality_gate`
- `assumptions`

### `feature-audit`

`summary` should include:
- `feature_set`
- `coverage_summary`
- `missingness_summary`
- `anomalies`
- `recommended_action`

### `rolling-eval`

`summary` should include:
- `evaluation_window`
- `baseline_model`
- `candidate_model`
- `key_metrics`
- `comparison_table`
- `promotion_suggestion`

## Decision Semantics

- `ready`: can proceed with the next workflow stage
- `warning`: usable, but requires explicit caveat tracking
- `blocked`: must not proceed without fixing the blocker

## Status Semantics

- `ok`: adapter ran and produced a contract-compliant result
- `error`: adapter failed to produce a valid result
