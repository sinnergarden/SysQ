from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from miniqmt_server.models import OrderRecord, TradeRecord


class JsonlStorage:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._orders_path = self.data_dir / "orders.jsonl"
        self._trades_path = self.data_dir / "trades.jsonl"
        self._snapshots_path = self.data_dir / "snapshots.jsonl"
        self._latest_snapshot_path = self.data_dir / "latest_snapshot.json"
        for path in [self._orders_path, self._trades_path, self._snapshots_path]:
            path.touch(exist_ok=True)

    def append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str))
            handle.write("\n")

    def load_jsonl(self, path: Path) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                records.append(json.loads(line))
        return records

    def record_order(self, order: OrderRecord) -> None:
        self.append_jsonl(self._orders_path, order.to_dict())

    def record_trade(self, trade: TradeRecord) -> None:
        self.append_jsonl(self._trades_path, trade.to_dict())

    def list_orders(self) -> list[OrderRecord]:
        latest_by_order_id: dict[str, OrderRecord] = {}
        for payload in self.load_jsonl(self._orders_path):
            order = OrderRecord.from_dict(payload)
            key = order.broker_order_id or order.client_order_id or order.intent_id
            latest_by_order_id[key] = order
        orders = list(latest_by_order_id.values())
        orders.sort(key=lambda item: (item.updated_at, item.broker_order_id))
        return orders

    def list_trades(self) -> list[TradeRecord]:
        trades = [TradeRecord.from_dict(payload) for payload in self.load_jsonl(self._trades_path)]
        trades.sort(key=lambda item: (item.executed_at, item.trade_id))
        return trades

    def record_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.append_jsonl(self._snapshots_path, snapshot)
        with open(self._latest_snapshot_path, "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, indent=2, ensure_ascii=True, sort_keys=True, default=str)

    def get_latest_snapshot(self) -> dict[str, Any] | None:
        if not self._latest_snapshot_path.exists():
            return None
        with open(self._latest_snapshot_path, "r", encoding="utf-8") as handle:
            return json.load(handle)
