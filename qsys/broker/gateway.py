from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from qsys.broker.miniqmt import MiniQMTReadback


def _default_readback_payload(trading_date: str, account_name: str) -> dict[str, Any]:
    return {
        "artifact_type": "miniqmt_readback",
        "adapter_name": "BrokerGatewayStub",
        "account_name": account_name,
        "as_of_date": trading_date,
        "account_snapshot": {
            "account_name": account_name,
            "cash": 50000.0,
            "available_cash": 50000.0,
            "frozen_cash": 0.0,
            "market_value": 4200.0,
            "total_assets": 54200.0,
        },
        "positions": [
            {
                "symbol": "600000.SH",
                "total_amount": 400,
                "sellable_amount": 400,
                "avg_cost": 10.2,
                "market_value": 4200.0,
                "last_price": 10.5,
            }
        ],
        "trades": [
            {
                "broker_trade_id": f"{trading_date}-fill-001",
                "broker_order_id": f"{trading_date}-broker-order-001",
                "intent_id": f"{trading_date}:real:buy:600000.SH",
                "symbol": "600000.SH",
                "side": "buy",
                "filled_amount": 400,
                "filled_price": 10.5,
                "fee": 1.2,
                "tax": 0.0,
                "note": "stub_gateway_fill",
            }
        ],
    }


class BrokerGateway:
    """Explicit read-only broker gateway backed by a JSON readback payload."""

    def __init__(
        self,
        *,
        account_name: str = "real",
        readback_path: str | Path | None = None,
        readback_payload: dict[str, Any] | None = None,
    ) -> None:
        self.account_name = account_name
        self.readback_path = Path(readback_path) if readback_path else None
        self.readback_payload = readback_payload

    def _resolve_trading_date(self, trading_date: str | None) -> str:
        if trading_date:
            return trading_date
        if isinstance(self.readback_payload, dict) and self.readback_payload.get("as_of_date"):
            return str(self.readback_payload["as_of_date"])
        return datetime.utcnow().strftime("%Y-%m-%d")

    def _load_readback(self, trading_date: str) -> MiniQMTReadback:
        if self.readback_payload is not None:
            payload = dict(self.readback_payload)
            payload.setdefault("account_name", self.account_name)
            payload.setdefault("as_of_date", trading_date)
        elif self.readback_path is not None:
            with open(self.readback_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle) or {}
        else:
            payload = _default_readback_payload(trading_date, self.account_name)
        return MiniQMTReadback.from_dict(payload)

    def get_account_snapshot(self, trading_date: str | None = None) -> dict[str, Any]:
        trading_date = self._resolve_trading_date(trading_date)
        readback = self._load_readback(trading_date)
        account = readback.account_snapshot
        return {
            "account_name": account.account_name,
            "cash": account.cash,
            "available_cash": account.available_cash,
            "frozen_cash": account.frozen_cash,
            "market_value": account.market_value,
            "total_assets": account.total_assets,
            "as_of_date": readback.as_of_date or trading_date,
        }

    def get_positions(self, trading_date: str | None = None) -> list[dict[str, Any]]:
        trading_date = self._resolve_trading_date(trading_date)
        readback = self._load_readback(trading_date)
        return [
            {
                "symbol": item.symbol,
                "quantity": item.total_amount,
                "sellable_quantity": item.sellable_amount,
                "price": item.last_price,
                "avg_cost": item.avg_cost,
                "market_value": item.market_value,
            }
            for item in readback.positions
        ]

    def get_fills(self, trading_date: str) -> list[dict[str, Any]]:
        readback = self._load_readback(trading_date)
        fills: list[dict[str, Any]] = []
        for item in readback.trades:
            fills.append(
                {
                    "fill_id": item.broker_trade_id or f"{trading_date}:{item.symbol}:{item.side}:{item.order_id}",
                    "order_id": item.order_id or item.broker_order_id,
                    "symbol": item.symbol,
                    "side": item.side,
                    "quantity": item.filled_amount,
                    "price": item.filled_price,
                    "fee": item.fee,
                    "tax": item.tax,
                    "filled_at": trading_date,
                    "note": item.note,
                }
            )
        return fills

    def write_snapshot(self, *, trading_date: str, output_path: str | Path) -> dict[str, Any]:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "artifact_type": "broker_snapshot",
            "trading_date": trading_date,
            "account_snapshot": self.get_account_snapshot(trading_date),
            "positions": self.get_positions(trading_date),
            "fills": self.get_fills(trading_date),
        }
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        payload["path"] = str(output_path)
        return payload
