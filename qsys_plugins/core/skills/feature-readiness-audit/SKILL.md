# Feature Readiness Audit

description: Audit whether a feature set is suitable for training or promotion. Use when evaluating coverage, missingness, duplicate columns, date alignment, or suspicious feature behavior.

## Triggers

Use when:
- the user asks for a feature audit or coverage review
- a new feature set is proposed for training
- model results look suspicious and feature quality needs checking
- promotion depends on proving a feature set is data-ready

## Workflow

1. Identify the target feature set, date range, and universe.
2. Collect feature coverage and missingness statistics.
3. Check for structural anomalies:
   - duplicate columns
   - unexpected suffix collisions such as `_x` / `_y`
   - date parsing inconsistencies
   - all-null or near-dead factors
   - mismatched index/date alignment
4. Classify each anomaly as informational, warning, or blocker.
5. Produce a final decision:
   - `ready`: safe to train
   - `warning`: trainable but needs caveat tracking
   - `blocked`: fix data before training

## Required Output

- `feature_set`
- `coverage_summary`
- `anomalies`
- `decision`
- `recommended_action`

## Notes

- The audit should explicitly look for failure modes already seen in Qsys, such as masked core columns and broken date handling.
- A pretty backtest never overrides a blocked audit result.
