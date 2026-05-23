"""Backward-compatible wrapper around extracted public APIs.

This module re-exports all public names from:
- ``qsys.ops.market_snapshot``
- ``qsys.ops.plan_builder``
- ``qsys.ops.shadow_execution``

And preserves the original private-name aliases used by existing callers:

  ``_fetch_market_snapshot``, ``_read_predictions``, ``_load_shadow_account``,
  ``_build_target_weights``, ``_build_order_intents``, ``_positions_frame``,
  ``_write_execution_to_ledger``, ``_append_ledger``

Kept functions (delegate to new APIs):

  ``build_alpha_v1_plan`` — wraps ``build_plan_from_predictions`` with alpha_v1
  defaults and optional ledger-based account loading.

  ``execute_alpha_v1_plan`` — wraps ``execute_shadow_plan`` with
  ``run_id=f"alpha_v1_execute_{execution_date}"`` and ledger loading.

  ``run_shadow_rebalance`` — wraps ``build_plan_from_predictions`` output with
  execution + shadow-write (legacy all-in-one).

  ``run_alpha_v1_shadow_rebalance`` — alpha_v1-config variant of the above.

  ``_load_account_from_ledger`` — private helper kept here for backward compat.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from qsys.backtest.portfolio import build_rank_weight_portfolio
from qsys.ledger.service import LedgerService
from qsys.ops.market_snapshot import (
    ShadowRebalanceError,
    fetch_market_snapshot,
)
from qsys.ops.plan_builder import (
    DEFAULT_INITIAL_CAPITAL,
    DEFAULT_MIN_PREDICTION_COUNT,
    DEFAULT_TURNOVER_BUFFER,
    ORDER_INTENT_COLUMNS,
    POSITION_COLUMNS,
    REBALANCE_AUDIT_COLUMNS,
    TARGET_WEIGHT_COLUMNS,
    build_order_intents,
    build_plan_from_predictions,
    build_target_weights,
    load_shadow_account,
    read_predictions,
)
from qsys.ops.shadow_execution import (
    DEFAULT_PRICE_MODE,
    DEFAULT_REBALANCE_MODE,
    LEDGER_COLUMNS,
    ShadowRebalanceArtifacts,
    append_ledger,
    commit_execution_artifacts,
    execute_shadow_plan,
    positions_frame,
    write_execution_to_ledger,
    write_failed_execution_summary as _public_write_failed_execution_summary,
)
from qsys.strategy.alpha_v1 import ALPHA_V1_CANDIDATE
from qsys.trader.account import Account, Position
from qsys.trader.diff import OrderGenerator
from qsys.trader.matcher import MatchEngine
from qsys.utils.json_io import write_json

# ── Re-export constants ──────────────────────────────────────────────────────

DEFAULT_LEDGER_DB_PATH = str(Path(__file__).resolve().parent.parent.parent / "data" / "trade.db")

# ── Private-name aliases (backward-compatible) ──────────────────────────────

_fetch_market_snapshot = fetch_market_snapshot
_read_predictions = read_predictions
_load_shadow_account = load_shadow_account
_build_target_weights = build_target_weights
_build_order_intents = build_order_intents
_positions_frame = positions_frame
_write_execution_to_ledger = write_execution_to_ledger
_append_ledger = append_ledger


# ── Kept private helper (only used by alpha_v1 wrappers) ─────────────────────

def _load_account_from_ledger(
    service: LedgerService, account_id: str, initial_capital: float,
) -> tuple[Account, dict[str, Any], pd.DataFrame]:
    """Load Account + prior_account dict + positions DataFrame from LedgerService."""
    cash = service.get_cash(account_id)
    positions = service.get_positions(account_id)

    account = Account(init_cash=initial_capital)
    account.cash = cash

    pos_rows: list[dict[str, Any]] = []
    for p in positions:
        sym = p["symbol"]
        qty = int(p["quantity"])
        if qty <= 0:
            continue
        avail = int(p["available_quantity"])
        cost = float(p["avg_cost"])
        account.positions[sym] = Position(
            symbol=sym,
            total_amount=qty,
            sellable_amount=max(avail, 0),
            avg_cost=cost,
        )
        pos_rows.append({
            "instrument": sym, "quantity": qty,
            "sellable_quantity": avail, "cost_price": cost,
            "last_price": float(p.get("last_price", 0)),
            "market_value": float(p.get("market_value", qty * cost)),
        })

    positions_df = pd.DataFrame(pos_rows, columns=POSITION_COLUMNS) if pos_rows else pd.DataFrame(columns=POSITION_COLUMNS)

    total_mv = float(positions_df["market_value"].sum()) if not positions_df.empty else 0.0
    total_value = cash + total_mv
    prior_account = {
        "trade_date": None,
        "cash": cash, "available_cash": cash,
        "market_value": total_mv, "total_value": total_value,
        "last_run_id": None, "initial_capital": initial_capital,
    }
    return account, prior_account, positions_df


# ── Alpha V1 Candidate Shadow Observation ────────────────────────────────────


def run_alpha_v1_shadow_rebalance(
    *,
    base_dir: str | Path, run_id: str, trade_date: str,
    predictions_path: str | Path, output_dir: str | Path,
) -> ShadowRebalanceArtifacts:
    """Run shadow rebalance using Alpha V1 config and build_rank_weight_portfolio."""
    config = ALPHA_V1_CANDIDATE
    return run_shadow_rebalance(
        base_dir=base_dir,
        run_id=run_id,
        trade_date=trade_date,
        predictions_path=predictions_path,
        output_dir=output_dir,
        portfolio_fn=build_rank_weight_portfolio,
        top_n=config.portfolio.top_n,
        buffer_hold=config.portfolio.buffer_hold,
        buffer_buy=config.portfolio.buffer_buy,
        single_stock_cap=config.portfolio.single_stock_cap,
        strategy_id=config.strategy_id,
        strategy_version=config.version,
        portfolio_method="rank_weight_buffer",
    )


def run_shadow_rebalance(
    *,
    base_dir: str | Path,
    run_id: str,
    trade_date: str,
    predictions_path: str | Path,
    output_dir: str | Path,
    portfolio_fn: Callable | None = None,
    top_n: int | None = None,
    buffer_hold: int | None = None,
    buffer_buy: int | None = None,
    single_stock_cap: float | None = None,
    strategy_id: str | None = None,
    strategy_version: str | None = None,
    portfolio_method: str = "rank_weight_buffer",
) -> ShadowRebalanceArtifacts:
    """Run shadow rebalance using a portfolio function and config parameters.

    Defaults to ``build_rank_weight_portfolio`` with ``ALPHA_V1_CANDIDATE``
    portfolio config.
    """
    portfolio_fn = portfolio_fn or build_rank_weight_portfolio
    top_n = top_n if top_n is not None else ALPHA_V1_CANDIDATE.portfolio.top_n
    buffer_hold = buffer_hold if buffer_hold is not None else ALPHA_V1_CANDIDATE.portfolio.buffer_hold
    buffer_buy = buffer_buy if buffer_buy is not None else ALPHA_V1_CANDIDATE.portfolio.buffer_buy
    single_stock_cap = single_stock_cap if single_stock_cap is not None else ALPHA_V1_CANDIDATE.portfolio.single_stock_cap
    strategy_id = strategy_id or ALPHA_V1_CANDIDATE.strategy_id
    strategy_version = strategy_version or ALPHA_V1_CANDIDATE.version

    base_dir = Path(base_dir)
    output_dir = Path(output_dir)
    shadow_dir = base_dir / "shadow"
    output_dir.mkdir(parents=True, exist_ok=True)

    predictions = read_predictions(predictions_path)
    account, prior_account, _ = load_shadow_account(shadow_dir)
    instruments = sorted(set(predictions["instrument"].astype(str)) | set(account.positions.keys()))
    current_prices, market_status = fetch_market_snapshot(trade_date, instruments)
    target_weights, target_frame = build_target_weights(
        predictions, current_prices, account,
        portfolio_fn=portfolio_fn, top_n=top_n,
        buffer_hold=buffer_hold, buffer_buy=buffer_buy,
        single_stock_cap=single_stock_cap,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        portfolio_method=portfolio_method,
    )
    orders, order_intents, rebalance_audit, cash_before, market_value_before, total_value_before = build_order_intents(
        account, predictions, target_weights, current_prices, trade_date,
    )

    matcher = MatchEngine(slippage=0.0)
    results = matcher.match(orders, account, market_status, current_prices)
    account.settlement()

    buy_count = sum(1 for order in orders if order["side"] == "buy")
    sell_count = sum(1 for order in orders if order["side"] == "sell")
    filled_count = sum(1 for item in results if item["status"] == "filled")
    rejected_count = sum(1 for item in results if item["status"] == "rejected")
    skipped_count = max(len(order_intents.index) - len(orders), 0)
    turnover = float(sum(
        float(item.get("filled_amount", 0)) * float(item.get("deal_price", 0.0))
        for item in results if item["status"] == "filled"
    ))

    positions_after = positions_frame(account, current_prices)
    market_value_after = float(positions_after["market_value"].sum()) if not positions_after.empty else 0.0
    cash_after = float(account.cash)
    total_value_after = float(cash_after + market_value_after)

    target_path = output_dir / "target_weights.csv"
    order_intents_path = output_dir / "order_intents.csv"
    account_after_path = output_dir / "account_after.json"
    positions_after_path = output_dir / "positions_after.csv"
    rebalance_audit_path = output_dir / "rebalance_audit.csv"
    execution_summary_path = output_dir / "execution_summary.json"
    shadow_account_path = shadow_dir / "account.json"
    shadow_positions_path = shadow_dir / "positions.csv"
    shadow_ledger_path = shadow_dir / "ledger.csv"

    target_frame.to_csv(target_path, index=False)
    order_intents.to_csv(order_intents_path, index=False)
    rebalance_audit.to_csv(rebalance_audit_path, index=False)
    positions_after.to_csv(positions_after_path, index=False)

    account_after = {
        "trade_date": trade_date,
        "cash": cash_after,
        "available_cash": cash_after,
        "market_value": market_value_after,
        "total_value": total_value_after,
        "last_run_id": run_id,
        "initial_capital": float(prior_account.get("initial_capital", DEFAULT_INITIAL_CAPITAL)),
    }
    write_json(account_after_path, account_after)
    write_json(shadow_account_path, account_after)
    positions_after.to_csv(shadow_positions_path, index=False)

    ledger_rows = []
    for item in results:
        order = item["order"]
        qty = int(item.get("filled_amount", order.get("amount", 0)) or 0)
        price = float(item.get("deal_price", order.get("price", 0.0)) or 0.0)
        ledger_rows.append({
            "run_id": run_id,
            "trade_date": trade_date,
            "instrument": order["symbol"],
            "side": order["side"],
            "quantity": qty,
            "price": price,
            "amount": float(qty * price),
            "fee": float(item.get("fee", 0.0) or 0.0),
            "status": item["status"],
            "reason": item.get("reason", "rebalance_to_target_weight"),
        })
    append_ledger(shadow_ledger_path, ledger_rows)

    execution_summary = {
        "trade_date": trade_date,
        "run_id": run_id,
        "status": "success",
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "portfolio_method": portfolio_method,
        "portfolio_params": {
            "portfolio_fn": portfolio_fn.__name__,
            "top_n": top_n,
            "buffer_hold": buffer_hold,
            "buffer_buy": buffer_buy,
            "single_stock_cap": single_stock_cap,
        },
        "turnover_buffer": DEFAULT_TURNOVER_BUFFER,
        "price_mode": DEFAULT_PRICE_MODE,
        "rebalance_mode": DEFAULT_REBALANCE_MODE,
        "order_count": len(orders),
        "buy_count": buy_count,
        "sell_count": sell_count,
        "skipped_count": skipped_count,
        "filled_count": filled_count,
        "rejected_count": rejected_count,
        "cash_before": cash_before,
        "cash_after": cash_after,
        "market_value_before": market_value_before,
        "market_value_after": market_value_after,
        "total_value_before": total_value_before,
        "total_value_after": total_value_after,
        "turnover": turnover,
        "no_real_orders": True,
        "no_trade_reason_counts": {
            str(key): int(value)
            for key, value in pd.Series(rebalance_audit["reason"]).value_counts().items()
        } if not rebalance_audit.empty else {},
        "notes": [
            "shadow_only",
            f"price_mode={DEFAULT_PRICE_MODE}",
            "no_real_order_submission",
        ],
    }
    write_json(execution_summary_path, execution_summary)

    return ShadowRebalanceArtifacts(
        trade_date=trade_date,
        run_id=run_id,
        status="success",
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        portfolio_method=portfolio_method,
        top_n=top_n,
        buffer_hold=buffer_hold,
        buffer_buy=buffer_buy,
        single_stock_cap=single_stock_cap,
        turnover_buffer=DEFAULT_TURNOVER_BUFFER,
        price_mode=DEFAULT_PRICE_MODE,
        rebalance_mode=DEFAULT_REBALANCE_MODE,
        target_weights_path=str(target_path),
        order_intents_path=str(order_intents_path),
        execution_summary_path=str(execution_summary_path),
        account_after_path=str(account_after_path),
        positions_after_path=str(positions_after_path),
        ledger_rows_path="",
        shadow_account_path=str(shadow_account_path),
        shadow_positions_path=str(shadow_positions_path),
        shadow_ledger_path=str(shadow_ledger_path),
        rebalance_audit_path=str(rebalance_audit_path),
        order_count=len(orders),
        buy_count=buy_count,
        sell_count=sell_count,
        skipped_count=skipped_count,
        filled_count=filled_count,
        rejected_count=rejected_count,
        turnover=turnover,
        cash_after=cash_after,
        total_value_after=total_value_after,
    )


def write_failed_execution_summary(
    *,
    output_dir: str | Path,
    trade_date: str,
    run_id: str,
    error: str,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Write a failed execution summary with alpha_v1 defaults."""
    return _public_write_failed_execution_summary(
        output_dir=output_dir,
        trade_date=trade_date,
        run_id=run_id,
        error=error,
        extra=extra,
        strategy_id=ALPHA_V1_CANDIDATE.strategy_id,
        strategy_version=ALPHA_V1_CANDIDATE.version,
    )


