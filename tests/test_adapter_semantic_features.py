import unittest
from unittest.mock import patch

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
        self.assertIn("$high_limit", requested_fields)
        self.assertEqual(mock_dataset.dataset.call_args.kwargs["start_time"], "2025-02-27")
        self.assertEqual(mock_dataset.dataset.call_args.kwargs["end_time"], "2026-04-03")

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


if __name__ == "__main__":
    unittest.main()
