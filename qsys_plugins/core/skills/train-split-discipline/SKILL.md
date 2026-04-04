# Train Split Discipline

description: Enforce the approved Qsys evaluation contract before interpreting model performance. Use when running strict evaluation, rolling backtests, or comparing a candidate against baseline or production.

## Triggers

Use when:
- the user asks for strict evaluation
- the user asks whether a model is better than baseline
- rolling backtest results are presented for decision-making
- a candidate is being considered for promotion

## Workflow

1. Read the requested train, valid, and test windows.
2. Enforce current Qsys discipline:
   - no train/test overlap
   - primary evaluation window is `2025 -> latest`
   - auxiliary review includes `2026 YTD`
   - default `top_k=5`
3. Check whether reported metrics are out-of-sample and reproducible.
4. Flag common false-confidence patterns:
   - training-period self-evaluation
   - windows that are too short
   - missing cost assumptions
   - no baseline comparison
5. Summarize metrics and classify the outcome:
   - `promote_candidate`
   - `hold_for_review`
   - `investigate_risk`

## Required Output

- `evaluation_window`
- `comparison_target`
- `metrics`
- `risk_flags`
- `decision`
- `next_action`

## Notes

- Strong returns without split discipline should be treated as suspicious, not persuasive.
- The purpose of this skill is not to run the backtest itself, but to force the right interpretation contract.