def build_alpha_v1_plan(
    *,
    base_dir: str | Path,
    trade_date: str,
    reference_date: str,
    predictions_path: str | Path,
    output_dir: str | Path,
    db_path: str | None = None,
) -> Path:
    """Build alpha_v1 trading plan from predictions, without executing.

    Delegates to ``build_plan_from_predictions`` with alpha_v1 config.
    Supports optional ledger-based account loading via *db_path*.
    """
    config = ALPHA_V1_CANDIDATE
    predictions = read_predictions(predictions_path)
    shadow_dir = Path(base_dir) / "shadow"

    if db_path and Path(db_path).exists():
        svc = LedgerService(db_path)
        account_id = f"shadow_{config.strategy_id}"
        try:
            init_cap = svc.get_initial_cash(account_id) or DEFAULT_INITIAL_CAPITAL
        except Exception:
            init_cap = DEFAULT_INITIAL_CAPITAL
        account, prior_account, _ = _load_account_from_ledger(svc, account_id, init_cap)
    else:
        account, prior_account, _ = load_shadow_account(shadow_dir)

    return build_plan_from_predictions(
        shadow_dir=shadow_dir,
        trade_date=trade_date,
        predictions=predictions,
        output_dir=Path(output_dir),
        portfolio_fn=build_rank_weight_portfolio,
        top_n=config.portfolio.top_n,
        buffer_hold=config.portfolio.buffer_hold,
        buffer_buy=config.portfolio.buffer_buy,
        single_stock_cap=config.portfolio.single_stock_cap,
        strategy_id=config.strategy_id,
        strategy_version=config.version,
        portfolio_method="rank_weight_buffer",
        reference_date=reference_date,
        account=account,
        prior_account=prior_account,
    )


def execute_alpha_v1_plan(
    *,
    base_dir: str | Path,
    plan_dir: str | Path,
    execution_date: str,
    output_dir: str | Path,
    debug_run: bool = False,
    db_path: str | None = None,
) -> ShadowRebalanceArtifacts:
    """Execute a saved alpha_v1 plan using execution_date's OPEN price.

    Wraps ``execute_shadow_plan`` with ``run_id=f"alpha_v1_execute_{execution_date}"``.
    """
    return execute_shadow_plan(
        base_dir=base_dir,
        plan_dir=plan_dir,
        execution_date=execution_date,
        output_dir=output_dir,
        debug_run=debug_run,
        db_path=db_path,
        run_id=f"alpha_v1_execute_{execution_date}",
    )
