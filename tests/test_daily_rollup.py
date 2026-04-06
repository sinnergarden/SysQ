import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from qsys.live.derived_rollup import rollup_daily_artifacts
from qsys.live.ops_manifest import update_manifest
from qsys.live.ops_paths import build_stage_paths


class TestDailyRollup(unittest.TestCase):
    def test_rollup_appends_daily_evidence_without_duplicates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            daily_root = project_root / "daily"
            derived_root = project_root / "data" / "derived"
            execution_date = "2026-04-03"
            signal_date = "2026-04-02"

            pre_open_paths = build_stage_paths(execution_date, stage="pre_open", daily_root=daily_root)
            post_close_paths = build_stage_paths(execution_date, stage="post_close", daily_root=daily_root)
            for path in [
                pre_open_paths.signals_dir,
                pre_open_paths.order_intents_dir,
                pre_open_paths.manifests_dir,
                post_close_paths.reconciliation_dir,
                post_close_paths.manifests_dir,
            ]:
                path.mkdir(parents=True, exist_ok=True)

            signal_path = pre_open_paths.signals_dir / f"signal_basket_{signal_date}.csv"
            pd.DataFrame(
                [
                    {
                        "symbol": "AAA",
                        "score": 1.2,
                        "score_rank": 1,
                        "weight": 0.6,
                        "price": 10.0,
                        "signal_date": signal_date,
                        "execution_date": execution_date,
                        "model_name": "demo_model",
                        "model_path": "data/models/demo_model",
                        "universe": "csi300",
                    },
                    {
                        "symbol": "BBB",
                        "score": 0.8,
                        "score_rank": 2,
                        "weight": 0.4,
                        "price": 20.0,
                        "signal_date": signal_date,
                        "execution_date": execution_date,
                        "model_name": "demo_model",
                        "model_path": "data/models/demo_model",
                        "universe": "csi300",
                    },
                ]
            ).to_csv(signal_path, index=False)

            intents_path = pre_open_paths.order_intents_dir / f"order_intents_{execution_date}_shadow.json"
            intents_path.write_text(
                json.dumps(
                    {
                        "signal_date": signal_date,
                        "execution_date": execution_date,
                        "account_name": "shadow",
                        "intents": [
                            {
                                "intent_id": f"{execution_date}:shadow:buy:AAA",
                                "symbol": "AAA",
                                "side": "buy",
                                "amount": 100,
                                "price": 10.0,
                                "est_value": 1000.0,
                                "score": 1.2,
                                "score_rank": 1,
                                "weight": 0.6,
                                "target_value": 6000.0,
                                "current_value": 0.0,
                                "diff_value": 6000.0,
                                "execution_bucket": "after_sell_cash",
                                "cash_dependency": "requires_available_cash",
                                "t1_rule": "new_buy_not_sellable_until_next_session",
                                "plan_role": "target_portfolio_delta",
                                "status": "planned",
                                "note": "demo intent",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            pd.DataFrame(
                [
                    {"metric": "cash", "real": 100000.0, "shadow": 99500.0, "diff": 500.0},
                    {"metric": "total_assets", "real": 150000.0, "shadow": 149000.0, "diff": 1000.0},
                ]
            ).to_csv(post_close_paths.reconciliation_dir / f"reconcile_summary_{execution_date}.csv", index=False)
            pd.DataFrame(
                [
                    {
                        "symbol": "AAA",
                        "real_amount": 100,
                        "shadow_amount": 0,
                        "amount_diff": 100,
                        "real_price": 10.0,
                        "shadow_price": 10.0,
                        "real_cost_basis": 10.0,
                        "shadow_cost_basis": 10.0,
                        "cost_basis_diff": 0.0,
                        "real_market_value": 1000.0,
                        "shadow_market_value": 0.0,
                        "market_value_diff": 1000.0,
                    },
                    {
                        "symbol": "BBB",
                        "real_amount": 100,
                        "shadow_amount": 100,
                        "amount_diff": 0,
                        "real_price": 20.0,
                        "shadow_price": 20.0,
                        "real_cost_basis": 20.0,
                        "shadow_cost_basis": 20.0,
                        "cost_basis_diff": 0.0,
                        "real_market_value": 2000.0,
                        "shadow_market_value": 2000.0,
                        "market_value_diff": 0.0,
                    },
                ]
            ).to_csv(post_close_paths.reconciliation_dir / f"reconcile_positions_{execution_date}.csv", index=False)

            update_manifest(
                report_dir=post_close_paths.manifests_dir,
                execution_date=execution_date,
                signal_date=signal_date,
                stage="post_close",
                status="success",
            )

            first = rollup_daily_artifacts(
                execution_date=execution_date,
                daily_root=daily_root,
                derived_root=derived_root,
            )
            second = rollup_daily_artifacts(
                execution_date=execution_date,
                daily_root=daily_root,
                derived_root=derived_root,
            )

            self.assertEqual(first.tables["signal_baskets"].added_rows, 2)
            self.assertEqual(first.tables["order_intents"].added_rows, 1)
            self.assertEqual(first.tables["reconciliation_summary"].added_rows, 2)
            self.assertEqual(first.tables["position_gaps"].added_rows, 1)
            self.assertEqual(second.tables["signal_baskets"].added_rows, 0)
            self.assertEqual(second.tables["order_intents"].added_rows, 0)
            self.assertEqual(second.tables["reconciliation_summary"].added_rows, 0)
            self.assertEqual(second.tables["position_gaps"].added_rows, 0)

            signal_rollup = pd.read_csv(derived_root / "signal_baskets.csv")
            intents_rollup = pd.read_csv(derived_root / "order_intents.csv")
            reconciliation_rollup = pd.read_csv(derived_root / "reconciliation_summary.csv")
            position_gap_rollup = pd.read_csv(derived_root / "position_gaps.csv")

            self.assertEqual(signal_rollup["account_name"].unique().tolist(), ["shared"])
            self.assertEqual(intents_rollup["account_name"].unique().tolist(), ["shadow"])
            self.assertEqual(reconciliation_rollup["account_name"].unique().tolist(), ["real_vs_shadow"])
            self.assertEqual(position_gap_rollup["symbol"].tolist(), ["AAA"])
            self.assertTrue(signal_rollup["artifact_source"].iloc[0].startswith("daily/2026-04-03/"))


if __name__ == "__main__":
    unittest.main()
