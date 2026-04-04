import json
import tempfile
import unittest

from qsys.broker.miniqmt import (
    BrokerOrderStatus,
    MiniQMTAdapter,
    MiniQMTOrderIntent,
)


class TestMiniQMTBridge(unittest.TestCase):
    def test_load_order_intents_from_artifact(self):
        payload = {
            "artifact_type": "order_intents",
            "intents": [
                {
                    "intent_id": "2026-04-07:real:buy:600000.SH",
                    "account_name": "real",
                    "symbol": "600000.SH",
                    "side": "buy",
                    "amount": 300,
                    "price": 10.5,
                    "execution_bucket": "after_sell_cash",
                    "cash_dependency": "requires_available_cash",
                    "t1_rule": "new_buy_not_sellable_until_next_session",
                    "signal_date": "2026-04-04",
                    "execution_date": "2026-04-07",
                    "model_version": "prod-v1",
                    "risk_tags": ["t1"],
                }
            ],
        }

        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False) as handle:
            json.dump(payload, handle)
            path = handle.name

        adapter = MiniQMTAdapter()
        intents = adapter.load_order_intents(path)
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0].symbol, "600000.SH")
        self.assertEqual(intents[0].amount, 300)

    def test_validate_intent_rejects_invalid_lot(self):
        adapter = MiniQMTAdapter()
        intent = MiniQMTOrderIntent(
            intent_id="bad",
            account_name="real",
            symbol="600000.SH",
            side="buy",
            amount=250,
            price=10.0,
        )
        issues = adapter.validate_intent(intent)
        self.assertIn("amount_not_lot_size", issues)

    def test_submit_orders_stays_dry_run_and_returns_rejections(self):
        adapter = MiniQMTAdapter(mode="dry_run")
        intents = [
            MiniQMTOrderIntent(
                intent_id="ok",
                account_name="real",
                symbol="600000.SH",
                side="buy",
                amount=300,
                price=10.0,
            ),
            MiniQMTOrderIntent(
                intent_id="bad",
                account_name="real",
                symbol="600000.SH",
                side="buy",
                amount=0,
                price=10.0,
            ),
        ]

        result = adapter.submit_orders(intents)
        self.assertEqual(result.intent_count, 2)
        self.assertEqual(len(result.accepted_orders), 1)
        self.assertEqual(len(result.rejected_orders), 1)
        self.assertEqual(result.accepted_orders[0].status, BrokerOrderStatus.PENDING)
        self.assertEqual(result.rejected_orders[0].status, BrokerOrderStatus.REJECTED)
        self.assertIn("dry_run_only", result.notes)

    def test_read_methods_are_not_implemented_yet(self):
        adapter = MiniQMTAdapter()
        with self.assertRaises(NotImplementedError):
            adapter.read_account_snapshot()
        with self.assertRaises(NotImplementedError):
            adapter.read_positions()
        with self.assertRaises(NotImplementedError):
            adapter.read_orders()
        with self.assertRaises(NotImplementedError):
            adapter.read_trades()


if __name__ == "__main__":
    unittest.main()
