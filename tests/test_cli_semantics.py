import unittest
from pathlib import Path
from unittest.mock import patch

from qsys.data.adapter import QlibAdapter
import scripts.run_daily_trading as run_daily_trading
from scripts.run_daily_trading import (
    _resolve_cli_path,
    next_trading_day,
    previous_trading_day,
    resolve_signal_and_execution_date,
)
from scripts.run_update import _normalize_date


class TestCliSemantics(unittest.TestCase):
    def test_run_update_accepts_dash_date(self):
        self.assertEqual(_normalize_date("2023-01-01"), "20230101")
        self.assertEqual(_normalize_date("20230101"), "20230101")

    @patch("scripts.run_daily_trading.datetime")
    def test_future_date_is_treated_as_execution_date(self, mock_datetime):
        QlibAdapter().init_qlib()
        mock_now = unittest.mock.Mock()
        mock_now.strftime.return_value = "2026-03-23"
        mock_datetime.now.return_value = mock_now

        future_execution_date = "2026-03-24"
        signal_date, execution_date = resolve_signal_and_execution_date(future_execution_date, None)
        self.assertEqual(signal_date, previous_trading_day(future_execution_date))
        self.assertEqual(execution_date, future_execution_date)

    def test_explicit_execution_date_keeps_signal_date(self):
        signal_date, execution_date = resolve_signal_and_execution_date("2026-03-20", "2026-03-23")
        self.assertEqual(signal_date, "2026-03-20")
        self.assertEqual(execution_date, "2026-03-23")

    def test_run_daily_cli_paths_resolve_inside_project_root(self):
        resolved = Path(_resolve_cli_path("ops/reports"))
        self.assertTrue(resolved.is_absolute())
        self.assertEqual(resolved.name, "reports")
        self.assertIn("SysQ", str(resolved))

    @patch("scripts.run_daily_trading.log")
    @patch("scripts.run_daily_trading.DailyOpsReport.save", return_value="/tmp/custom/daily_ops_pre_open_1.json")
    @patch("scripts.run_daily_trading.DailyOpsReport.generate_pre_open_report")
    @patch("scripts.run_daily_trading.update_data", return_value=(False, {"aligned": False}))
    def test_require_update_success_blocks_and_uses_custom_report_dir(
        self,
        _mock_update_data,
        mock_generate_report,
        mock_save,
        _mock_log,
    ):
        report = unittest.mock.Mock()
        report.artifacts = {}
        mock_generate_report.return_value = report

        with patch("sys.argv", [
            "run_daily_trading.py",
            "--date", "2026-03-20",
            "--require_update_success",
            "--report_dir", "tmp_reports",
            "--db_path", "tmp_state/account.db",
        ]):
            run_daily_trading.main()

        self.assertEqual(report.artifacts["account_db"], _resolve_cli_path("tmp_state/account.db"))
        mock_save.assert_called_once()
        self.assertEqual(mock_save.call_args.kwargs["output_dir"], _resolve_cli_path("tmp_reports"))


if __name__ == "__main__":
    unittest.main()
