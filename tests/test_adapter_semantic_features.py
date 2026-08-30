import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np
import pandas as pd

from qsys.data.adapter import QlibAdapter


class TestAdapterSemanticFeatures(unittest.TestCase):
    def _mock_native_frame(self):
        dates = pd.to_datetime([
            "2026-03-30",
            "2026-03-31",
            "2026-04-01",
            "2026-04-02",
            "2026-04-03",
        ])
        index = pd.MultiIndex.from_product(
            [dates, ["AAA"]],
            names=["datetime", "instrument"],
        )
        return pd.DataFrame(
            {
                "$close": [10.0, 10.2, 10.4, 10.5, 10.8],
                "$open": [9.9, 10.1, 10.3, 10.4, 10.6],
                "$high": [10.1, 10.3, 10.5, 10.6, 10.9],
                "$low": [9.8, 10.0, 10.2, 10.3, 10.5],
                "$volume": [100.0, 120.0, 150.0, 180.0, 240.0],
                "$amount": [1000.0, 1320.0, 1800.0, 2250.0, 3120.0],
                "$turnover_rate": [0.01, 0.011, 0.013, 0.014, 0.016],
                "$paused": [0.0, 0.0, 0.0, 0.0, 0.0],
                "$high_limit": [11.0, 11.0, 11.0, 11.0, 11.0],
                "$low_limit": [9.0, 9.0, 9.0, 9.0, 9.0],
            },
            index=index,
        )

    @patch("qsys.data.adapter.DatasetD")
    def test_get_features_builds_semantic_columns_from_native_daily_path(self, mock_dataset):
        native_df = self._mock_native_frame()
        mock_dataset.dataset.return_value = native_df

        adapter = QlibAdapter()
        out = adapter.get_features(
            instruments=["AAA"],
            fields=["$close", "amount_log", "volume_shock_3", "is_limit_up"],
            start_time="2026-04-03",
            end_time="2026-04-03",
        )

        self.assertEqual(list(out.columns), ["$close", "amount_log", "volume_shock_3", "is_limit_up"])
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out.iloc[0]["$close"], 10.8)
        self.assertAlmostEqual(out.iloc[0]["amount_log"], np.log1p(3120.0))
        self.assertAlmostEqual(out.iloc[0]["volume_shock_3"], 240.0 / ((150.0 + 180.0 + 240.0) / 3.0))
        self.assertFalse(bool(out.iloc[0]["is_limit_up"]))

        requested_fields = mock_dataset.dataset.call_args.args[1]
        self.assertIn("$amount", requested_fields)
        self.assertIn("$factor", requested_fields)
        self.assertIn("$high_limit", requested_fields)
        self.assertEqual(mock_dataset.dataset.call_args.kwargs["start_time"], "2022-04-03")
        self.assertEqual(mock_dataset.dataset.call_args.kwargs["end_time"], "2026-04-03")

    def test_semantic_lookback_covers_756_trading_session_shift(self):
        start = QlibAdapter._semantic_lookback_start("2026-08-07", "2026-08-07")

        self.assertEqual(start, "2022-08-07")
        self.assertGreaterEqual(
            (pd.Timestamp("2026-08-07") - pd.Timestamp(start)).days,
            1461,
        )

    def test_semantic_pit_mask_preserves_reentry_gaps(self):
        frame = pd.DataFrame(
            {
                "ts_code": ["AAA"] * 5 + ["BBB"] * 5,
                "trade_date": pd.to_datetime(
                    [
                        "2023-01-02",
                        "2023-01-03",
                        "2023-01-04",
                        "2023-01-05",
                        "2023-01-06",
                    ]
                    * 2
                ),
            }
        )
        spans = pd.DataFrame(
            {
                "instrument": ["AAA", "AAA", "BBB"],
                "effective_from": ["20230102", "20230106", "20230103"],
                "effective_to": ["20230103", "20230106", "20230105"],
            }
        )

        marked = QlibAdapter._attach_semantic_pit_membership(
            frame, spans, "member_as_of"
        )

        self.assertEqual(
            marked["_pit_member"].tolist(),
            [True, True, False, False, True, False, True, True, True, False],
        )

    def test_industry_relative_features_enable_return_dependencies(self):
        flags = QlibAdapter._semantic_feature_flags(
            ["industry_top_stock_momentum", "stock_minus_industry_ret_60d"]
        )

        self.assertTrue(flags["enable_relative_strength_features"])
        self.assertTrue(flags["enable_industry_momentum_features"])
        self.assertTrue(flags["enable_industry_context_features"])

    def test_margin_interactions_enable_return_dependencies(self):
        flags = QlibAdapter._semantic_feature_flags(
            ["margin_trend_confirm_score", "margin_overheat_risk_score"]
        )

        self.assertTrue(flags["enable_relative_strength_features"])
        self.assertTrue(flags["enable_v3a_margin_features"])

    def test_path_scores_enable_price_context_dependencies(self):
        flags = QlibAdapter._semantic_feature_flags(
            [
                "continuation_candidate_score",
                "repair_candidate_score",
                "overheat_risk_score",
                "value_trap_risk_score",
            ]
        )

        self.assertTrue(flags["enable_relative_strength_features"])
        self.assertTrue(flags["enable_fundamental_context_features"])

    @patch("qsys.data.adapter.build_phase1_features")
    def test_growth_sidecar_identity_is_forwarded_with_requested_window(
        self, mock_build
    ):
        native_df = self._mock_native_frame()
        artifact = Path("/tmp/pinned-income.parquet")
        manifest = Path("/tmp/pinned-income-manifest.json")

        def _return_feature(frame, *, flags):
            result = frame.copy()
            result["ttm_revenue_yoy"] = 0.25
            return result

        mock_build.side_effect = _return_feature
        adapter = QlibAdapter(
            income_source_mode="audited_sidecar_v1",
            income_sidecar_path=artifact,
            income_sidecar_sha256="a" * 64,
            income_sidecar_manifest_path=manifest,
            income_sidecar_manifest_sha256="b" * 64,
            income_sidecar_required_history_start="2014-03-13",
        )

        result = adapter._build_semantic_features(
            native_df,
            ["ttm_revenue_yoy"],
            start_time="2026-04-02",
            end_time="2026-04-03",
        )

        self.assertEqual(len(result), 2)
        flags = mock_build.call_args.kwargs["flags"]
        self.assertEqual(flags["income_sidecar_path"], str(artifact))
        self.assertEqual(flags["income_sidecar_sha256"], "a" * 64)
        self.assertEqual(flags["income_sidecar_manifest_path"], str(manifest))
        self.assertEqual(flags["income_sidecar_manifest_sha256"], "b" * 64)
        self.assertEqual(flags["income_sidecar_required_start"], "2026-04-02")
        self.assertEqual(flags["income_sidecar_required_end"], "2026-04-03")
        self.assertEqual(flags["income_source_mode"], "audited_sidecar_v1")
        self.assertEqual(
            flags["income_sidecar_required_history_start"], "2014-03-13"
        )

    @patch("qsys.data.adapter.build_phase1_features")
    def test_growth_sidecar_end_is_bounded_by_consumed_qlib_rows(
        self, mock_build
    ):
        native_df = self._mock_native_frame()

        def _return_feature(frame, *, flags):
            result = frame.copy()
            result["ttm_revenue_yoy"] = 0.25
            return result

        mock_build.side_effect = _return_feature
        adapter = QlibAdapter(
            income_source_mode="audited_sidecar_v1",
            income_sidecar_path=Path("/tmp/pinned-income.parquet"),
            income_sidecar_sha256="a" * 64,
            income_sidecar_manifest_path=Path("/tmp/pinned-income-manifest.json"),
            income_sidecar_manifest_sha256="b" * 64,
            income_sidecar_required_history_start="2014-03-13",
        )

        adapter._build_semantic_features(
            native_df,
            ["ttm_revenue_yoy"],
            start_time="2026-04-02",
            end_time="2026-05-03",
        )

        flags = mock_build.call_args.kwargs["flags"]
        self.assertEqual(flags["income_sidecar_required_end"], "2026-04-03")

    @patch("qsys.data.adapter.DatasetD")
    def test_get_features_keeps_unavailable_semantic_columns_as_nan(self, mock_dataset):
        native_df = self._mock_native_frame().drop(columns=["$amount"])
        mock_dataset.dataset.return_value = native_df

        adapter = QlibAdapter()
        out = adapter.get_features(
            instruments=["AAA"],
            fields=["inventory_yoy", "amount_log"],
            start_time="2026-04-03",
            end_time="2026-04-03",
        )

        self.assertEqual(list(out.columns), ["inventory_yoy", "amount_log"])
        self.assertTrue(out["inventory_yoy"].isna().all())
        self.assertTrue(out["amount_log"].isna().all())

    @patch("qsys.data.adapter.DatasetD")
    def test_get_features_builds_raw_relative_strength_columns(self, mock_dataset):
        native_df = self._mock_native_frame()
        mock_dataset.dataset.return_value = native_df

        adapter = QlibAdapter()
        out = adapter.get_features(
            instruments=["AAA"],
            fields=["ret_3d", "amount_mean_3d", "amount_mean_5d"],
            start_time="2026-04-03",
            end_time="2026-04-03",
        )

        self.assertEqual(list(out.columns), ["ret_3d", "amount_mean_3d", "amount_mean_5d"])
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out.iloc[0]["ret_3d"], (10.8 / 10.2) - 1.0)
        self.assertAlmostEqual(out.iloc[0]["amount_mean_3d"], (1800.0 + 2250.0 + 3120.0) / 3.0)
        self.assertAlmostEqual(out.iloc[0]["amount_mean_5d"], (1000.0 + 1320.0 + 1800.0 + 2250.0 + 3120.0) / 5.0)

        requested_fields = mock_dataset.dataset.call_args.args[1]
        self.assertIn("$close", requested_fields)
        self.assertIn("$amount", requested_fields)
        self.assertIn("$volume", requested_fields)

    @patch("qsys.data.adapter.DatasetD")
    def test_semantic_price_returns_are_adjusted_without_mutating_native_price(
        self, mock_dataset
    ):
        native_df = self._mock_native_frame()
        native_df["$close"] = [10.0, 10.2, 5.2, 5.25, 5.4]
        native_df["$open"] = [9.9, 10.1, 5.1, 5.2, 5.3]
        native_df["$high"] = [10.1, 10.3, 5.3, 5.35, 5.45]
        native_df["$low"] = [9.8, 10.0, 5.0, 5.1, 5.2]
        native_df["$high_limit"] = [11.0, 11.2, 5.7, 5.75, 5.9]
        native_df["$low_limit"] = [9.0, 9.2, 4.7, 4.75, 4.9]
        native_df["$factor"] = [1.0, 1.0, 2.0, 2.0, 2.0]
        mock_dataset.dataset.return_value = native_df

        adapter = QlibAdapter()
        out = adapter.get_features(
            instruments=["AAA"],
            fields=["$close", "ret_3d"],
            start_time="2026-04-03",
            end_time="2026-04-03",
        )

        self.assertAlmostEqual(out.iloc[0]["$close"], 5.4)
        self.assertAlmostEqual(out.iloc[0]["ret_3d"], (5.4 * 2.0 / 10.2) - 1.0)
        requested_fields = mock_dataset.dataset.call_args.args[1]
        self.assertIn("$factor", requested_fields)


if __name__ == "__main__":
    unittest.main()
