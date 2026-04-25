from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.data.adapter import QlibAdapter
from qsys.strategy.engine import StrategyEngine
from qsys.trader.account import Account, Position
from qsys.trader.diff import OrderGenerator
from qsys.trader.matcher import MatchEngine

DEFAULT_INITIAL_CAPITAL = 1_000_000.0
DEFAULT_TOP_K = 5
DEFAULT_TURNOVER_BUFFER = 0.0
DEFAULT_STRATEGY_VARIANT = "top5_equal_weight"
DEFAULT_PRICE_MODE = "shadow_mark_price"
DEFAULT_REBALANCE_MODE = "daily"


class ShadowRebalanceError(RuntimeError):
    pass


@dataclass
class ShadowRebalanceArtifacts:
    trade_date: str
    run_id: str
    status: str
    strategy_variant: str
    top_k: int
    turnover_buffer: float
    price_mode: str
    rebalance_mode: str
    target_weights_path: str
    order_intents_path: str
    execution_summary_path: str
    account_after_path: str
    positions_after_path: str
    shadow_account_path: str
    shadow_positions_path: str
    shadow_ledger_path: str
    order_count: int
    buy_count: int
    sell_count: int
    skipped_count: int
    filled_count: int
    rejected_count: int
    turnover: float
    cash_after: float
    total_value_after: float


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _read_predictions(predictions_path: str | Path) -> pd.DataFrame:
    frame = pd.read_csv(predictions_path)
    required = {"trade_date", "instrument", "score", "model_name", "mainline_object_name"}
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
        }, pd.DataFrame(columns=["instrument", "quantity", "sellable_quantity", "cost_price", "last_price", "market_value"])

    payload = json.loads(account_path.read_text(encoding="utf-8"))
    account = Account(init_cash=float(payload.get("initial_capital", DEFAULT_INITIAL_CAPITAL)))
    account.cash = float(payload.get("cash", payload.get("available_cash", DEFAULT_INITIAL_CAPITAL)))
    if positions_path.exists():
        positions = pd.read_csv(positions_path)
    else:
        positions = pd.DataFrame(columns=["instrument", "quantity", "sellable_quantity", "cost_price", "last_price", "market_value"])

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


def _fetch_market_snapshot(trade_date: str, instruments: list[str]) -> tuple[dict[str, float], pd.DataFrame]:
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
    frame["limit_up"] = pd.to_numeric(frame["limit_up"], errors="coerce")
    frame["limit_down"] = pd.to_numeric(frame["limit_down"], errors="coerce")
    frame["is_suspended"] = frame["is_suspended"].fillna(0).astype(bool)
    frame["is_limit_up"] = (frame["limit_up"] > 0.01) & (frame["close"] >= frame["limit_up"])
    frame["is_limit_down"] = (frame["limit_down"] > 0.01) & (frame["close"] <= frame["limit_down"])
    frame = frame.dropna(subset=["close"])
    if frame.empty:
        raise ShadowRebalanceError(f"no valid close prices for {trade_date}")
    market_status = frame.set_index("instrument")[["is_suspended", "is_limit_up", "is_limit_down"]]
    current_prices = frame.set_index("instrument")["close"].astype(float).to_dict()
    return current_prices, market_status


def _build_target_weights(predictions: pd.DataFrame, current_prices: dict[str, float]) -> tuple[dict[str, float], pd.DataFrame]:
    filtered = predictions[predictions["instrument"].isin(current_prices)].copy()
    if filtered.empty:
        raise ShadowRebalanceError("no predictions remain after joining market prices")
    scores = filtered.set_index("instrument")["score"]
    strategy = StrategyEngine(top_k=DEFAULT_TOP_K, method="equal_weight")
    weights = strategy.generate_target_weights(scores)
    rows = []
    for instrument, target_weight in sorted(weights.items()):
        score = float(scores.loc[instrument])
        sample = filtered.loc[filtered["instrument"] == instrument].iloc[0]
        rows.append(
            {
                "trade_date": str(sample["trade_date"]),
                "instrument": instrument,
                "score": score,
                "target_weight": float(target_weight),
                "model_name": str(sample.get("model_name", "")),
                "mainline_object_name": str(sample.get("mainline_object_name", "")),
                "strategy_variant": DEFAULT_STRATEGY_VARIANT,
            }
        )
    return weights, pd.DataFrame(rows)


