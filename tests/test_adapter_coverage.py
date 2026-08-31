import unittest
from unittest.mock import patch

import pandas as pd

from qsys.config import cfg
from qsys.data.adapter import InstrumentCoverageReport, QlibAdapter
from qsys.data.health import inspect_qlib_data_health


class TestAdapterCoverage(unittest.TestCase):
    @patch.object(QlibAdapter, "touch_qlib_mtime")
    @patch("qsys.data.adapter.subprocess.run")
    def test_run_dump_script_forwards_max_workers(self, mock_run, _mock_touch):
        adapter = QlibAdapter()
        adapter._run_dump_script(
            adapter.qlib_dir.parent / "qlib_csv_tmp_missing",
            mode="dump_fix",
            refresh_universes=[],
            max_workers=3,
        )

        command = mock_run.call_args.args[0]
        self.assertEqual(command[-2:], ["--max_workers", "3"])

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

    @patch.object(QlibAdapter, "_run_dump_script")
    @patch.object(QlibAdapter, "_prepare_csvs")
    def test_convert_fix_rebuilds_full_history_for_affected_symbols(
        self,
        mock_prepare_csvs,
        mock_run_dump,
    ):
        adapter = QlibAdapter()
        mock_prepare_csvs.return_value = (adapter.qlib_dir.parent / "qlib_csv_tmp", 2)

        adapter.convert_fix(pd.Timestamp("2026-04-17"))

        mock_prepare_csvs.assert_called_once()
        mock_run_dump.assert_called_once()
        self.assertEqual(mock_run_dump.call_args.kwargs["mode"], "dump_fix")

    @patch.object(QlibAdapter, "touch_qlib_mtime")
    def test_convert_fix_touches_qlib_when_nothing_needs_refresh(self, mock_touch):
        adapter = QlibAdapter()

        adapter.convert_fix(None)

        mock_touch.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()


# ── convert_fix inlined-fs tests (real feather files) ────────────────────────


def test_convert_fix_selects_symbols_with_new_data(data_dir):
    """convert_fix scans raw_dir and only selects symbols whose max date >= threshold."""
    import numpy as np

    raw_dir = cfg.get_path("canonical_dir")  # convert_fix reads from adapter.raw_dir
    adapter = QlibAdapter(raw_dir=raw_dir)

    # Symbols A has data through 2026-04-17, B only to 2026-04-10, C no data
    pd.DataFrame({"trade_date": ["20260410", "20260417"]}).to_feather(
        raw_dir / "A.feather"
    )
    pd.DataFrame({"trade_date": ["20260410"]}).to_feather(
        raw_dir / "B.feather"
    )
    pd.DataFrame({"trade_date": []}).to_feather(
        raw_dir / "empty.feather"
    )

    with patch.object(adapter, "_prepare_csvs", return_value=(raw_dir, 2)) as mock_prep, \
         patch.object(adapter, "_run_dump_script"):
        adapter.convert_fix(pd.Timestamp("2026-04-17"))

    mock_prep.assert_called_once()
    call_kwargs = mock_prep.call_args.kwargs
    assert "selected_symbols" in call_kwargs
    assert sorted(call_kwargs["selected_symbols"]) == sorted(["A"])


def test_convert_fix_no_data_does_not_call_convert(data_dir):
    """When no symbol has data at or after threshold, convert_fix is a no-op."""
    raw_dir = cfg.get_path("canonical_dir")
    adapter = QlibAdapter(raw_dir=raw_dir)

    pd.DataFrame({"trade_date": ["20260410"]}).to_feather(raw_dir / "X.feather")

    with patch.object(adapter, "touch_qlib_mtime") as mock_touch, \
         patch.object(adapter, "_prepare_csvs") as mock_prep, \
         patch.object(adapter, "_run_dump_script") as mock_dump:
        adapter.convert_fix(pd.Timestamp("2026-04-17"))

    mock_touch.assert_called_once()
    mock_prep.assert_not_called()
    mock_dump.assert_not_called()
