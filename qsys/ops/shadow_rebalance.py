from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from qsys.utils.json_io import write_json

from qsys.backtest.portfolio import build_rank_weight_portfolio
from qsys.data.adapter import QlibAdapter
from qsys.ledger.service import LedgerService
from qsys.strategy.alpha_v1 import ALPHA_V1_CANDIDATE
from qsys.trader.account import Account, Position
from qsys.trader.diff import OrderGenerator
from qsys.trader.matcher import MatchEngine

# Default DB path for the ledger
DEFAULT_LEDGER_DB_PATH = str(Path(__file__).resolve().parent.parent.parent / "data" / "trade.db")

DEFAULT_INITIAL_CAPITAL = 1_000_000.0
DEFAULT_TURNOVER_BUFFER = 0.0
DEFAULT_PRICE_MODE = "shadow_mark_price"
DEFAULT_REBALANCE_MODE = "daily"
DEFAULT_MIN_PREDICTION_COUNT = 50
TARGET_WEIGHT_COLUMNS = [
    "trade_date",
    "instrument",
    "score",
    "rank",
    "target_weight",
    "strategy_id",
    "strategy_version",
    "portfolio_method",
    "model_name",
    "mainline_object_name",
]
ORDER_INTENT_COLUMNS = [
    "trade_date",
    "instrument",
    "side",
    "target_weight",
    "current_weight",
    "target_value",
    "current_value",
    "diff_value",
    "requested_qty",
    "reason",
]
REBALANCE_AUDIT_COLUMNS = [
    "trade_date",
    "instrument",
    "score",
    "target_weight",
    "current_weight",
    "target_value",
    "current_value",
    "diff_value",
    "requested_qty",
    "action",
    "reason",
]
POSITION_COLUMNS = [
    "instrument",
    "quantity",
    "sellable_quantity",
    "cost_price",
    "last_price",
    "market_value",
]
LEDGER_COLUMNS = [
    "run_id",
    "trade_date",
    "instrument",
    "side",
    "quantity",
    "price",
    "amount",
    "fee",
    "status",
    "reason",
]


class ShadowRebalanceError(RuntimeError):
    pass


@dataclass
class ShadowRebalanceArtifacts:
    trade_date: str
    run_id: str
    status: str
    strategy_id: str
    strategy_version: str
    portfolio_method: str
    top_n: int
    buffer_hold: int
    buffer_buy: int
    single_stock_cap: float
    turnover_buffer: float
    price_mode: str
    rebalance_mode: str
    target_weights_path: str
    order_intents_path: str
    execution_summary_path: str
    account_after_path: str
    positions_after_path: str
    ledger_rows_path: str
    shadow_account_path: str
    shadow_positions_path: str
    shadow_ledger_path: str
    rebalance_audit_path: str
    order_count: int
    buy_count: int
    sell_count: int
    skipped_count: int
    filled_count: int
    rejected_count: int
    turnover: float
    cash_after: float
    total_value_after: float