def _build_order_intents(account: Account, target_weights: dict[str, float], current_prices: dict[str, float], trade_date: str) -> tuple[list[dict[str, Any]], pd.DataFrame, float, float, float]:
    total_value_before = float(account.get_total_equity(current_prices))
    market_value_before = float(account.get_market_value(current_prices))
    cash_before = float(account.cash)
    order_gen = OrderGenerator(min_trade_buffer_ratio=DEFAULT_TURNOVER_BUFFER)
    orders = order_gen.generate_orders(target_weights, account, current_prices)
    rows = []
    for order in orders:
        instrument = order["symbol"]
        price = float(current_prices[instrument])
        current_qty = account.positions.get(instrument).total_amount if instrument in account.positions else 0
        current_value = float(current_qty * price)
        target_weight = float(target_weights.get(instrument, 0.0))
        target_value = float(total_value_before * target_weight)
        diff_value = float(target_value - current_value)
        rows.append(
            {
                "trade_date": trade_date,
                "instrument": instrument,
                "side": order["side"],
                "target_weight": target_weight,
                "current_weight": float(current_value / total_value_before) if total_value_before > 0 else 0.0,
                "target_value": target_value,
                "current_value": current_value,
                "diff_value": diff_value,
                "requested_qty": int(order["amount"]),
                "reason": "rebalance_to_target_weight",
            }
        )
    return orders, pd.DataFrame(rows), cash_before, market_value_before, total_value_before


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
    return pd.DataFrame(rows, columns=["instrument", "quantity", "sellable_quantity", "cost_price", "last_price", "market_value"])


def _append_ledger(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["run_id", "trade_date", "instrument", "side", "quantity", "price", "amount", "fee", "status", "reason"]
    exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


def run_shadow_rebalance(*, base_dir: str | Path, run_id: str, trade_date: str, predictions_path: str | Path, output_dir: str | Path) -> ShadowRebalanceArtifacts:
    base_dir = Path(base_dir)
    output_dir = Path(output_dir)
    shadow_dir = base_dir / "shadow"
    output_dir.mkdir(parents=True, exist_ok=True)

    predictions = _read_predictions(predictions_path)
    account, prior_account, _ = _load_shadow_account(shadow_dir)
    instruments = sorted(set(predictions["instrument"].astype(str)) | set(account.positions.keys()))
    current_prices, market_status = _fetch_market_snapshot(trade_date, instruments)
    target_weights, target_frame = _build_target_weights(predictions, current_prices)
    orders, order_intents, cash_before, market_value_before, total_value_before = _build_order_intents(account, target_weights, current_prices, trade_date)

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

    strategy_variant = DEFAULT_STRATEGY_VARIANT
    target_path = output_dir / "target_weights.csv"
    order_intents_path = output_dir / "order_intents.csv"
    account_after_path = output_dir / "account_after.json"
    positions_after_path = output_dir / "positions_after.csv"
    execution_summary_path = output_dir / "execution_summary.json"
    shadow_account_path = shadow_dir / "account.json"
    shadow_positions_path = shadow_dir / "positions.csv"
    shadow_ledger_path = shadow_dir / "ledger.csv"

    target_frame.to_csv(target_path, index=False)
    order_intents.to_csv(order_intents_path, index=False)
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
    _write_json(account_after_path, account_after)
    _write_json(shadow_account_path, account_after)
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
        "strategy_variant": strategy_variant,
        "top_k": DEFAULT_TOP_K,
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
        "notes": [
            "shadow_only",
            "price_mode=shadow_mark_price",
            "no_real_order_submission",
        ],
    }
    _write_json(execution_summary_path, execution_summary)

    return ShadowRebalanceArtifacts(
        trade_date=trade_date,
        run_id=run_id,
        status="success",
        strategy_variant=strategy_variant,
        top_k=DEFAULT_TOP_K,
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


def write_failed_execution_summary(*, output_dir: str | Path, trade_date: str, run_id: str, error: str) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    return _write_json(
        output_dir / "execution_summary.json",
        {
            "trade_date": trade_date,
            "run_id": run_id,
            "status": "failed",
            "strategy_variant": DEFAULT_STRATEGY_VARIANT,
            "top_k": DEFAULT_TOP_K,
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
        },
    )
