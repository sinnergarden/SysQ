from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

STAGING_STAGE = "staging"


@dataclass(frozen=True)
class StagingResult:
    orders: pd.DataFrame
    reason_codes: list[dict[str, Any]]


def stage_orders(
    target_weights: pd.DataFrame,
    broker_snapshot: Mapping[str, Any],
    market_data: pd.DataFrame,
    config: Mapping[str, Any] | None = None,
) -> StagingResult:
    targets = _normalize_target_weights(target_weights)
    market = _normalize_market_data(market_data)
    account_snapshot = dict((broker_snapshot or {}).get("account_snapshot") or {})
    positions = _normalize_positions((broker_snapshot or {}).get("positions") or [])
    settings = _normalize_config(config)

    total_assets = float(account_snapshot.get("total_assets") or 0.0)
    available_cash = float(account_snapshot.get("available_cash") or account_snapshot.get("cash") or 0.0)
    remaining_buy_budget = available_cash * (1 - settings["cash_buffer"])
    lot_size = settings["lot_size"]

    target_qty_map: dict[str, int] = {}
    for row in targets.itertuples(index=False):
        price = market.get(row.ts_code, {}).get("latest_price", 0.0)
        if price > 0:
            target_qty_map[row.ts_code] = _round_down_to_lot(total_assets * row.target_weight / price, lot_size)
        else:
            target_qty_map[row.ts_code] = 0

    reason_codes: list[dict[str, Any]] = []
    sell_orders: list[dict[str, Any]] = []
    buy_orders: list[dict[str, Any]] = []

    sell_candidates: list[dict[str, Any]] = []
    buy_candidates: list[dict[str, Any]] = []
    all_codes = sorted(set(target_qty_map) | set(positions))
    target_lookup = targets.set_index("ts_code").to_dict("index") if not targets.empty else {}

    for ts_code in all_codes:
        position = positions.get(ts_code, {"current_qty": 0, "sellable_qty": 0})
        current_qty = int(position["current_qty"])
        target_qty = int(target_qty_map.get(ts_code, 0))
        if current_qty > target_qty:
            sell_candidates.append(
                {
                    "ts_code": ts_code,
                    "current_qty": current_qty,
                    "sellable_qty": int(position["sellable_qty"]),
                    "target_qty": target_qty,
                }
            )
        elif target_qty > current_qty:
            item = target_lookup.get(ts_code, {})
            buy_candidates.append(
                {
                    "ts_code": ts_code,
                    "current_qty": current_qty,
                    "sellable_qty": int(position["sellable_qty"]),
                    "target_qty": target_qty,
                    "score": float(item.get("score") or 0.0),
                    "target_weight": float(item.get("target_weight") or 0.0),
                }
            )

    sell_candidates.sort(key=lambda item: (item["current_qty"] - item["target_qty"], item["ts_code"]), reverse=True)
    buy_candidates.sort(key=lambda item: (-item["score"], -item["target_weight"], item["ts_code"]))

    for candidate in sell_candidates:
        order, order_reasons = _build_sell_order(candidate, market.get(candidate["ts_code"], {}), lot_size)
        sell_orders.append(order)
        reason_codes.extend(order_reasons)

    for candidate in buy_candidates:
        order, order_reasons, remaining_buy_budget = _build_buy_order(
            candidate,
            market.get(candidate["ts_code"], {}),
            lot_size,
            remaining_buy_budget,
        )
        buy_orders.append(order)
        reason_codes.extend(order_reasons)

    orders = pd.DataFrame(sell_orders + buy_orders)
    if orders.empty:
        orders = pd.DataFrame(columns=["ts_code", "side", "requested_qty", "requested_price", "staging_status"])
    return StagingResult(orders=orders, reason_codes=reason_codes)


def save_orders(orders: pd.DataFrame, output_path: str | Path) -> str:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    orders.to_csv(output_path, index=False)
    return str(output_path)


def save_staging_reason_codes(reason_codes: list[dict[str, Any]], output_path: str | Path) -> str:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(reason_codes, handle, indent=2, ensure_ascii=False)
    return str(output_path)


def _normalize_target_weights(target_weights: pd.DataFrame) -> pd.DataFrame:
    if target_weights is None or target_weights.empty:
        return pd.DataFrame(columns=["ts_code", "target_weight", "score"])

    targets = target_weights.copy()
    rename_map = {}
    if "ts_code" not in targets.columns and "symbol" in targets.columns:
        rename_map["symbol"] = "ts_code"
    if "target_weight" not in targets.columns and "weight" in targets.columns:
        rename_map["weight"] = "target_weight"
    if rename_map:
        targets = targets.rename(columns=rename_map)

    if "ts_code" not in targets.columns or "target_weight" not in targets.columns:
        raise ValueError("target_weights must contain ts_code/symbol and target_weight/weight")

    if "score" not in targets.columns:
        targets["score"] = 0.0

    targets["ts_code"] = targets["ts_code"].astype(str).str.strip()
    targets["target_weight"] = pd.to_numeric(targets["target_weight"], errors="coerce").fillna(0.0)
    targets["score"] = pd.to_numeric(targets["score"], errors="coerce").fillna(0.0)
    targets = targets[(targets["ts_code"] != "") & (targets["target_weight"] > 0)]
    return targets[["ts_code", "target_weight", "score"]].drop_duplicates(subset=["ts_code"], keep="first")


