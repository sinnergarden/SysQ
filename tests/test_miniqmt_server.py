from __future__ import annotations

import json
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib import error as urlerror
from urllib import request

from miniqmt_server.app import MiniQMTServerApp, build_server
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

    def test_submit_replays_same_request_id_after_restart(self) -> None:
        payload = {
            "request_id": "request-idempotent",
            "strategy_id": "strategy_a",
            "trade_date": "2026-04-06",
            "account_id": "mock_account",
            "dry_run": False,
            "orders": [
                {
                    "intent_id": "intent-idempotent",
                    "symbol": "600000.SH",
                    "side": "BUY",
                    "quantity": 100,
                    "order_type": "LIMIT",
                    "limit_price": 12.34,
                    "time_in_force": "DAY",
                    "reason": "idempotent retry",
                }
            ],
        }

        first_submit = self._post("/orders/submit", payload)
        self.assertEqual(first_submit["status"], "accepted")
        self.assertEqual(first_submit["idempotency_status"], "new")

        self.app = MiniQMTServerApp(self.app.config)
        replayed_submit = self._post("/orders/submit", payload)
        self.assertEqual(replayed_submit["status"], "accepted")
        self.assertEqual(replayed_submit["idempotency_status"], "replayed")
        self.assertEqual(replayed_submit["broker_order_ids"], first_submit["broker_order_ids"])
        self.assertEqual(replayed_submit["original_submit_time"], first_submit["submit_time"])
        self.assertEqual(self._get("/orders")["count"], 1)

    def test_submit_rejects_reused_request_id_with_different_payload(self) -> None:
        original_payload = {
            "request_id": "request-conflict",
            "strategy_id": "strategy_a",
            "trade_date": "2026-04-06",
            "account_id": "mock_account",
            "dry_run": False,
            "orders": [
                {
                    "intent_id": "intent-conflict",
                    "symbol": "600000.SH",
                    "side": "BUY",
                    "quantity": 100,
                    "order_type": "LIMIT",
                    "limit_price": 12.34,
                    "time_in_force": "DAY",
                    "reason": "initial submit",
                }
            ],
        }
        conflicting_payload = {
            "request_id": "request-conflict",
            "strategy_id": "strategy_a",
            "trade_date": "2026-04-06",
            "account_id": "mock_account",
            "dry_run": False,
            "orders": [
                {
                    "intent_id": "intent-conflict",
                    "symbol": "600000.SH",
                    "side": "BUY",
                    "quantity": 200,
                    "order_type": "LIMIT",
                    "limit_price": 12.34,
                    "time_in_force": "DAY",
                    "reason": "mutated submit",
                }
            ],
        }

        first_submit = self._post("/orders/submit", original_payload)
        self.assertEqual(first_submit["status"], "accepted")

        second_submit = self._post("/orders/submit", conflicting_payload)
        self.assertEqual(second_submit["status"], "rejected")
        self.assertEqual(second_submit["idempotency_status"], "conflict")
        self.assertEqual(second_submit["errors"][0]["code"], "request_id_conflict")
        self.assertEqual(second_submit["broker_order_ids"], [])
        self.assertEqual(self._get("/orders")["count"], 1)

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

    def test_submit_rejects_buy_when_available_cash_is_insufficient(self) -> None:
        payload = {
            "request_id": "request-no-cash",
            "strategy_id": "strategy_a",
            "trade_date": "2026-04-06",
            "account_id": "mock_account",
            "dry_run": False,
            "orders": [
                {
                    "intent_id": "intent-no-cash",
                    "symbol": "600000.SH",
                    "side": "BUY",
                    "quantity": 100,
                    "order_type": "LIMIT",
                    "limit_price": 600.0,
                    "time_in_force": "DAY",
                    "reason": "oversized rebalance",
                }
            ],
        }

        validate_result = self._post("/orders/validate", payload)
        self.assertEqual(validate_result["status"], "rejected")
        self.assertEqual(validate_result["rejected"][0]["reasons"][0]["code"], "insufficient_available_cash")

        submit_result = self._post("/orders/submit", payload)
        self.assertEqual(submit_result["status"], "rejected")
        self.assertEqual(submit_result["broker_order_ids"], [])
        self.assertEqual(self._get("/orders")["count"], 0)

    def test_validate_rejects_sell_when_available_volume_is_insufficient(self) -> None:
        payload = {
            "request_id": "request-no-volume",
            "strategy_id": "strategy_a",
            "trade_date": "2026-04-06",
            "account_id": "mock_account",
            "dry_run": True,
            "orders": [
                {
                    "intent_id": "intent-no-volume",
                    "symbol": "600000.SH",
                    "side": "SELL",
                    "quantity": 1100,
                    "order_type": "LIMIT",
                    "limit_price": 10.0,
                    "time_in_force": "DAY",
                    "reason": "oversell test",
                }
            ],
        }

        validate_result = self._post("/orders/validate", payload)
        self.assertEqual(validate_result["status"], "rejected")
        self.assertEqual(validate_result["rejected"][0]["reasons"][0]["code"], "insufficient_available_volume")

    def test_validate_tracks_cash_across_multiple_buy_orders(self) -> None:
        payload = {
            "request_id": "request-batch-cash",
            "strategy_id": "strategy_a",
            "trade_date": "2026-04-06",
            "account_id": "mock_account",
            "dry_run": True,
            "orders": [
                {
                    "intent_id": "intent-batch-1",
                    "symbol": "600000.SH",
                    "side": "BUY",
                    "quantity": 200,
                    "order_type": "LIMIT",
                    "limit_price": 100.0,
                    "time_in_force": "DAY",
                    "reason": "batch cash test",
                },
                {
                    "intent_id": "intent-batch-2",
                    "symbol": "600000.SH",
                    "side": "BUY",
                    "quantity": 400,
                    "order_type": "LIMIT",
                    "limit_price": 100.0,
                    "time_in_force": "DAY",
                    "reason": "batch cash test",
                },
            ],
        }

        validate_result = self._post("/orders/validate", payload)
        self.assertEqual(validate_result["status"], "partial")
        self.assertEqual(validate_result["accepted_count"], 1)
        self.assertEqual(validate_result["rejected_count"], 1)
        self.assertEqual(validate_result["accepted"][0]["intent_id"], "intent-batch-1")
        self.assertEqual(validate_result["rejected"][0]["intent_id"], "intent-batch-2")
        self.assertEqual(validate_result["rejected"][0]["reasons"][0]["code"], "insufficient_available_cash")

    def test_validate_rejects_market_buy_without_reference_price(self) -> None:
        payload = {
            "request_id": "request-no-price",
            "strategy_id": "strategy_a",
            "trade_date": "2026-04-06",
            "account_id": "mock_account",
            "dry_run": True,
            "orders": [
                {
                    "intent_id": "intent-no-price",
                    "symbol": "601398.SH",
                    "side": "BUY",
                    "quantity": 100,
                    "order_type": "MARKET",
                    "time_in_force": "DAY",
                    "reason": "market order safety",
                }
            ],
        }

        validate_result = self._post("/orders/validate", payload)
        self.assertEqual(validate_result["status"], "rejected")
        self.assertEqual(validate_result["rejected"][0]["reasons"][0]["code"], "missing_reference_price")

    def test_load_config_example(self) -> None:
        config = load_config("miniqmt_server/config.example.yaml")
        self.assertEqual(config.broker_mode, "mock")
        self.assertEqual(config.port, 8811)


class MiniQMTServerHTTPIntegrationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.server = None
        self.thread = None

    def tearDown(self) -> None:
        self._stop_server()
        self.temp_dir.cleanup()

    def _build_config(self, *, auto_fill: bool = False) -> ServerConfig:
        data_dir = Path(self.temp_dir.name) / "data"
        return ServerConfig(
            host="127.0.0.1",
            port=0,
            broker_mode="mock",
            data_dir=data_dir,
            mock=MockBrokerConfig(
                account_id="mock_account",
                allow_submit=True,
                auto_fill=auto_fill,
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

    def _start_server(self, *, auto_fill: bool = False) -> None:
        self.config = self._build_config(auto_fill=auto_fill)
        try:
            self.server = build_server(self.config)
        except PermissionError as exc:
            self.skipTest(f"sandbox disallows binding a local TCP port: {exc}")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self._wait_until_ready()

    def _stop_server(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.thread is not None:
            self.thread.join(timeout=5)
            self.thread = None

    def _restart_server(self) -> None:
        auto_fill = self.config.mock.auto_fill
        self._stop_server()
        self._start_server(auto_fill=auto_fill)

    def _request_json(self, path: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"} if body is not None else {}
        http_request = request.Request(f"{self.base_url}{path}", data=body, headers=headers, method=method)
        try:
            with request.urlopen(http_request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urlerror.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _wait_until_ready(self) -> None:
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                health_status, health = self._request_json("/health")
            except OSError:
                time.sleep(0.05)
                continue
            if health_status == 200 and health.get("status") == "ok":
                return
            time.sleep(0.05)
        self.fail("miniqmt_server HTTP endpoint did not become ready in time")

    def test_http_server_end_to_end_autofill_flow(self) -> None:
        self._start_server(auto_fill=True)

        health_status, health = self._request_json("/health")
        self.assertEqual(health_status, 200)
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["broker_mode"], "mock")
        self.assertEqual(health["submit_enabled"], True)

        account_status, account_before = self._request_json("/account")
        self.assertEqual(account_status, 200)
        self.assertEqual(account_before["account_id"], "mock_account")

        positions_status, positions_before = self._request_json("/positions")
        self.assertEqual(positions_status, 200)
        self.assertEqual(positions_before["count"], 1)
        initial_position = positions_before["positions"][0]
        self.assertEqual(initial_position["symbol"], "600000.SH")

        payload = {
            "request_id": "request-http-autofill",
            "strategy_id": "strategy_http_e2e",
            "trade_date": "2026-04-06",
            "account_id": "mock_account",
            "dry_run": False,
            "orders": [
                {
                    "intent_id": "intent-http-sell",
                    "symbol": "600000.SH",
                    "side": "SELL",
                    "quantity": 200,
                    "order_type": "LIMIT",
                    "limit_price": 10.5,
                    "time_in_force": "DAY",
                    "reason": "trim position",
                },
                {
                    "intent_id": "intent-http-buy",
                    "symbol": "601398.SH",
                    "side": "BUY",
                    "quantity": 300,
                    "order_type": "LIMIT",
                    "limit_price": 4.2,
                    "time_in_force": "DAY",
                    "reason": "rotate into bank",
                },
            ],
        }

        validate_status, validate = self._request_json("/orders/validate", method="POST", payload=payload)
        self.assertEqual(validate_status, 200)
        self.assertEqual(validate["status"], "accepted")
        self.assertEqual(validate["accepted_count"], 2)

        submit_status, submit = self._request_json("/orders/submit", method="POST", payload=payload)
        self.assertEqual(submit_status, 200)
        self.assertEqual(submit["status"], "accepted")
        self.assertEqual(submit["idempotency_status"], "new")
        self.assertEqual(submit["accepted_count"], 2)
        self.assertEqual(len(submit["broker_order_ids"]), 2)

        orders_status, orders = self._request_json(
            "/orders?request_id=request-http-autofill&status=filled&strategy_id=strategy_http_e2e"
        )
        self.assertEqual(orders_status, 200)
        self.assertEqual(orders["count"], 2)
        self.assertTrue(all(order["status"] == "filled" for order in orders["orders"]))

        trades_status, trades = self._request_json("/trades?trade_date=2026-04-06&strategy_id=strategy_http_e2e")
        self.assertEqual(trades_status, 200)
        self.assertEqual(trades["count"], 2)
        self.assertEqual({trade["symbol"] for trade in trades["trades"]}, {"600000.SH", "601398.SH"})

        sell_trade_status, sell_trade = self._request_json("/trades?symbol=600000.SH&trade_date=2026-04-06")
        self.assertEqual(sell_trade_status, 200)
        self.assertEqual(sell_trade["count"], 1)
        self.assertEqual(sell_trade["trades"][0]["broker_order_id"], submit["broker_order_ids"][0])

        account_after_status, account_after = self._request_json("/account")
        self.assertEqual(account_after_status, 200)
        expected_cash = round(account_before["available_cash"] + 200 * 10.5 - 300 * 4.2, 2)
        self.assertEqual(account_after["available_cash"], expected_cash)

        positions_after_status, positions_after = self._request_json("/positions")
        self.assertEqual(positions_after_status, 200)
        self.assertEqual(positions_after["count"], 2)
        positions_by_symbol = {item["symbol"]: item for item in positions_after["positions"]}
        self.assertEqual(positions_by_symbol["600000.SH"]["volume"], initial_position["volume"] - 200)
        self.assertEqual(
            positions_by_symbol["600000.SH"]["available_volume"],
            initial_position["available_volume"] - 200,
        )
        self.assertEqual(positions_by_symbol["601398.SH"]["volume"], 300)
        self.assertEqual(positions_by_symbol["601398.SH"]["market_price"], 4.2)

        snapshot_status, snapshot = self._request_json("/snapshots/latest")
        self.assertEqual(snapshot_status, 200)
        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(snapshot["snapshot"]["trigger"], "submit")
        self.assertEqual(len(snapshot["snapshot"]["orders"]), 2)
        self.assertEqual(len(snapshot["snapshot"]["trades"]), 2)

        self._restart_server()
        replay_status, replay = self._request_json("/orders/submit", method="POST", payload=payload)
        self.assertEqual(replay_status, 200)
        self.assertEqual(replay["status"], "accepted")
        self.assertEqual(replay["idempotency_status"], "replayed")
        self.assertEqual(replay["broker_order_ids"], submit["broker_order_ids"])
        self.assertEqual(replay["original_submit_time"], submit["submit_time"])

        replay_orders_status, replay_orders = self._request_json("/orders?request_id=request-http-autofill")
        self.assertEqual(replay_orders_status, 200)
        self.assertEqual(replay_orders["count"], 2)

    def test_http_server_end_to_end_submit_cancel_snapshot_flow(self) -> None:
        self._start_server(auto_fill=False)

        payload = {
            "request_id": "request-http-cancel",
            "strategy_id": "strategy_http_cancel",
            "trade_date": "2026-04-06",
            "account_id": "mock_account",
            "dry_run": False,
            "orders": [
                {
                    "intent_id": "intent-http-cancel",
                    "symbol": "600000.SH",
                    "side": "BUY",
                    "quantity": 100,
                    "order_type": "LIMIT",
                    "limit_price": 12.34,
                    "time_in_force": "DAY",
                    "reason": "place then cancel",
                }
            ],
        }

        submit_status, submit = self._request_json("/orders/submit", method="POST", payload=payload)
        self.assertEqual(submit_status, 200)
        self.assertEqual(submit["status"], "accepted")
        self.assertEqual(submit["accepted_count"], 1)
        client_order_id = submit["accepted"][0]["client_order_id"]

        orders_status, orders = self._request_json(f"/orders?client_order_id={client_order_id}&status=submitted")
        self.assertEqual(orders_status, 200)
        self.assertEqual(orders["count"], 1)
        self.assertEqual(orders["orders"][0]["filled_quantity"], 0)

        trades_status, trades = self._request_json("/trades?strategy_id=strategy_http_cancel")
        self.assertEqual(trades_status, 200)
        self.assertEqual(trades["count"], 0)

        cancel_status, cancel = self._request_json(
            "/orders/cancel",
            method="POST",
            payload={
                "request_id": "cancel-http-001",
                "account_id": "mock_account",
                "client_order_ids": [client_order_id],
                "reason": "cancel through HTTP e2e test",
            },
        )
        self.assertEqual(cancel_status, 200)
        self.assertEqual(cancel["status"], "accepted")
        self.assertEqual(cancel["canceled_count"], 1)
        self.assertEqual(cancel["canceled"][0]["status"], "canceled")

        canceled_orders_status, canceled_orders = self._request_json(
            "/orders?request_id=request-http-cancel&status=canceled"
        )
        self.assertEqual(canceled_orders_status, 200)
        self.assertEqual(canceled_orders["count"], 1)
        self.assertEqual(canceled_orders["orders"][0]["cancel_reason"], "cancel through HTTP e2e test")

        snapshot_status, snapshot = self._request_json("/snapshots/latest")
        self.assertEqual(snapshot_status, 200)
        self.assertEqual(snapshot["snapshot"]["trigger"], "cancel")
        self.assertEqual(snapshot["snapshot"]["orders"][0]["status"], "canceled")
        self.assertEqual(snapshot["snapshot"]["trades"], [])


if __name__ == "__main__":
    unittest.main()
