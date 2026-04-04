import json
import tempfile
import unittest

import pandas as pd

from qsys.trader.order_intents import build_order_intents, save_order_intents


class TestOrderIntents(unittest.TestCase):
    def test_build_order_intents_from_plan(self):
        plan = pd.DataFrame(
            [
                {
                    "symbol": "600000.SH",
                    "side": "buy",
                    "amount": 300,
                    "price": 10.5,
                    "est_value": 3150.0,
                    "score": 0.88,
                    "score_rank": 1,
                    "weight": 0.2,
                    "target_value": 20000.0,
                    "current_value": 0.0,
                    "diff_value": 20000.0,
                    "execution_bucket": "after_sell_cash",
                    "cash_dependency": "requires_available_cash",
                    "t1_rule": "new_buy_not_sellable_until_next_session",
                    "plan_role": "target_portfolio_delta",
                    "price_basis_date": "2026-04-03",
                    "price_basis_field": "close",
                    "price_basis_label": "close@2026-04-03 -> next-session execution plan",
                    "status": "planned",
                    "note": "Baseline rotation",
                }
            ]
        )

        payload = build_order_intents(
            plan,
            signal_date="2026-04-03",
            execution_date="2026-04-06",
            account_name="real",
            model_info={"model_name": "qlib_lgbm_prod"},
            assumptions={"top_k": 5},
        )

        self.assertEqual(payload["artifact_type"], "order_intents")
        self.assertEqual(payload["intent_count"], 1)
        self.assertEqual(payload["intents"][0]["symbol"], "600000.SH")
        self.assertEqual(payload["intents"][0]["execution_bucket"], "after_sell_cash")
        self.assertEqual(payload["intents"][0]["price_basis"]["field"], "close")

    def test_save_order_intents_writes_json(self):
        payload = {
            "artifact_type": "order_intents",
            "signal_date": "2026-04-03",
            "execution_date": "2026-04-06",
            "account_name": "real",
            "model_info": {},
            "assumptions": {},
            "intent_count": 0,
            "intents": [],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_order_intents(
                payload,
                output_dir=tmpdir,
                execution_date="2026-04-06",
                account_name="real",
            )
            with open(path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)

        self.assertEqual(loaded["artifact_type"], "order_intents")
        self.assertEqual(loaded["account_name"], "real")


if __name__ == "__main__":
    unittest.main()