def _read_predictions(predictions_path: str | Path) -> pd.DataFrame:
    frame = pd.read_csv(predictions_path)
    required = {"trade_date", "instrument", "score"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ShadowRebalanceError(f"predictions missing required columns: {', '.join(missing)}")
    if frame.empty:
        raise ShadowRebalanceError("predictions are empty")
    frame["instrument"] = frame["instrument"].astype(str)
    frame["score"] = pd.to_numeric(frame["score"], errors="coerce")
    frame = frame.dropna(subset=["score"])
    if frame.empty:
        raise ShadowRebalanceError("predictions contain no usable scores")
    # Fill optional metadata columns for compatibility
    for col in ("model_name", "mainline_object_name"):
        if col not in frame.columns:
            frame[col] = ""
    return frame.sort_values(["score", "instrument"], ascending=[False, True]).reset_index(drop=True)


def _load_shadow_account(shadow_dir: Path) -> tuple[Account, dict[str, Any], pd.DataFrame]:
    account_path = shadow_dir / "account.json"
    positions_path = shadow_dir / "positions.csv"
    if not account_path.exists():
        account = Account(init_cash=DEFAULT_INITIAL_CAPITAL)
        return account, {
            "trade_date": None,
            "cash": DEFAULT_INITIAL_CAPITAL,
            "available_cash": DEFAULT_INITIAL_CAPITAL,
            "market_value": 0.0,
            "total_value": DEFAULT_INITIAL_CAPITAL,
            "last_run_id": None,
        }, pd.DataFrame(columns=POSITION_COLUMNS)

    payload = json.loads(account_path.read_text(encoding="utf-8"))
    account = Account(init_cash=float(payload.get("initial_capital", DEFAULT_INITIAL_CAPITAL)))
    account.cash = float(payload.get("cash", payload.get("available_cash", DEFAULT_INITIAL_CAPITAL)))
    if positions_path.exists():
        positions = pd.read_csv(positions_path)
    else:
        positions = pd.DataFrame(columns=POSITION_COLUMNS)

    for row in positions.to_dict("records"):
        instrument = str(row.get("instrument", ""))
        quantity = int(float(row.get("quantity", 0) or 0))
        if not instrument or quantity <= 0:
            continue
        sellable_quantity = int(float(row.get("sellable_quantity", quantity) or quantity))
        cost_price = float(row.get("cost_price", 0.0) or 0.0)
        account.positions[instrument] = Position(
            symbol=instrument,
            total_amount=quantity,
            sellable_amount=max(sellable_quantity, 0),
            avg_cost=cost_price,
        )
    return account, payload, positions


def _fetch_market_snapshot(trade_date: str, instruments: list[str], price_col: str = "close") -> tuple[dict[str, float], pd.DataFrame]:
    adapter = QlibAdapter()
    adapter.init_qlib()
    market = adapter.get_features(instruments, ["$close", "$open", "$factor", "$paused", "$high_limit", "$low_limit"], start_time=trade_date, end_time=trade_date)
    if market is None or market.empty:
        raise ShadowRebalanceError(f"no market data for {trade_date}")
    market = market.copy()
    market.columns = ["close", "open", "factor", "is_suspended", "limit_up", "limit_down"]
    if isinstance(market.index, pd.MultiIndex) and market.index.names == ["datetime", "instrument"]:
        market = market.swaplevel().sort_index()
    elif isinstance(market.index, pd.MultiIndex) and market.index.names != ["instrument", "datetime"]:
        market = market.reorder_levels([1, 0]).sort_index()
    frame = market.reset_index()
    frame = frame[frame["datetime"].astype(str).str.startswith(trade_date)]
    if frame.empty:
        raise ShadowRebalanceError(f"no market snapshot rows for {trade_date}")
    frame = frame.sort_values(["instrument", "datetime"]).drop_duplicates(subset=["instrument"], keep="last")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["open"] = pd.to_numeric(frame["open"], errors="coerce")
    frame["limit_up"] = pd.to_numeric(frame["limit_up"], errors="coerce")
    frame["limit_down"] = pd.to_numeric(frame["limit_down"], errors="coerce")
    frame["is_suspended"] = frame["is_suspended"].fillna(0).astype(bool)
    frame["is_limit_up"] = (frame["limit_up"] > 0.01) & (frame[price_col] >= frame["limit_up"])
    frame["is_limit_down"] = (frame["limit_down"] > 0.01) & (frame[price_col] <= frame["limit_down"])
    frame = frame.dropna(subset=[price_col])
    if frame.empty:
        raise ShadowRebalanceError(f"no valid close prices for {trade_date}")
    market_status = frame.set_index("instrument")[["is_suspended", "is_limit_up", "is_limit_down"]]
    current_prices = frame.set_index("instrument")[price_col].astype(float).to_dict()
    return current_prices, market_status


def _build_target_weights(
    predictions: pd.DataFrame,
    current_prices: dict[str, float],
    account: Account,
    *,
    portfolio_fn: Callable,
    top_n: int,
    buffer_hold: int,
    buffer_buy: int,
    single_stock_cap: float,
    strategy_id: str,
    strategy_version: str,
    portfolio_method: str = "rank_weight_buffer",
) -> tuple[dict[str, float], pd.DataFrame]:
    filtered = predictions[predictions["instrument"].isin(current_prices)].copy()
    if filtered.empty:
        raise ShadowRebalanceError("no predictions remain after joining market prices")
    scores = filtered.set_index("instrument")["score"]
    weights = portfolio_fn(
        scores,
        account,
        top_n=top_n,
        buffer_hold=buffer_hold,
        buffer_buy=buffer_buy,
        single_stock_cap=single_stock_cap,
    )
    ranked = scores.sort_values(ascending=False)
    ranks = pd.Series(range(1, len(ranked) + 1), index=ranked.index)
    rows = []
    for instrument, target_weight in sorted(weights.items()):
        score = float(scores.loc[instrument]) if instrument in scores.index else 0.0
        rank = int(ranks.loc[instrument]) if instrument in ranks.index else 0
        sample = filtered.loc[filtered["instrument"] == instrument].iloc[0]
        rows.append(
            {
                "trade_date": str(sample["trade_date"]),
                "instrument": instrument,
                "score": score,
                "rank": rank,
                "target_weight": float(target_weight),
                "strategy_id": strategy_id,
                "strategy_version": strategy_version,
                "portfolio_method": portfolio_method,
                "model_name": str(sample.get("model_name", "")),
                "mainline_object_name": str(sample.get("mainline_object_name", "")),
            }
        )
    return weights, pd.DataFrame(rows, columns=TARGET_WEIGHT_COLUMNS)


def _build_order_intents(account: Account, predictions: pd.DataFrame, target_weights: dict[str, float], current_prices: dict[str, float], trade_date: str) -> tuple[list[dict[str, Any]], pd.DataFrame, pd.DataFrame, float, float, float]:
    total_value_before = float(account.get_total_equity(current_prices))
    market_value_before = float(account.get_market_value(current_prices))
    cash_before = float(account.cash)
    order_gen = OrderGenerator(min_trade_buffer_ratio=DEFAULT_TURNOVER_BUFFER)
    orders = order_gen.generate_orders(target_weights, account, current_prices)
    order_lookup = {(order["symbol"], order["side"]): order for order in orders}
    score_lookup = predictions.set_index("instrument")["score"].to_dict()
    rows = []
    audit_rows = []
    tracked_instruments = sorted(set(target_weights) | set(account.positions.keys()))
    for instrument in tracked_instruments:
        price = float(current_prices.get(instrument, 0.0) or 0.0)
        current_qty = account.positions.get(instrument).total_amount if instrument in account.positions else 0
        current_value = float(current_qty * price)
        target_weight = float(target_weights.get(instrument, 0.0))
        target_value = float(total_value_before * target_weight)
        diff_value = float(target_value - current_value)
        current_weight = float(current_value / total_value_before) if total_value_before > 0 else 0.0
        score = score_lookup.get(instrument)
        buy_order = order_lookup.get((instrument, "buy"))
        sell_order = order_lookup.get((instrument, "sell"))
        order = buy_order or sell_order
        requested_qty = int(order["amount"]) if order else 0
        if order is not None:
            action = order["side"]
            reason = "rebalance_to_target_weight"
            rows.append(
                {
                    "trade_date": trade_date,
                    "instrument": instrument,
                    "side": action,
                    "target_weight": target_weight,
                    "current_weight": current_weight,
                    "target_value": target_value,
                    "current_value": current_value,
                    "diff_value": diff_value,
                    "requested_qty": requested_qty,
                    "reason": reason,
                }
            )
        else:
            if target_weight <= 0 and current_qty <= 0:
                action = "skip"
                reason = "no_target_selected"
            elif abs(diff_value) < max(price, 1.0) * 100:
                action = "hold"
                reason = "diff_below_lot_size"
            else:
                action = "hold"
                reason = "already_at_target"
        audit_rows.append(
            {
                "trade_date": trade_date,
                "instrument": instrument,
                "score": float(score) if score is not None else None,
                "target_weight": target_weight,
                "current_weight": current_weight,
                "target_value": target_value,
                "current_value": current_value,
                "diff_value": diff_value,
                "requested_qty": requested_qty,
                "action": action,
                "reason": reason,
            }
        )
    return orders, pd.DataFrame(rows, columns=ORDER_INTENT_COLUMNS), pd.DataFrame(audit_rows, columns=REBALANCE_AUDIT_COLUMNS), cash_before, market_value_before, total_value_before


def _positions_frame(account: Account, current_prices: dict[str, float]) -> pd.DataFrame:
    rows = []
    for instrument in sorted(account.positions):
        pos = account.positions[instrument]
        last_price = float(current_prices.get(instrument, 0.0) or 0.0)
        market_value = float(pos.total_amount * last_price)
        rows.append(
            {
                "instrument": instrument,
                "quantity": int(pos.total_amount),
                "sellable_quantity": int(pos.sellable_amount),
                "cost_price": float(pos.avg_cost),
                "last_price": last_price,
                "market_value": market_value,
            }
        )
    return pd.DataFrame(rows, columns=POSITION_COLUMNS)


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


def _append_ledger(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = LEDGER_COLUMNS
    exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


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
    portfolio config, so the generic daily pipeline and the alpha_v1 wrapper
    share the same portfolio logic.
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

    predictions = _read_predictions(predictions_path)
    account, prior_account, _ = _load_shadow_account(shadow_dir)
    instruments = sorted(set(predictions["instrument"].astype(str)) | set(account.positions.keys()))
    current_prices, market_status = _fetch_market_snapshot(trade_date, instruments)
    target_weights, target_frame = _build_target_weights(
        predictions, current_prices, account,
        portfolio_fn=portfolio_fn, top_n=top_n,
        buffer_hold=buffer_hold, buffer_buy=buffer_buy,
        single_stock_cap=single_stock_cap,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        portfolio_method=portfolio_method,
    )
    orders, order_intents, rebalance_audit, cash_before, market_value_before, total_value_before = _build_order_intents(account, predictions, target_weights, current_prices, trade_date)

    matcher = MatchEngine(slippage=0.0)
    results = matcher.match(orders, account, market_status, current_prices)
    account.settlement()

    buy_count = sum(1 for order in orders if order["side"] == "buy")
    sell_count = sum(1 for order in orders if order["side"] == "sell")
    filled_count = sum(1 for item in results if item["status"] == "filled")
    rejected_count = sum(1 for item in results if item["status"] == "rejected")
    skipped_count = max(len(order_intents.index) - len(orders), 0)
    turnover = float(sum(float(item.get("filled_amount", 0)) * float(item.get("deal_price", 0.0)) for item in results if item["status"] == "filled"))

    positions_after = _positions_frame(account, current_prices)
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
        ledger_rows.append(
            {
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
            }
        )
    _append_ledger(shadow_ledger_path, ledger_rows)

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
        "no_trade_reason_counts": {str(key): int(value) for key, value in pd.Series(rebalance_audit["reason"]).value_counts().items()} if not rebalance_audit.empty else {},
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


def write_failed_execution_summary(*, output_dir: str | Path, trade_date: str, run_id: str, error: str, extra: dict[str, Any] | None = None) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    return write_json(
        output_dir / "execution_summary.json",
        {
            "trade_date": trade_date,
            "run_id": run_id,
            "status": "failed",
            "strategy_id": ALPHA_V1_CANDIDATE.strategy_id,
            "strategy_version": ALPHA_V1_CANDIDATE.version,
            "turnover_buffer": DEFAULT_TURNOVER_BUFFER,
            "price_mode": DEFAULT_PRICE_MODE,
            "rebalance_mode": DEFAULT_REBALANCE_MODE,
            "order_count": 0,
            "buy_count": 0,
            "sell_count": 0,
            "skipped_count": 0,
            "filled_count": 0,
            "rejected_count": 0,
            "cash_before": None,
            "cash_after": None,
            "market_value_before": None,
            "market_value_after": None,
            "total_value_before": None,
            "total_value_after": None,
            "turnover": 0.0,
            "error": error,
            "notes": ["shadow_rebalance_failed"],
            **(extra or {}),
        },
    )


# ── Alpha V1 Candidate Shadow Observation ──


def run_alpha_v1_shadow_rebalance(*, base_dir: str | Path, run_id: str, trade_date: str,
                                   predictions_path: str | Path, output_dir: str | Path) -> ShadowRebalanceArtifacts:
    """Run shadow rebalance using Alpha V1 config and build_rank_weight_portfolio.

    Reads portfolio parameters from ALPHA_V1_CANDIDATE.portfolio (top_n,
    buffer_hold, buffer_buy, single_stock_cap) and passes
    ``build_rank_weight_portfolio`` as the portfolio function — the same
    function used by the rolling backtest engine for target-weight
    construction.
    """
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

    Reads predictions, loads current shadow holdings, fetches close prices
    for reference_date, computes target weights and order intents.
    Saves to output_dir/plan/ — no MatchEngine, no shadow account writes.
    Returns the plan directory.
    """
    plan_dir = Path(output_dir) / "plan"
    plan_dir.mkdir(parents=True, exist_ok=True)
    config = ALPHA_V1_CANDIDATE

    predictions = _read_predictions(predictions_path)
    shadow_dir = Path(base_dir) / "shadow"
    if db_path and Path(db_path).exists():
        _service = LedgerService(db_path)
        account_id = f"shadow_{config.strategy_id}"
        try:
            init_cap = _service.get_initial_cash(account_id) or DEFAULT_INITIAL_CAPITAL
        except Exception:
            init_cap = DEFAULT_INITIAL_CAPITAL
        account, prior_account, _ = _load_account_from_ledger(_service, account_id, init_cap)
    else:
        account, prior_account, _ = _load_shadow_account(shadow_dir)
    instruments = sorted(set(predictions["instrument"].astype(str)) | set(account.positions.keys()))

    current_prices, market_status = _fetch_market_snapshot(
        reference_date, instruments, price_col="close",
    )
    target_weights, target_frame = _build_target_weights(
        predictions, current_prices, account,
        portfolio_fn=build_rank_weight_portfolio,
        top_n=config.portfolio.top_n,
        buffer_hold=config.portfolio.buffer_hold,
        buffer_buy=config.portfolio.buffer_buy,
        single_stock_cap=config.portfolio.single_stock_cap,
        strategy_id=config.strategy_id,
        strategy_version=config.version,
    )
    orders, order_intents, rebalance_audit, cash_before, market_value_before, total_value_before = _build_order_intents(
        account, predictions, target_weights, current_prices, trade_date,
    )

    target_frame.to_csv(plan_dir / "target_weights.csv", index=False)
    order_intents.to_csv(plan_dir / "order_intents.csv", index=False)
    rebalance_audit.to_csv(plan_dir / "rebalance_audit.csv", index=False)
    write_json(plan_dir / "plan_meta.json", {
        "trade_date": trade_date,
        "reference_date": reference_date,
        "strategy_id": config.strategy_id,
        "strategy_version": config.version,
        "portfolio_method": "rank_weight_buffer",
        "top_n": config.portfolio.top_n,
        "buffer_hold": config.portfolio.buffer_hold,
        "buffer_buy": config.portfolio.buffer_buy,
        "single_stock_cap": config.portfolio.single_stock_cap,
        "cash_before": cash_before,
        "market_value_before": market_value_before,
        "total_value_before": total_value_before,
        "buy_count": len([o for o in orders if o["side"] == "buy"]),
        "sell_count": len([o for o in orders if o["side"] == "sell"]),
        "total_orders": len(orders),
        "build_ts": datetime.now().isoformat(),
    })

    print(f"  ✅ Plan built: {len(orders)} orders ({len([o for o in orders if o['side'] == 'buy'])} buy / "
          f"{len([o for o in orders if o['side'] == 'sell'])} sell), "
          f"value=¥{total_value_before:_.0f}, cash=¥{cash_before:_.0f}")
    return plan_dir


def _write_execution_to_ledger(
    db_path: str,
    execution_date: str,
    strategy_id: str,
    orders: list[dict[str, Any]],
    ledger_rows: list[dict[str, Any]],
    results: list[dict[str, Any]],
    close_prices: dict[str, float],
    cash_after: float,
    market_value_after: float,
    total_value_after: float,
    positions_after: pd.DataFrame,
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
) -> None:
    """Write execution results to SQLite ledger.

    Called from execute_alpha_v1_plan() when db_path is provided.
    This is the single point where fills, orders, and snapshots
    enter the ledger as source of truth.

    Run-idempotency rules:
    - If run_id already exists with status "completed": skip all writes (return early).
    - If run_id exists but not completed: re-open with force=True, then write.
    - If run_id does not exist: create normally, then write.
    """
    service = LedgerService(db_path)
    account_id = f"shadow_{strategy_id}"
    run_id = f"{execution_date}.{strategy_id}.shadow"
    trade_date = execution_date

    # Ensure account exists
    service.create_account(account_id, "shadow", initial_capital)

    # Check run existence
    existing_run = service.get_run(run_id)
    if existing_run:
        if existing_run["status"] == "completed":
            print(f"  ⏭  Run {run_id} already completed — skip ledger write")
            service.close()
            return
        # exists but not completed — re-open with force
        print(f"  ⚠  Run {run_id} exists (status={existing_run['status']}) — force re-start")
        service.start_run(
            run_id=run_id, trade_date=trade_date,
            strategy_id=strategy_id, account_id=account_id,
            mode="postclose", force=True,
        )
    else:
        service.start_run(
            run_id=run_id, trade_date=trade_date,
            strategy_id=strategy_id, account_id=account_id,
            mode="postclose",
        )

    # Record orders
    order_dicts = []
    for o in orders:
        order_dicts.append({
            "order_id": f"ord_{execution_date}_{o['symbol']}",
            "run_id": run_id,
            "account_id": account_id,
            "strategy_id": strategy_id,
            "trade_date": trade_date,
            "symbol": o["symbol"],
            "side": "BUY" if o["side"] == "buy" else "SELL",
            "order_type": o.get("order_type", "market"),
            "quantity": int(o.get("amount", 0)),
            "limit_price": o.get("price"),
            "status": "filled",
            "reason": "plan_execution",
        })
    # Record orders (idempotent: skip if order_id already exists)
    from qsys.ledger import repository as repo
    repo.insert_orders_ignore_conflicts(service.conn, order_dicts)

    # Build fills from match results
    fill_dicts = []
    for item in results:
        o = item["order"]
        qty = int(item.get("filled_amount", o.get("amount", 0)) or 0)
        if qty <= 0:
            continue
        price = float(item.get("deal_price", o.get("price", 0.0)) or 0.0)
        fee = float(item.get("fee", 0.0) or 0.0)
        gross = qty * price
        side = "BUY" if o["side"] == "buy" else "SELL"
        net = gross + fee if side == "BUY" else gross - fee

        fill_dicts.append({
            "fill_id": f"fil_{execution_date}_{o['symbol']}",
            "order_id": f"ord_{execution_date}_{o['symbol']}",
            "run_id": run_id,
            "account_id": account_id,
            "strategy_id": strategy_id,
            "trade_date": trade_date,
            "symbol": o["symbol"],
            "side": side,
            "quantity": qty,
            "price": price,
            "gross_amount": gross,
            "commission": fee,
            "stamp_tax": 0.0,
            "slippage": 0.0,
            "net_amount": net,
            "source": "simulation",
        })

    if fill_dicts:
        service.apply_fills(run_id, fill_dicts, t_plus_one=True, idempotent=True)

    # Portfolio snapshot at close prices
    prices_dict: dict[str, float] = {}
    for _, row in positions_after.iterrows():
        sym = str(row["instrument"])
        prices_dict[sym] = close_prices.get(sym, float(row.get("last_price", 0)))
    service.create_portfolio_snapshot(run_id, trade_date, prices=prices_dict)

    service.close()



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

    Reads plan artifacts (order intents), loads current shadow account,
    executes at OPEN price via MatchEngine, then MTM at CLOSE price.
    Writes results to output_dir and delegates state persistence to
    LedgerService when db_path is provided (SQLite replaces shadow files).

    When debug_run=True, reads shadow account as input but NEVER writes
    to shadow/account.json, shadow/positions.csv, or shadow/ledger.csv.
    All derived outputs are written to output_dir only.
    """
    plan_dir = Path(plan_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    shadow_dir = Path(base_dir) / "shadow"

    plan_meta_path = plan_dir / "plan_meta.json"
    if not plan_meta_path.exists():
        raise ShadowRebalanceError(f"Plan meta not found: {plan_meta_path}")
    plan_meta = json.loads(plan_meta_path.read_text())

    order_intents = pd.read_csv(plan_dir / "order_intents.csv")
    if order_intents.empty:
        raise ShadowRebalanceError("Plan has no order intents")

    strategy_id = plan_meta.get("strategy_id", "alpha_v1")
    if db_path and Path(db_path).exists():
        _service = LedgerService(db_path)
        account_id = f"shadow_{strategy_id}"
        try:
            initial_capital = _service.get_initial_cash(account_id) or DEFAULT_INITIAL_CAPITAL
        except Exception:
            initial_capital = DEFAULT_INITIAL_CAPITAL
        account, prior_account, positions_df = _load_account_from_ledger(
            _service, account_id, initial_capital,
        )
    else:
        account, prior_account, positions_df = _load_shadow_account(shadow_dir)
    positions_before_count = len(account.positions)
    instruments = sorted(set(order_intents["instrument"].astype(str)) | set(account.positions.keys()))

    # Save before-state for --force-rerun restore
    write_json(output_dir / "account_before.json", prior_account)
    pos_before_path = output_dir / "positions_before.csv"
    if not positions_df.empty:
        positions_df.to_csv(pos_before_path, index=False)
    else:
        pd.DataFrame(columns=POSITION_COLUMNS).to_csv(pos_before_path, index=False)

    open_prices, market_status = _fetch_market_snapshot(
        execution_date, instruments, price_col="open",
    )

    orders = []
    for _, row in order_intents.iterrows():
        inst = str(row["instrument"])
        side = str(row["side"])
        qty = int(float(row.get("requested_qty", 0)))
        if qty <= 0:
            continue
        price = float(open_prices.get(inst, 0))
        if price <= 0:
            continue
        orders.append({
            "symbol": inst, "side": side, "amount": qty, "price": price,
            "order_type": "market",
            "strategy_id": plan_meta.get("strategy_id", "alpha_v1"),
        })

    if not orders:
        raise ShadowRebalanceError("No executable orders after price validation")

    matcher = MatchEngine(slippage=0.0)
    results = matcher.match(orders, account, market_status, open_prices)
    account.settlement()

    buy_count = sum(1 for o in orders if o["side"] == "buy")
    sell_count = sum(1 for o in orders if o["side"] == "sell")
    filled_count = sum(1 for r in results if r["status"] == "filled")
    rejected_count = sum(1 for r in results if r["status"] == "rejected")
    turnover = float(sum(
        float(r.get("filled_amount", 0)) * float(r.get("deal_price", 0.0))
        for r in results if r["status"] == "filled"
    ))

    close_prices, _ = _fetch_market_snapshot(execution_date, instruments, price_col="close")
    positions_after = _positions_frame(account, close_prices)
    market_value_after = float(positions_after["market_value"].sum()) if not positions_after.empty else 0.0
    cash_after = float(account.cash)
    total_value_after = float(cash_after + market_value_after)

    positions_after.to_csv(output_dir / "positions_after.csv", index=False)
    account_after = {
        "trade_date": execution_date,
        "cash": cash_after,
        "available_cash": cash_after,
        "market_value": market_value_after,
        "total_value": total_value_after,
        "last_run_id": f"alpha_v1_execute_{execution_date}",
        "initial_capital": float(prior_account.get("initial_capital", DEFAULT_INITIAL_CAPITAL)),
    }
    write_json(output_dir / "account_after.json", account_after)

    ledger_rows = []
    for item in results:
        o = item["order"]
        qty = int(item.get("filled_amount", o.get("amount", 0)) or 0)
        price = float(item.get("deal_price", o.get("price", 0.0)) or 0.0)
        ledger_rows.append({
            "run_id": f"alpha_v1_execute_{execution_date}",
            "trade_date": execution_date,
            "instrument": o["symbol"], "side": o["side"],
            "quantity": qty, "price": price, "amount": float(qty * price),
            "fee": float(item.get("fee", 0.0) or 0.0),
            "status": item["status"], "reason": item.get("reason", "plan_execution"),
        })
    pd.DataFrame(ledger_rows).to_csv(output_dir / "ledger_rows.csv", index=False)

    # ── Write to SQLite ledger (when db_path provided and not debug) ──
    if db_path and not debug_run:
        _write_execution_to_ledger(
            db_path=db_path,
            execution_date=execution_date,
            strategy_id=plan_meta.get("strategy_id", "alpha_v1"),
            orders=orders,
            ledger_rows=ledger_rows,
            results=results,
            close_prices=close_prices,
            cash_after=cash_after,
            market_value_after=market_value_after,
            total_value_after=total_value_after,
            positions_after=positions_after,
            initial_capital=float(prior_account.get("initial_capital", DEFAULT_INITIAL_CAPITAL)),
        )

    execution_summary = {
        "trade_date": execution_date,
        "run_id": f"alpha_v1_execute_{execution_date}",
        "status": "success",
        "strategy_id": plan_meta.get("strategy_id", "alpha_v1"),
        "strategy_version": plan_meta.get("strategy_version", ""),
        "portfolio_method": "plan_execution",
        "portfolio_params": {
            "price_mode": "open", "mtm_mode": "close",
            "plan_trade_date": plan_meta.get("trade_date", ""),
            "plan_reference_date": plan_meta.get("reference_date", ""),
        },
        "order_count": len(orders), "buy_count": buy_count, "sell_count": sell_count,
        "filled_count": filled_count, "rejected_count": rejected_count,
        "skipped_count": len(order_intents) - len(orders),
        "cash_before": float(prior_account.get("cash", 0)),
        "cash_after": cash_after,
        "market_value_before": float(prior_account.get("market_value", 0)),
        "market_value_after": market_value_after,
        "total_value_before": float(prior_account.get("total_value", 0)),
        "total_value_after": total_value_after,
        "turnover": turnover,
        "positions_before_count": positions_before_count,
        "positions_after_count": len(positions_after),
        "no_real_orders": True,
        "notes": [
            "executed_at_open",
            f"execution_price=open_{execution_date}",
            f"mtm_price=close_{execution_date}",
            *(["debug_run"] if debug_run else []),
        ],
    }
    write_json(output_dir / "execution_summary.json", execution_summary)

    return ShadowRebalanceArtifacts(
        trade_date=execution_date,
        run_id=f"alpha_v1_execute_{execution_date}",
        status="success",
        strategy_id=plan_meta.get("strategy_id", "alpha_v1"),
        strategy_version=plan_meta.get("strategy_version", ""),
        portfolio_method="plan_execution",
        top_n=plan_meta.get("top_n", 20),
        buffer_hold=plan_meta.get("buffer_hold", 60),
        buffer_buy=plan_meta.get("buffer_buy", 40),
        single_stock_cap=plan_meta.get("single_stock_cap", 0.07),
        turnover_buffer=0.0, price_mode="open", rebalance_mode="plan_execution",
        target_weights_path=str(plan_dir / "target_weights.csv"),
        order_intents_path=str(plan_dir / "order_intents.csv"),
        execution_summary_path=str(output_dir / "execution_summary.json"),
        account_after_path=str(output_dir / "account_after.json"),
        positions_after_path=str(output_dir / "positions_after.csv"),
        ledger_rows_path=str(output_dir / "ledger_rows.csv"),
        shadow_account_path="",
        shadow_positions_path="",
        shadow_ledger_path="",
        rebalance_audit_path=str(plan_dir / "rebalance_audit.csv"),
        order_count=len(orders), buy_count=buy_count, sell_count=sell_count,
        skipped_count=max(len(order_intents) - len(orders), 0),
        filled_count=filled_count, rejected_count=rejected_count,
        turnover=turnover, cash_after=cash_after, total_value_after=total_value_after,
    )