def _normalize_market_data(market_data: pd.DataFrame) -> dict[str, dict[str, float]]:
    if market_data is None or market_data.empty:
        return {}

    frame = market_data.copy()
    rename_map = {}
    if "ts_code" not in frame.columns and "symbol" in frame.columns:
        rename_map["symbol"] = "ts_code"
    latest_price_col = _find_first_column(frame.columns, ["latest_price", "price", "last_price", "close", "reference_price"])
    limit_up_col = _find_first_column(frame.columns, ["limit_up_price", "limit_up", "high_limit"])
    limit_down_col = _find_first_column(frame.columns, ["limit_down_price", "limit_down", "low_limit"])
    if latest_price_col and latest_price_col != "latest_price":
        rename_map[latest_price_col] = "latest_price"
    if limit_up_col and limit_up_col != "limit_up_price":
        rename_map[limit_up_col] = "limit_up_price"
    if limit_down_col and limit_down_col != "limit_down_price":
        rename_map[limit_down_col] = "limit_down_price"
    if rename_map:
        frame = frame.rename(columns=rename_map)

    if "ts_code" not in frame.columns or "latest_price" not in frame.columns:
        raise ValueError("market_data must contain ts_code/symbol and latest_price/price")

    for column in ["latest_price", "limit_up_price", "limit_down_price"]:
        if column not in frame.columns:
            frame[column] = 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)

    quotes: dict[str, dict[str, float]] = {}
    for row in frame.itertuples(index=False):
        ts_code = str(row.ts_code).strip()
        if not ts_code:
            continue
        quotes[ts_code] = {
            "latest_price": float(row.latest_price),
            "limit_up_price": float(row.limit_up_price),
            "limit_down_price": float(row.limit_down_price),
        }
    return quotes


