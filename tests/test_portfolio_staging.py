import unittest

import pandas as pd

from qsys.strategy.portfolio import build_portfolio_intent
from qsys.trader.staging import stage_orders


class TestPortfolioIntent(unittest.TestCase):
    def test_blacklist_and_top_k_emit_reason_codes(self):
        raw_scores = pd.DataFrame(
            [
                {"ts_code": "600000.SH", "score": 0.91, "industry": "Bank"},
                {"ts_code": "000001.SZ", "score": 0.88, "industry": "Bank"},
                {"ts_code": "600519.SH", "score": 0.79, "industry": "Consumer"},
                {"ts_code": "300750.SZ", "score": 0.72, "industry": "Battery"},
            ]
        )
        broker_snapshot = {
            "positions": [
                {"symbol": "600000.SH", "total_amount": 300},
                {"symbol": "300750.SZ", "total_amount": 200},
            ]
        }
        result = build_portfolio_intent(
            raw_scores,
            broker_snapshot=broker_snapshot,
            risk_rules={
                "blacklist": ["300750.SZ"],
                "max_positions": 2,
            },
        )

        self.assertEqual(list(result.target_weights["ts_code"]), ["600000.SH", "000001.SZ"])
        self.assertEqual(result.target_weights.set_index("ts_code").loc["600000.SH", "current_qty"], 300)

        reason_index = {(item["ts_code"], item["reason"]) for item in result.reason_codes}
        self.assertIn(("300750.SZ", "rejected_blacklist"), reason_index)
        self.assertIn(("600519.SH", "rejected_max_positions"), reason_index)
        self.assertIn(("600000.SH", "selected_score_rank"), reason_index)
        self.assertIn(("000001.SZ", "selected_score_rank"), reason_index)


class TestOrderStaging(unittest.TestCase):
    def test_buy_qty_rounds_down_to_lot_size(self):
        target_weights = pd.DataFrame(
            [{"ts_code": "600000.SH", "target_weight": 0.2, "score": 0.9}]
        )
        broker_snapshot = {
            "account_snapshot": {"available_cash": 10000.0, "total_assets": 10000.0},
            "positions": [{"symbol": "600000.SH", "total_amount": 50, "sellable_amount": 50}],
        }
        market_data = pd.DataFrame(
            [{"ts_code": "600000.SH", "latest_price": 10.0, "limit_up_price": 11.0, "limit_down_price": 9.0}]
        )

        result = stage_orders(target_weights, broker_snapshot, market_data)

        order = result.orders.iloc[0].to_dict()
        self.assertEqual(order["side"], "buy")
        self.assertEqual(order["requested_qty"], 100)
        self.assertEqual(order["staging_status"], "adjusted")
        self.assertIn(
            ("600000.SH", "buy_qty_rounded_down_lot_size"),
            {(item["ts_code"], item["reason"]) for item in result.reason_codes},
        )

    def test_limit_up_and_limit_down_orders_are_rejected(self):
        target_weights = pd.DataFrame(
            [{"ts_code": "000001.SZ", "target_weight": 0.2, "score": 0.8}]
        )
        broker_snapshot = {
            "account_snapshot": {"available_cash": 10000.0, "total_assets": 20000.0},
            "positions": [{"symbol": "600000.SH", "total_amount": 300, "sellable_amount": 300}],
        }
        market_data = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "latest_price": 10.0, "limit_up_price": 10.0, "limit_down_price": 9.0},
                {"ts_code": "600000.SH", "latest_price": 8.0, "limit_up_price": 8.8, "limit_down_price": 8.0},
            ]
        )

        result = stage_orders(target_weights, broker_snapshot, market_data)

        orders = result.orders.set_index(["ts_code", "side"])
        self.assertEqual(orders.loc[("000001.SZ", "buy"), "staging_status"], "rejected")
        self.assertEqual(orders.loc[("600000.SH", "sell"), "staging_status"], "rejected")

        reason_index = {(item["ts_code"], item["reason"]) for item in result.reason_codes}
        self.assertIn(("000001.SZ", "buy_rejected_limit_up"), reason_index)
        self.assertIn(("600000.SH", "sell_rejected_limit_down"), reason_index)

    def test_buy_side_respects_cash_budget_and_records_reason(self):
        target_weights = pd.DataFrame(
            [{"ts_code": "600000.SH", "target_weight": 0.5, "score": 0.9}]
        )
        broker_snapshot = {
            "account_snapshot": {"available_cash": 1500.0, "total_assets": 4000.0},
            "positions": [],
        }
        market_data = pd.DataFrame(
            [{"ts_code": "600000.SH", "latest_price": 10.0, "limit_up_price": 11.0, "limit_down_price": 9.0}]
        )

        result = stage_orders(target_weights, broker_snapshot, market_data)

        order = result.orders.iloc[0].to_dict()
        self.assertEqual(order["side"], "buy")
        self.assertEqual(order["requested_qty"], 100)
        self.assertEqual(order["staging_status"], "adjusted")
        self.assertIn(
            ("600000.SH", "buy_qty_limited_by_cash"),
            {(item["ts_code"], item["reason"]) for item in result.reason_codes},
        )


if __name__ == "__main__":
    unittest.main()
