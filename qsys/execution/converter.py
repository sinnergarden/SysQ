from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.execution.models import BrokerOrderRequest


def to_broker_order_requests(
    *,
    intent_rows: list[dict[str, Any]],
    trade_date: str,
    run_id: str,
    price_source: str = "",
    price_snapshot_time: str = "",
) -> list[BrokerOrderRequest]:
    """Convert a list of intent dicts to ``BrokerOrderRequest``.

    Phase 1 only supports **limit** orders. Every order must have a price;
    rows without a valid price are skipped with a warning (the caller is
    responsible for logging).

    Each intent dict should have:
    - *symbol*, *side*, *amount* (or *requested_qty* or *quantity*)
    - *price* (required for limit orders)
    - optionally *intent_id*, *target_weight*, *reason*
    """
    requests: list[BrokerOrderRequest] = []
    for i, row in enumerate(intent_rows):
        symbol = str(row.get("instrument") or row.get("symbol") or "")
        side = str(row.get("side") or "").lower()
        qty = int(row.get("requested_qty") or row.get("amount") or row.get("quantity", 0))
        price_raw = row.get("price") or row.get("limit_price") or row.get("est_value")
        limit_price: float | None = None
        if price_raw is not None:
            try:
                limit_price = float(price_raw)
            except (TypeError, ValueError):
                limit_price = None

        intent_id = str(row.get("intent_id") or f"{run_id}:{symbol}:{side}:{i:03d}")

        if not symbol or side not in ("buy", "sell") or qty <= 0:
            continue

        # Phase 1: limit-only. Fail closed if no price is available.
        if limit_price is None or limit_price <= 0:
            continue

        requests.append(
            BrokerOrderRequest(
                intent_id=intent_id,
                symbol=symbol,
                side=side,
                order_type="limit",
                quantity=qty,
                limit_price=limit_price,
                price_source=row.get("price_source", price_source),
                price_snapshot_time=row.get("price_snapshot_time", price_snapshot_time),
                target_weight=row.get("target_weight"),
                reason=row.get("reason", "live_execution"),
            )
        )
    return requests


def from_order_intents_csv(csv_path: str | Path, *, trade_date: str, run_id: str) -> list[BrokerOrderRequest]:
    """Convert a shadow-rebalance *order_intents.csv* to ``BrokerOrderRequest``."""
    path = Path(csv_path)
    if not path.exists() or path.stat().st_size == 0:
        return []
    try:
        df = pd.read_csv(path)
    except (pd.errors.EmptyDataError, pd.errors.ParserError):
        return []
    if df.empty:
        return []
    rows = df.to_dict(orient="records")
    mapped: list[dict[str, Any]] = []
    for row in rows:
        mapped.append(
            {
                "instrument": str(row.get("instrument", "")),
                "side": str(row.get("side", "")).lower(),
                "requested_qty": int(row.get("requested_qty", 0)),
                "price": row.get("price"),
                "target_weight": float(row.get("target_weight", 0.0)),
                "reason": str(row.get("reason", "rebalance_to_target_weight")),
            }
        )
    return to_broker_order_requests(intent_rows=mapped, trade_date=trade_date, run_id=run_id)


def from_plan_dataframe(plan_df: pd.DataFrame, *, trade_date: str, run_id: str) -> list[BrokerOrderRequest]:
    """Convert a ``PlanGenerator`` output DataFrame to ``BrokerOrderRequest``."""
    if plan_df is None or plan_df.empty:
        return []
    rows = plan_df.to_dict(orient="records")
    return to_broker_order_requests(intent_rows=rows, trade_date=trade_date, run_id=run_id)


def from_intents_json(json_path: str | Path, *, trade_date: str, run_id: str) -> list[BrokerOrderRequest]:
    """Convert a ``build_order_intents`` JSON artifact to ``BrokerOrderRequest``."""
    path = Path(json_path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    raw = payload.get("intents") or []
    rows: list[dict[str, Any]] = []
    for item in raw:
        rows.append(
            {
                "symbol": str(item.get("symbol", "")),
                "side": str(item.get("side", "")).lower(),
                "amount": int(item.get("amount", 0)),
                "price": item.get("price"),
                "intent_id": str(item.get("intent_id", "")),
                "target_weight": item.get("weight"),
                "reason": str(item.get("note", "")),
            }
        )
    return to_broker_order_requests(intent_rows=rows, trade_date=trade_date, run_id=run_id)