def _normalize_positions(positions: list[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    normalized: dict[str, dict[str, int]] = {}
    for item in positions:
        ts_code = str(item.get("ts_code") or item.get("symbol") or "").strip()
        if not ts_code:
            continue
        current_qty = int(item.get("total_amount") or item.get("quantity") or item.get("amount") or 0)
        sellable_qty = int(item.get("sellable_amount") or item.get("sellable_quantity") or current_qty)
        normalized[ts_code] = {
            "current_qty": current_qty,
            "sellable_qty": sellable_qty,
        }
    return normalized


def _normalize_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    config = dict(config or {})
    return {
        "lot_size": int(config.get("lot_size") or 100),
        "cash_buffer": float(config.get("cash_buffer") if config and "cash_buffer" in config else 0.02),
    }


def _build_sell_order(
    candidate: dict[str, Any],
    quote: Mapping[str, float],
    lot_size: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    requested_qty = max(candidate["current_qty"] - candidate["target_qty"], 0)
    reasons: list[dict[str, Any]] = []
    price = float(quote.get("latest_price") or 0.0)
    requested_price = round(price, 4)

    if requested_qty > 0:
        rounded_qty = _round_down_to_lot(requested_qty, lot_size)
        if rounded_qty < requested_qty:
            reasons.append(_stage_reason(candidate["ts_code"], "sell", "adjusted", "sell_qty_rounded_down_lot_size", rounded_qty, price))
        requested_qty = rounded_qty

    if requested_qty > 0 and candidate["sellable_qty"] < requested_qty:
        sellable_qty = _round_down_to_lot(candidate["sellable_qty"], lot_size)
        if sellable_qty < requested_qty:
            reasons.append(_stage_reason(candidate["ts_code"], "sell", "adjusted", "sell_qty_limited_by_sellable", sellable_qty, price))
        requested_qty = sellable_qty

    if price <= 0:
        reasons.append(_stage_reason(candidate["ts_code"], "sell", "rejected", "sell_rejected_missing_price", 0, price))
        return _order_row(candidate["ts_code"], "sell", 0, requested_price, "rejected", candidate), reasons

    if requested_qty <= 0:
        reasons.append(_stage_reason(candidate["ts_code"], "sell", "rejected", "sell_rejected_below_lot_size", 0, price))
        return _order_row(candidate["ts_code"], "sell", 0, requested_price, "rejected", candidate), reasons

    if float(quote.get("limit_down_price") or 0.0) > 0 and price <= float(quote["limit_down_price"]):
        reasons.append(_stage_reason(candidate["ts_code"], "sell", "rejected", "sell_rejected_limit_down", requested_qty, price))
        return _order_row(candidate["ts_code"], "sell", 0, requested_price, "rejected", candidate), reasons

    status = "adjusted" if any(reason["action"] == "adjusted" for reason in reasons) else "staged"
    if status == "staged":
        reasons.append(_stage_reason(candidate["ts_code"], "sell", "staged", "sell_staged", requested_qty, price))
    return _order_row(candidate["ts_code"], "sell", requested_qty, requested_price, status, candidate), reasons


def _build_buy_order(
    candidate: dict[str, Any],
    quote: Mapping[str, float],
    lot_size: int,
    remaining_buy_budget: float,
) -> tuple[dict[str, Any], list[dict[str, Any]], float]:
    requested_qty = max(candidate["target_qty"] - candidate["current_qty"], 0)
    reasons: list[dict[str, Any]] = []
    price = float(quote.get("latest_price") or 0.0)
    requested_price = round(price, 4)

    if requested_qty > 0:
        rounded_qty = _round_down_to_lot(requested_qty, lot_size)
        if rounded_qty < requested_qty:
            reasons.append(_stage_reason(candidate["ts_code"], "buy", "adjusted", "buy_qty_rounded_down_lot_size", rounded_qty, price))
        requested_qty = rounded_qty

    if price <= 0:
        reasons.append(_stage_reason(candidate["ts_code"], "buy", "rejected", "buy_rejected_missing_price", 0, price, candidate.get("score")))
        return _order_row(candidate["ts_code"], "buy", 0, requested_price, "rejected", candidate), reasons, remaining_buy_budget

    if requested_qty <= 0:
        reasons.append(_stage_reason(candidate["ts_code"], "buy", "rejected", "buy_rejected_below_lot_size", 0, price, candidate.get("score")))
        return _order_row(candidate["ts_code"], "buy", 0, requested_price, "rejected", candidate), reasons, remaining_buy_budget

    if float(quote.get("limit_up_price") or 0.0) > 0 and price >= float(quote["limit_up_price"]):
        reasons.append(_stage_reason(candidate["ts_code"], "buy", "rejected", "buy_rejected_limit_up", requested_qty, price, candidate.get("score")))
        return _order_row(candidate["ts_code"], "buy", 0, requested_price, "rejected", candidate), reasons, remaining_buy_budget

    max_affordable_qty = _round_down_to_lot(remaining_buy_budget / price, lot_size)
    if max_affordable_qty <= 0:
        reasons.append(_stage_reason(candidate["ts_code"], "buy", "rejected", "buy_rejected_cash_limit", 0, price, candidate.get("score")))
        return _order_row(candidate["ts_code"], "buy", 0, requested_price, "rejected", candidate), reasons, remaining_buy_budget

    if max_affordable_qty < requested_qty:
        requested_qty = max_affordable_qty
        reasons.append(_stage_reason(candidate["ts_code"], "buy", "adjusted", "buy_qty_limited_by_cash", requested_qty, price, candidate.get("score")))

    if requested_qty <= 0:
        reasons.append(_stage_reason(candidate["ts_code"], "buy", "rejected", "buy_rejected_cash_limit", 0, price, candidate.get("score")))
        return _order_row(candidate["ts_code"], "buy", 0, requested_price, "rejected", candidate), reasons, remaining_buy_budget

    order_value = requested_qty * price
    remaining_buy_budget = max(remaining_buy_budget - order_value, 0.0)
    status = "adjusted" if any(reason["action"] == "adjusted" for reason in reasons) else "staged"
    if status == "staged":
        reasons.append(_stage_reason(candidate["ts_code"], "buy", "staged", "buy_staged", requested_qty, price, candidate.get("score")))
    return _order_row(candidate["ts_code"], "buy", requested_qty, requested_price, status, candidate), reasons, remaining_buy_budget


def _order_row(
    ts_code: str,
    side: str,
    requested_qty: int,
    requested_price: float,
    staging_status: str,
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    order_value = round(requested_qty * requested_price, 4)
    payload = {
        "ts_code": ts_code,
        "side": side,
        "requested_qty": int(requested_qty),
        "requested_price": requested_price,
        "staging_status": staging_status,
        "target_qty": int(candidate.get("target_qty") or 0),
        "current_qty": int(candidate.get("current_qty") or 0),
        "order_value": order_value,
    }
    if "score" in candidate:
        payload["score"] = round(float(candidate.get("score") or 0.0), 8)
    return payload


def _stage_reason(
    ts_code: str,
    side: str,
    action: str,
    reason: str,
    requested_qty: int,
    requested_price: float,
    score: float | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ts_code": ts_code,
        "stage": STAGING_STAGE,
        "action": action,
        "reason": reason,
        "side": side,
        "requested_qty": int(requested_qty),
        "requested_price": round(float(requested_price or 0.0), 4),
    }
    if score is not None:
        payload["score"] = round(float(score), 8)
    return payload


def _round_down_to_lot(quantity: float, lot_size: int) -> int:
    if quantity <= 0 or lot_size <= 0:
        return 0
    return int(quantity // lot_size) * lot_size


def _find_first_column(columns: pd.Index, candidates: list[str]) -> str | None:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


__all__ = [
    "StagingResult",
    "save_orders",
    "save_staging_reason_codes",
    "stage_orders",
]
