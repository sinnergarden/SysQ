from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from miniqmt_server.app import MiniQMTServerApp
from miniqmt_server.config import MockBrokerConfig, ServerConfig, load_config


class MiniQMTServerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        data_dir = Path(self.temp_dir.name) / "data"
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            broker_mode="mock",
            data_dir=data_dir,
            mock=MockBrokerConfig(
                account_id="mock_account",
                allow_submit=True,
                auto_fill=False,
                miniqmt_connected=False,
                query_ready=True,
                submit_enabled=True,
                account={
                    "account_id": "mock_account",
                    "total_assets": 100000.0,
                    "available_cash": 50000.0,
                    "market_value": 50000.0,
                    "frozen_cash": 0.0,
                    "daily_pnl": 0.0,
                },
                positions=[
                    {
                        "symbol": "600000.SH",
                        "volume": 1000,
                        "available_volume": 1000,
                        "cost_price": 10.0,
                        "market_price": 10.5,
                        "market_value": 10500.0,
                        "pnl": 500.0,
                        "pnl_pct": 0.05,
                        "update_time": "2026-04-06T09:25:00Z",
                    }
                ],
            ),
        )
        self.app = MiniQMTServerApp(config)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _get(self, path: str) -> dict:
        status_code, payload = self.app.handle("GET", path)
        self.assertEqual(status_code, 200)
        return payload

    def _post(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        status_code, response_payload = self.app.handle("POST", path, body)
        self.assertEqual(status_code, 200)
        return response_payload

    def test_health_validate_submit_cancel_snapshot_flow(self) -> None:
        health = self._get("/health")
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["broker_mode"], "mock")

        payload = {
            "request_id": "request-001",
            "strategy_id": "strategy_a",
            "trade_date": "2026-04-06",
            "account_id": "mock_account",
            "dry_run": False,
            "orders": [
                {
                    "intent_id": "intent-001",
                    "symbol": "600000.SH",
                    "side": "BUY",
                    "quantity": 100,
                    "order_type": "LIMIT",
                    "limit_price": 12.34,
                    "time_in_force": "DAY",
                    "reason": "rebalance",
                    "target_weight": 0.01,
                    "notes": "unit test",
                }
            ],
        }
        validate_result = self._post("/orders/validate", payload)
        self.assertEqual(validate_result["status"], "accepted")
        self.assertEqual(validate_result["accepted_count"], 1)

        submit_result = self._post("/orders/submit", payload)
        self.assertEqual(submit_result["status"], "accepted")
        self.assertEqual(len(submit_result["broker_order_ids"]), 1)
        broker_order_id = submit_result["broker_order_ids"][0]

        orders_result = self._get("/orders")
        self.assertEqual(orders_result["count"], 1)
        self.assertEqual(orders_result["orders"][0]["status"], "submitted")

        cancel_result = self._post(
            "/orders/cancel",
            {
                "request_id": "cancel-001",
                "account_id": "mock_account",
                "broker_order_ids": [broker_order_id],
                "reason": "manual review",
            },
        )
        self.assertEqual(cancel_result["status"], "accepted")
        self.assertEqual(cancel_result["canceled_count"], 1)
        self.assertEqual(cancel_result["canceled"][0]["status"], "canceled")

        snapshot_result = self._get("/snapshots/latest")
        self.assertEqual(snapshot_result["status"], "ok")
        self.assertEqual(snapshot_result["snapshot"]["orders"][0]["status"], "canceled")

    def test_dry_run_submit_does_not_persist_orders(self) -> None:
        payload = {
            "request_id": "request-dry-run",
            "strategy_id": "strategy_a",
            "trade_date": "2026-04-06",
            "account_id": "mock_account",
            "dry_run": True,
            "orders": [
                {
                    "intent_id": "intent-dry-run",
                    "symbol": "600000.SH",
                    "side": "BUY",
                    "quantity": 100,
                    "order_type": "LIMIT",
                    "limit_price": 12.34,
                    "time_in_force": "DAY",
                    "reason": "rebalance",
                    "target_weight": 0.01,
                    "notes": "unit test",
                }
            ],
        }
        submit_result = self._post("/orders/submit", payload)
        self.assertEqual(submit_result["status"], "dry_run")
        self.assertEqual(submit_result["broker_order_ids"], [])

        orders_result = self._get("/orders")
        self.assertEqual(orders_result["count"], 0)

    def test_submit_rejects_invalid_order(self) -> None:
        payload = {
            "request_id": "request-invalid",
            "strategy_id": "strategy_a",
            "trade_date": "2026-04-06",
            "account_id": "mock_account",
            "dry_run": False,
            "orders": [
                {
                    "intent_id": "intent-invalid",
                    "symbol": "600000.SH",
                    "side": "BUY",
                    "quantity": 123,
                    "order_type": "LIMIT",
                    "limit_price": 12.34,
                    "time_in_force": "DAY",
                    "reason": "rebalance",
                    "target_weight": 0.01,
                    "notes": "unit test",
                }
            ],
        }
        validate_result = self._post("/orders/validate", payload)
        self.assertEqual(validate_result["status"], "rejected")
        self.assertEqual(validate_result["rejected_count"], 1)
        self.assertEqual(validate_result["rejected"][0]["reasons"][0]["code"], "invalid_lot_size")

    def test_load_config_example(self) -> None:
        config = load_config("miniqmt_server/config.example.yaml")
        self.assertEqual(config.broker_mode, "mock")
        self.assertEqual(config.port, 8811)


if __name__ == "__main__":
    unittest.main()
