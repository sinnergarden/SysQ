import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from qsys.data.adapter import QlibAdapter
from qsys.live.ops_paths import build_stage_paths, find_plan_path_for_execution_date
import scripts.run_daily_trading as run_daily_trading
import scripts.run_post_close as run_post_close
from scripts.run_daily_trading import (
    _resolve_cli_path,
    _resolve_ops_paths as resolve_preopen_ops_paths,
    extract_plan_summary,
    previous_trading_day,
    resolve_signal_and_execution_date,
)
from scripts.run_update import _normalize_date


class TestCliSemantics(unittest.TestCase):
    def test_run_update_accepts_dash_date(self):
        self.assertEqual(_normalize_date("2023-01-01"), "20230101")
        self.assertEqual(_normalize_date("20230101"), "20230101")

    @patch("scripts.run_daily_trading.QlibAdapter")
    @patch("scripts.run_daily_trading.D")
    @patch("scripts.run_daily_trading.datetime")
    def test_future_date_is_treated_as_execution_date(self, mock_datetime, mock_d, mock_adapter_cls):
        mock_now = unittest.mock.Mock()
        mock_now.strftime.return_value = "2026-03-23"
        mock_datetime.now.return_value = mock_now
        mock_d.calendar.return_value = [pd.Timestamp("2026-03-23"), pd.Timestamp("2026-03-24")]

        future_execution_date = "2026-03-24"
        signal_date, execution_date = resolve_signal_and_execution_date(future_execution_date, None)
        self.assertEqual(signal_date, previous_trading_day(future_execution_date))
        self.assertEqual(execution_date, future_execution_date)
        self.assertGreaterEqual(mock_adapter_cls.return_value.init_qlib.call_count, 1)

    def test_explicit_execution_date_keeps_signal_date(self):
        signal_date, execution_date = resolve_signal_and_execution_date("2026-03-20", "2026-03-23")
        self.assertEqual(signal_date, "2026-03-20")
        self.assertEqual(execution_date, "2026-03-23")

    def test_run_daily_cli_paths_resolve_inside_project_root(self):
        resolved = Path(_resolve_cli_path("ops/reports"))
        self.assertTrue(resolved.is_absolute())
        self.assertEqual(resolved.name, "reports")
        self.assertIn("SysQ", str(resolved))

    def test_run_daily_defaults_use_dated_daily_layout(self):
        resolved = resolve_preopen_ops_paths(
            execution_date="2026-03-23",
            output_dir=None,
            report_dir=None,
            db_path=None,
        )
        self.assertTrue(str(resolved["output_dir"]).endswith("daily/2026-03-23/pre_open"))
        self.assertTrue(str(resolved["report_dir"]).endswith("daily/2026-03-23/pre_open/reports"))
        self.assertTrue(str(resolved["manifest_dir"]).endswith("daily/2026-03-23/pre_open/manifests"))
        self.assertTrue(str(resolved["db_path"]).endswith("data/meta/real_account.db"))

    def test_run_post_close_defaults_use_dated_daily_layout(self):
        resolved = run_post_close._resolve_ops_paths(
            execution_date="2026-03-23",
            output_dir=None,
            report_dir=None,
            plan_dir=None,
            db_path=None,
        )
        self.assertTrue(str(resolved["output_dir"]).endswith("daily/2026-03-23/post_close"))
        self.assertTrue(str(resolved["report_dir"]).endswith("daily/2026-03-23/post_close/reports"))
        self.assertTrue(str(resolved["plan_dir"]).endswith("daily/2026-03-23/pre_open/plans"))
        self.assertTrue(str(resolved["manifest_dir"]).endswith("daily/2026-03-23/post_close/manifests"))
        self.assertTrue(str(resolved["db_path"]).endswith("data/meta/real_account.db"))

    def test_build_stage_paths_only_creates_stage_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            daily_root = Path(tmpdir) / "daily"
            paths = build_stage_paths("2026-03-23", stage="pre_open", daily_root=daily_root)

            self.assertTrue(paths.root.exists())
            self.assertFalse(paths.plans_dir.exists())
            self.assertFalse(paths.order_intents_dir.exists())
            self.assertFalse(paths.signals_dir.exists())
            self.assertFalse(paths.reports_dir.exists())
            self.assertFalse(paths.manifests_dir.exists())

    def test_find_plan_path_only_reads_new_daily_layout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            legacy_plan = project_root / "data" / "plan_2026-03-20_shadow.csv"
            legacy_plan.parent.mkdir(parents=True)
            legacy_plan.write_text("symbol,execution_date\nAAA,2026-03-23\n", encoding="utf-8")

            resolved = find_plan_path_for_execution_date(
                execution_date="2026-03-23",
                account_name="shadow",
                daily_root=project_root / "daily",
            )

            self.assertIsNone(resolved)

    def test_extract_plan_summary_is_structured_and_low_noise(self):
        plan = run_daily_trading.pd.DataFrame(
            [
                {"symbol": "000001.SZ", "side": "buy", "amount": 100, "est_value": 1200.0},
                {"symbol": "000002.SZ", "side": "sell", "amount": 200, "est_value": 2300.0},
                {"symbol": "000003.SZ", "side": "buy", "amount": 0, "est_value": 999.0},
            ]
        )
        summary = extract_plan_summary(plan, "shadow", "2026-03-20", "2026-03-23")
        self.assertEqual(summary["status"], "ready")
        self.assertEqual(summary["trades"], 2)
        self.assertEqual(summary["buy_trades"], 1)
        self.assertEqual(summary["sell_trades"], 1)
        self.assertEqual(summary["total_value"], 3500.0)
        self.assertEqual(summary["symbols"], ["000001.SZ", "000002.SZ"])

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
