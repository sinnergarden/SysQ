import unittest
from unittest.mock import patch

import pandas as pd

from qsys.data.adapter import InstrumentCoverageReport, QlibAdapter
from qsys.data.health import inspect_qlib_data_health


class TestAdapterCoverage(unittest.TestCase):
    @patch.object(QlibAdapter, "_refresh_universe_instruments")
    @patch.object(QlibAdapter, "get_instrument_coverage_report")
    def test_ensure_instrument_coverage_refreshes_and_rechecks(self, mock_report, mock_refresh):
        mock_report.side_effect = [
            InstrumentCoverageReport(
                calendar_latest="2026-04-03",
                all_latest="2026-04-03",
                universe="csi300",
                universe_latest="2026-04-02",
            ),
            InstrumentCoverageReport(
                calendar_latest="2026-04-03",
                all_latest="2026-04-03",
                universe="csi300",
                universe_latest="2026-04-03",
            ),
        ]

        report = QlibAdapter().ensure_instrument_coverage("csi300", refresh_on_mismatch=True)

        self.assertTrue(report.is_closed)
        mock_refresh.assert_called_once_with(universe="csi300")
        self.assertEqual(mock_report.call_count, 2)

    @patch.object(QlibAdapter, "ensure_instrument_coverage")
    @patch.object(QlibAdapter, "check_and_update")
    @patch.object(QlibAdapter, "_get_raw_latest_date")
    @patch.object(QlibAdapter, "get_last_qlib_date")
    def test_refresh_qlib_date_raises_when_universe_stays_stale(
        self,
        mock_last_qlib_date,
        mock_raw_latest,
        mock_check_and_update,
        mock_ensure_coverage,
    ):
        mock_raw_latest.return_value = pd.Timestamp("2026-04-03")
        mock_last_qlib_date.return_value = pd.Timestamp("2026-04-03")
        mock_ensure_coverage.return_value = InstrumentCoverageReport(
            calendar_latest="2026-04-03",
            all_latest="2026-04-03",
            universe="csi300",
            universe_latest="2026-04-02",
        )

        with self.assertRaisesRegex(RuntimeError, "coverage mismatch blocks planning"):
            QlibAdapter().refresh_qlib_date()

        mock_check_and_update.assert_called_once_with(force=False)
        mock_ensure_coverage.assert_called_once_with("csi300", refresh_on_mismatch=True)

    @patch("qsys.data.health._resolve_expected_latest_date", return_value=("2026-04-03", "2026-04-03"))
    @patch("qsys.data.health.StockDataStore")
    @patch("qsys.data.health.QlibAdapter")
    def test_health_blocks_when_universe_coverage_is_not_closed(self, mock_adapter_cls, mock_store_cls, _mock_expected):
        mock_store_cls.return_value.get_global_latest_date.return_value = "2026-04-03"

        mock_adapter = mock_adapter_cls.return_value
        mock_adapter.get_last_qlib_date.return_value = pd.Timestamp("2026-04-03")
        mock_adapter.get_instrument_coverage_report.return_value = InstrumentCoverageReport(
            calendar_latest="2026-04-03",
            all_latest="2026-04-03",
            universe="csi300",
            universe_latest="2026-04-02",
        )
        mock_adapter.get_features.side_effect = [
            pd.DataFrame({"$close": [1.0]}),
            pd.DataFrame({"$open": [1.0], "$high": [1.1], "$low": [0.9], "$close": [1.0], "$volume": [100.0], "$factor": [1.0]}),
        ]

        report = inspect_qlib_data_health("2026-04-03", ["$close"], universe="csi300")

        self.assertFalse(report.ok)
        self.assertTrue(any("coverage mismatch blocks planning" in issue for issue in report.blocking_issues))


if __name__ == "__main__":
    unittest.main()
