"""Plan builder — build trading plans from predictions.

Public APIs for constructing target weights, order intents, and complete
trading plans.  Depends on ``market_snapshot`` and ``qsys.trader.*`` but not
on execution/ledger modules.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from qsys.backtest.portfolio import build_rank_weight_portfolio
from qsys.ops.market_snapshot import ShadowRebalanceError, fetch_market_snapshot
from qsys.trader.account import Account, Position
from qsys.trader.diff import OrderGenerator
from qsys.utils.json_io import write_json

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_INITIAL_CAPITAL = 1_000_000.0
DEFAULT_TURNOVER_BUFFER = 0.0
DEFAULT_MIN_PREDICTION_COUNT = 50

TARGET_WEIGHT_COLUMNS = [
    "trade_date", "instrument", "score", "rank", "target_weight",
    "strategy_id", "strategy_version", "portfolio_method",
    "model_name", "mainline_object_name",
]
ORDER_INTENT_COLUMNS = [
    "trade_date", "instrument", "side", "target_weight", "current_weight",
    "target_value", "current_value", "diff_value", "requested_qty", "reason",
]
REBALANCE_AUDIT_COLUMNS = [
    "trade_date", "instrument", "score", "target_weight", "current_weight",
    "target_value", "current_value", "diff_value", "requested_qty",
    "action", "reason",
]
POSITION_COLUMNS = [
    "instrument", "quantity", "sellable_quantity", "cost_price",
    "last_price", "market_value",
]


# ── Prediction I/O ───────────────────────────────────────────────────────────

def read_predictions(predictions_path: str | Path) -> pd.DataFrame:
    """Read and validate a predictions CSV.

    Returns a DataFrame sorted by score descending (with instrument as tiebreaker).
    Adds empty ``model_name`` / ``mainline_object_name`` columns if absent.
    """
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
    for col in ("model_name", "mainline_object_name"):
        if col not in frame.columns:
            frame[col] = ""
    return frame.sort_values(["score", "instrument"], ascending=[False, True]).reset_index(drop=True)


# ── Shadow account I/O ───────────────────────────────────────────────────────

def load_shadow_account(
    shadow_dir: Path,
) -> tuple[Account, dict[str, Any], pd.DataFrame]:
    """Load or create a shadow account from ``account.json`` / ``positions.csv``.

    Returns (Account, prior_account_dict, positions_DataFrame).
    """
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


# ── Target weights ───────────────────────────────────────────────────────────

def build_target_weights(
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
    """Build target-weight map and DataFrame from predictions and market prices.

    Returns (weights_dict, target_weights_DataFrame).
    """
    filtered = predictions[predictions["instrument"].isin(current_prices)].copy()
    if filtered.empty:
        raise ShadowRebalanceError("no predictions remain after joining market prices")
    scores = filtered.set_index("instrument")["score"]
    weights = portfolio_fn(
        scores, account,
        top_n=top_n, buffer_hold=buffer_hold, buffer_buy=buffer_buy,
        single_stock_cap=single_stock_cap,
    )
    ranked = scores.sort_values(ascending=False)
    ranks = pd.Series(range(1, len(ranked) + 1), index=ranked.index)
    rows = []
    for instrument, target_weight in sorted(weights.items()):
        score = float(scores.loc[instrument]) if instrument in scores.index else 0.0
        rank = int(ranks.loc[instrument]) if instrument in ranks.index else 0
        sample = filtered.loc[filtered["instrument"] == instrument].iloc[0]
        rows.append({
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
        })
    return weights, pd.DataFrame(rows, columns=TARGET_WEIGHT_COLUMNS)


# ── Order intents ────────────────────────────────────────────────────────────

def build_order_intents(
    account: Account,
    predictions: pd.DataFrame,
    target_weights: dict[str, float],
    current_prices: dict[str, float],
    trade_date: str,
) -> tuple[list[dict[str, Any]], pd.DataFrame, pd.DataFrame, float, float, float]:
    """Generate order intents from target weights and current account state.

    Returns (orders, order_intents_df, rebalance_audit_df,
             cash_before, market_value_before, total_value_before).
    """
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
            rows.append({
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
            })
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
        audit_rows.append({
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
        })
    return orders, pd.DataFrame(rows, columns=ORDER_INTENT_COLUMNS), pd.DataFrame(audit_rows, columns=REBALANCE_AUDIT_COLUMNS), cash_before, market_value_before, total_value_before


# ── Composite plan builder ───────────────────────────────────────────────────

def build_plan_from_predictions(
    *,
    shadow_dir: Path,
    trade_date: str,
    predictions: pd.DataFrame,
    output_dir: Path,
    portfolio_fn: Callable,
    top_n: int,
    buffer_hold: int,
    buffer_buy: int,
    single_stock_cap: float,
    strategy_id: str,
    strategy_version: str,
    portfolio_method: str = "rank_weight_buffer",
    reference_date: str | None = None,
    account: Account | None = None,
    prior_account: dict | None = None,
) -> Path:
    """Build a complete trading plan from predictions (no execution).

    Writes 4 files to ``output_dir/plan/``: ``target_weights.csv``,
    ``order_intents.csv``, ``rebalance_audit.csv``, ``plan_meta.json``.

    When ``account``/``prior_account`` are provided, skips loading from
    ``shadow_dir`` (used by alpha_v1's ledger-based loading path).
    """
    plan_dir = Path(output_dir) / "plan"
    plan_dir.mkdir(parents=True, exist_ok=True)

    # Load account from shadow dir if not provided
    if account is None:
        account, prior_account, _ = load_shadow_account(shadow_dir)

    instruments = sorted(
        set(predictions["instrument"].astype(str))
        | set(account.positions.keys())
    )

    ref_date = reference_date or trade_date
    current_prices, _market_status = fetch_market_snapshot(ref_date, instruments)

    target_weights, target_frame = build_target_weights(
        predictions, current_prices, account,
        portfolio_fn=portfolio_fn,
        top_n=top_n, buffer_hold=buffer_hold, buffer_buy=buffer_buy,
        single_stock_cap=single_stock_cap,
        strategy_id=strategy_id, strategy_version=strategy_version,
        portfolio_method=portfolio_method,
    )
    orders, order_intents, rebalance_audit, cash_before, mv_before, tv_before = (
        build_order_intents(account, predictions, target_weights, current_prices, trade_date)
    )

    target_frame.to_csv(plan_dir / "target_weights.csv", index=False)
    order_intents.to_csv(plan_dir / "order_intents.csv", index=False)
    rebalance_audit.to_csv(plan_dir / "rebalance_audit.csv", index=False)
    write_json(plan_dir / "plan_meta.json", {
        "trade_date": trade_date,
        "reference_date": ref_date,
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "portfolio_method": portfolio_method,
        "top_n": top_n,
        "buffer_hold": buffer_hold,
        "buffer_buy": buffer_buy,
        "single_stock_cap": single_stock_cap,
        "cash_before": cash_before,
        "market_value_before": mv_before,
        "total_value_before": tv_before,
        "buy_count": len([o for o in orders if o["side"] == "buy"]),
        "sell_count": len([o for o in orders if o["side"] == "sell"]),
        "total_orders": len(orders),
        "build_ts": datetime.now().isoformat(),
    })

    print(f"  ✅ Plan built: {len(orders)} orders "
          f"({len([o for o in orders if o['side'] == 'buy'])} buy / "
          f"{len([o for o in orders if o['side'] == 'sell'])} sell)")
    return plan_dir
