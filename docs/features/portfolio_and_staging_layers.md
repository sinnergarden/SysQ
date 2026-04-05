# Portfolio And Staging Layers

## Goal

Add a minimal, auditable two-step flow that keeps portfolio intent and executable orders separate:

1. `portfolio`: convert model scores into target weights with explicit risk filtering and reason codes.
2. `staging`: convert target weights into staged orders under broker snapshot, price limits, lot size, and cash constraints.

## Use Cases

- Generate `target_weights.csv` from ranked scores without mixing in execution side effects.
- Review why a stock was selected, rejected, or trimmed before any executable order exists.
- Convert target weights into `orders.csv` that reflects current holdings, tradability limits, and cash limits.
- Preserve a clear audit trail when orders are rounded, reduced, or rejected.

## API Change

New module APIs:

- `qsys.strategy.portfolio.build_portfolio_intent(...)`
- `qsys.strategy.portfolio.save_target_weights(...)`
- `qsys.strategy.portfolio.save_reason_codes(...)`
- `qsys.trader.staging.stage_orders(...)`
- `qsys.trader.staging.save_orders(...)`
- `qsys.trader.staging.save_staging_reason_codes(...)`

No protected live/account/model interfaces are changed.

## UI

No UI change.

A small example script writes:

- `target_weights.csv`
- `reason_codes.json`
- `orders.csv`
- `staging_reason_codes.json`

## Constraints

- `portfolio` and `staging` stay explicitly separated.
- Both layers remain pure in core calculation; file output is only via explicit save helpers.
- Outputs use `ts_code` at the boundary, with `symbol` accepted only as input alias.
- Industry concentration handling is explicit: if industry input is missing, the rule is skipped and logged.
- Buy cash budget is conservative by default: `available_cash * (1 - cash_buffer)` without assuming sell proceeds are filled.
- No writes to `data/raw/` or other primary data directories.

## Done Criteria

- `qsys/strategy/portfolio.py` builds target weights from scores and risk rules.
- `qsys/strategy/portfolio.py` emits structured portfolio reason codes.
- `qsys/trader/staging.py` stages sell orders before buy orders.
- `qsys/trader/staging.py` enforces lot size, price limit rejection, and buy cash budget.
- Rejections and adjustments are recorded in structured staging reason codes.
- A runnable example demonstrates score -> target weights -> orders.
- Tests cover blacklist/top-k reasons, lot-size round-down, price-limit rejection, and cash-budget adjustment.
