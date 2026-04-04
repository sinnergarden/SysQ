# Trading Calendar Guard

description: Validate signal_date, execution_date, raw_latest, qlib_latest, and trading-calendar semantics before generating a plan. Use when preparing a daily plan, checking data readiness, or reviewing a suspicious date mismatch.

## Triggers

Use when:
- the user asks for a pre-open plan or daily plan
- the user asks whether data is ready for today's recommendation
- plan generation involves `signal_date` / `execution_date`
- any output depends on T-1 close data

## Workflow

1. Resolve the requested `signal_date` and `execution_date`.
2. Read current data status: `raw_latest`, `last_qlib_date`, universe readiness, and whether requested feature rows exist.
3. Enforce date semantics:
   - `signal_date` is the market close date used for signal generation
   - `execution_date` is the intended trading date
   - pre-open recommendations must be based on T-1 close
4. Block plan generation if any of the following holds:
   - `raw_latest < signal_date - 1 trading day`
   - `last_qlib_date < signal_date`
   - requested universe or feature rows are unavailable
   - date alignment is ambiguous
5. If dates are valid, pass the request to planning logic.

## Required Output

- `data_status`: raw_latest, qlib_latest, aligned, requested_dates
- `decision`: `ready` or `blocked`
- `blocker`: explicit reason when blocked
- `next_action`: what to fix or run next

## Notes

- Never produce a fake pre-open recommendation when data is stale.
- Date correctness is a hard gate, not a soft warning.
- This skill validates semantics; it does not replace the actual plan generator.
