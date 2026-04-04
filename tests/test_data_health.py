import unittest
from unittest.mock import patch

import pandas as pd

from qsys.data.health import (
    DataHealthReport,
    DataReadinessError,
    assert_qlib_data_ready,
    inspect_qlib_data_health,
)


class TestDataHealth(unittest.TestCase):
    @patch("qsys.data.health._resolve_expected_latest_date", return_value=("2026-03-25", "2026-03-25"))
    @patch("qsys.data.health.StockDataStore")
    @patch("qsys.data.health.QlibAdapter")
    def test_inspect_health_flags_unusable_required_columns(self, mock_adapter_cls, mock_store_cls, _mock_expected):
        mock_store = mock_store_cls.return_value
        mock_store.get_global_latest_date.return_value = "2026-03-25"

        mock_adapter = mock_adapter_cls.return_value
        mock_adapter.get_last_qlib_date.return_value = pd.Timestamp("2026-03-25")
        mock_adapter.get_features.side_effect = [
            pd.DataFrame({"$close": [1.0, 2.0], "$open": [1.1, 2.1]}),
            pd.DataFrame(
                {
                    "$open": [1.0, 2.0],
                    "$high": [1.1, 2.1],
                    "$low": [0.9, 1.9],
                    "$close": [float("nan"), float("nan")],
                    "$volume": [100.0, 200.0],
                    "$factor": [1.0, 1.0],
                    "$new_factor": [float("nan"), float("nan")],
                }
            ),
        ]

        report = inspect_qlib_data_health(
            "2026-03-25",
            ["$close", "Ref($new_factor, 1)"],
            universe="all",
            optional_field_missing_threshold=0.9,
        )

        self.assertFalse(report.ok)
        self.assertIn("$close", report.unusable_required_fields)
        self.assertIn("Required qlib columns unusable", " ".join(report.blocking_issues))

    @patch("qsys.data.health._resolve_expected_latest_date", return_value=("2026-03-25", "2026-03-25"))
    @patch("qsys.data.health.StockDataStore")
    @patch("qsys.data.health.QlibAdapter")
    def test_pit_and_margin_gaps_are_non_blocking_warnings(self, mock_adapter_cls, mock_store_cls, _mock_expected):
        mock_store = mock_store_cls.return_value
        mock_store.get_global_latest_date.return_value = "2026-03-25"

        mock_adapter = mock_adapter_cls.return_value
        mock_adapter.get_last_qlib_date.return_value = pd.Timestamp("2026-03-25")
        mock_adapter.get_features.side_effect = [
            pd.DataFrame({"$close": [1.0], "$roe": [float("nan")], "$margin_balance": [float("nan")]}),
            pd.DataFrame(
                {
                    "$open": [1.0], "$high": [1.1], "$low": [0.9], "$close": [1.0], "$volume": [100.0], "$factor": [1.0],
                    "$roe": [float("nan")], "$margin_balance": [float("nan")],
                }
            ),
        ]
        report = inspect_qlib_data_health(
            "2026-03-25",
            ["$close", "$roe", "$margin_balance"],
            universe="all",
            pit_optional_field_missing_threshold=0.9,
            margin_optional_field_missing_threshold=0.9,
        )
        self.assertTrue(report.ok)
        self.assertEqual(report.core_daily_status, "ok")
        self.assertEqual(report.pit_status, "warning")
        self.assertEqual(report.margin_status, "warning")
        self.assertTrue(any("non-blocking" in item for item in report.warnings))

    def test_assert_qlib_data_ready_raises_clear_exception(self):
        failing_report = DataHealthReport(
            requested_date="2026-03-25",
            raw_latest="2026-03-25",
            last_qlib_date="2026-03-25",
            trading_calendar_last_date="2026-03-25",
            expected_latest_date="2026-03-25",
            date_ok=True,
            feature_rows=0,
            feature_cols=0,
            missing_ratio=1.0,
            has_data_for_requested_date=False,
            gap_days=0,
            aligned=True,
            required_fields=["$close"],
            monitored_fields=["$close"],
            column_missing_ratio={"$close": 1.0},
            unusable_required_fields=["$close"],
            unusable_optional_fields=[],
            issues=["No feature rows available for requested_date=2026-03-25"],
            blocking_issues=["No feature rows available for requested_date=2026-03-25"],
        )

        with patch("qsys.data.health.inspect_qlib_data_health", return_value=failing_report):
            with self.assertRaises(DataReadinessError) as ctx:
                assert_qlib_data_ready("2026-03-25", ["$close"])

        self.assertIn("2026-03-25", str(ctx.exception))
        self.assertIn("No feature rows available", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
