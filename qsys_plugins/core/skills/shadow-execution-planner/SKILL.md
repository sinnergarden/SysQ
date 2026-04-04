# Shadow Execution Planner

description: Translate a target portfolio into an executable A-share plan with explicit T+1 and cash constraints. Use when producing daily recommendations, reviewing turnover, or comparing target vs executable outcomes.

## Triggers

Use when:
- the user asks for a daily plan
- the user asks what is actually executable tomorrow
- the user asks why target and real/shadow portfolios differ
- A-share T+1 constraints matter

## Workflow

1. Read the target portfolio and current account state.
2. Separate two views:
   - `target_portfolio`: ideal holdings implied by the model
   - `executable_portfolio`: what can actually be traded on `execution_date`
3. Apply A-share execution constraints:
   - T+1 sellability
   - available cash after expected sells
   - minimum trade amount and board-lot rules
   - fees and slippage assumptions
4. Record blocked or degraded actions with explicit reasons.
5. Produce an execution summary that is honest about utilization, leftovers, and risk.

## Required Output

- `target_portfolio`
- `executable_portfolio`
- `blocked_symbols`
- `cash_utilization`
- `assumptions`
- `risk_notes`

## Notes

- Never pretend the target portfolio is fully executable when T+1 or cash constraints prevent it.
- This skill is about execution realism, not alpha generation.
