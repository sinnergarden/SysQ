import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from qsys.broker.gateway import BrokerGateway
from qsys.core.runner import QsysRunner
from qsys.trader.database import TradeLedger


class TestTradeLedger(unittest.TestCase):
    def test_initialize_creates_required_tables(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "data" / "trade.db"
            TradeLedger(db_path)

            self.assertTrue(db_path.exists())
            with sqlite3.connect(db_path) as connection:
                rows = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()

            table_names = {row[0] for row in rows}
            self.assertTrue(
                {
                    "pipeline_runs",
                    "position_snapshots",
                    "orders",
                    "fills",
                    "daily_metrics",
                }.issubset(table_names)
            )


class TestQsysRunner(unittest.TestCase):
    def test_run_step_skips_successful_step(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = QsysRunner(
                trading_date="2026-04-07",
                runs_root=Path(tmpdir) / "runs",
                db_path=Path(tmpdir) / "data" / "trade.db",
            )
            call_log = []

            def step_func() -> None:
                call_log.append("called")

            result_first = runner.run_step("01_sync_data", step_func)
            result_second = runner.run_step("01_sync_data", step_func)

            self.assertEqual(result_first, "success")
            self.assertEqual(result_second, "skipped")
            self.assertEqual(call_log, ["called"])
            manifest = runner.load_manifest()
            self.assertEqual(manifest["steps"]["01_sync_data"]["status"], "success")

    def test_sync_broker_step_writes_snapshot_artifact(self):
        payload = {
            "artifact_type": "miniqmt_readback",
            "adapter_name": "TestGateway",
            "account_name": "real",
            "as_of_date": "2026-04-07",
            "account_snapshot": {
                "account_name": "real",
                "cash": 60000.0,
                "available_cash": 58000.0,
                "frozen_cash": 2000.0,
                "market_value": 5000.0,
                "total_assets": 65000.0,
            },
            "positions": [
                {
                    "symbol": "600000.SH",
                    "total_amount": 500,
                    "sellable_amount": 500,
                    "avg_cost": 9.8,
                    "market_value": 5250.0,
                    "last_price": 10.5,
                }
            ],
            "trades": [
                {
                    "broker_trade_id": "fill-001",
                    "broker_order_id": "order-001",
                    "symbol": "600000.SH",
                    "side": "buy",
                    "filled_amount": 500,
                    "filled_price": 10.5,
                    "fee": 1.5,
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            runner = QsysRunner(
                trading_date="2026-04-07",
                runs_root=Path(tmpdir) / "runs",
                db_path=Path(tmpdir) / "data" / "trade.db",
                broker_gateway=BrokerGateway(readback_payload=payload),
            )

            handlers = runner.build_default_step_handlers()
            result = runner.run_step("02_sync_broker", handlers["02_sync_broker"])

            self.assertEqual(result, "success")
            snapshot_path = Path(tmpdir) / "runs" / "2026-04-07" / "02_broker" / "broker_snapshot.json"
            self.assertTrue(snapshot_path.exists())

            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            self.assertEqual(snapshot["account_snapshot"]["cash"], 60000.0)
            self.assertEqual(snapshot["positions"][0]["symbol"], "600000.SH")

            manifest = runner.load_manifest()
            self.assertEqual(
                manifest["artifacts"]["broker_snapshot"]["path"],
                str(snapshot_path),
            )

            with sqlite3.connect(Path(tmpdir) / "data" / "trade.db") as connection:
                position_count = connection.execute(
                    "SELECT COUNT(*) FROM position_snapshots WHERE snapshot_type = 'real'"
                ).fetchone()[0]

            self.assertEqual(position_count, 1)


if __name__ == "__main__":
    unittest.main()
