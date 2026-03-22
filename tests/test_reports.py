import tempfile
import unittest
from pathlib import Path

from qsys.reports import (
    BacktestReport,
    DataUpdateReport,
    DailyOpsReport,
    ReportStatus,
    RunReport,
    StrictEvalReport,
    TrainingReport,
    load_report,
    save_report,
)


class TestRunReports(unittest.TestCase):
    def test_base_report_roundtrip(self):
        report = RunReport(workflow="daily_ops_pre_open", signal_date="2026-03-20", execution_date="2026-03-23")
        report.status = ReportStatus.SUCCESS
        report.set_data_status(raw_latest="2026-03-20", qlib_latest="2026-03-20", aligned=True, health_ok=True)
        report.set_model_info(model_path="data/models/qlib_lgbm", feature_set="extended")
        report.add_section("checklist", status=ReportStatus.SUCCESS, metrics={"steps": 3})

        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_report(report, tmpdir)
            loaded = load_report(path)

        self.assertEqual(loaded.workflow, report.workflow)
        self.assertEqual(loaded.signal_date, "2026-03-20")
        self.assertEqual(loaded.data_status["aligned"], True)
        self.assertEqual(len(loaded.sections), 1)

    def test_daily_report_generation(self):
        report = DailyOpsReport.generate_pre_open_report(
            signal_date="2026-03-20",
            execution_date="2026-03-23",
            data_status={"aligned": True},
            model_info={"model_path": "data/models/qlib_lgbm"},
            shadow_plan_summary={"trades": 5},
            real_plan_summary={"trades": 2},
            blockers=[],
        )
        self.assertEqual(report.workflow, "daily_ops_pre_open")
        self.assertEqual(report.status, ReportStatus.SUCCESS)
        self.assertEqual(len(report.sections), 2)

    def test_daily_report_empty_plan(self):
        """Test that empty plan is handled correctly"""
        report = DailyOpsReport.generate_pre_open_report(
            signal_date="2026-03-20",
            execution_date="2026-03-23",
            data_status={"aligned": True},
            model_info={"model_path": "data/models/qlib_lgbm"},
            shadow_plan_summary={"trades": 0},  # Empty
            real_plan_summary={"trades": 0},    # Empty
            blockers=[],
        )
        self.assertEqual(report.status, ReportStatus.SKIPPED)
        
    def test_daily_report_partial_empty_plan(self):
        """Test that one empty plan results in PARTIAL status"""
        report = DailyOpsReport.generate_pre_open_report(
            signal_date="2026-03-20",
            execution_date="2026-03-23",
            data_status={"aligned": True},
            model_info={"model_path": "data/models/qlib_lgbm"},
            shadow_plan_summary={"trades": 5},
            real_plan_summary={"trades": 0},  # Empty
            blockers=[],
        )
        self.assertEqual(report.status, ReportStatus.PARTIAL)

    def test_daily_report_with_blockers(self):
        """Test that blockers result in PARTIAL status"""
        report = DailyOpsReport.generate_pre_open_report(
            signal_date="2026-03-20",
            execution_date="2026-03-23",
            data_status={"aligned": True},
            model_info={"model_path": "data/models/qlib_lgbm"},
            shadow_plan_summary={"trades": 5},
            real_plan_summary={"trades": 2},
            blockers=["Data health check failed"],
        )
        self.assertEqual(report.status, ReportStatus.PARTIAL)
        self.assertIn("Data health check failed", report.blockers)

    def test_daily_report_data_anomaly(self):
        """Test that data anomalies are captured in notes"""
        report = DailyOpsReport.generate_pre_open_report(
            signal_date="2026-03-20",
            execution_date="2026-03-23",
            data_status={"aligned": False, "health_ok": False},
            model_info={"model_path": "data/models/qlib_lgbm"},
            shadow_plan_summary={"trades": 5},
            real_plan_summary={"trades": 2},
            blockers=[],
        )
        notes_text = " ".join(report.notes)
        self.assertIn("Data health", notes_text)
        self.assertIn("aligned", notes_text)

    def test_training_report_generation(self):
        report = TrainingReport.generate(
            signal_date="2026-03-20",
            data_status={"aligned": True},
            model_info={"model_name": "qlib_lgbm_extended"},
            training_metrics={"mse": 0.9, "rank_ic": 0.1},
            feature_count=173,
            sample_count=1000,
        )
        self.assertEqual(report.workflow, "train")
        self.assertEqual(report.status, ReportStatus.SUCCESS)
        self.assertEqual(report.model_info["feature_count"], 173)
        self.assertEqual(report.model_info["sample_count"], 1000)

    def test_backtest_report_generation(self):
        report = BacktestReport.generate(
            start_date="2025-01-01",
            end_date="2025-12-31",
            model_info={"model_path": "data/models/qlib_lgbm"},
            metrics={"sharpe": 1.5, "annual_return": "15%", "max_drawdown": "-10%"},
            top_k=30,
            universe="csi300",
        )
        self.assertEqual(report.workflow, "backtest")
        self.assertEqual(report.status, ReportStatus.SUCCESS)
        self.assertEqual(len(report.sections), 1)
        self.assertEqual(report.model_info.get("top_k"), 30)
        
    def test_strict_eval_report_generation(self):
        results = [
            {"period": "2025-01~2025-06", "model": "Baseline", "annual_return": 0.10, "sharpe": 1.0, "max_drawdown": -0.05, "trade_count": 50},
            {"period": "2025-01~2025-06", "model": "Extended", "annual_return": 0.15, "sharpe": 1.2, "max_drawdown": -0.08, "trade_count": 60},
        ]
        report = StrictEvalReport.generate(
            baseline_model_path="data/models/qlib_lgbm",
            extended_model_path="data/models/qlib_lgbm_extended",
            end_date="2025-06-30",
            results=results,
            top_k=5,
        )
        self.assertEqual(report.workflow, "strict_eval")
        self.assertEqual(len(report.sections), 2)
        
    def test_data_update_report_aligned(self):
        report = DataUpdateReport.generate(
            raw_latest="2026-03-20",
            qlib_latest="2026-03-20",
            aligned=True,
            gap_days=0,
        )
        self.assertEqual(report.workflow, "data_update")
        self.assertEqual(report.status, ReportStatus.SUCCESS)
        
    def test_data_update_report_misaligned(self):
        report = DataUpdateReport.generate(
            raw_latest="2026-03-20",
            qlib_latest="2026-03-18",
            aligned=False,
            gap_days=2,
        )
        self.assertEqual(report.status, ReportStatus.PARTIAL)
        
    def test_data_update_report_with_blockers(self):
        report = DataUpdateReport.generate(
            raw_latest="2026-03-20",
            qlib_latest="2026-03-20",
            aligned=True,
            blockers=["Failed to sync some symbols"],
        )
        self.assertEqual(report.status, ReportStatus.PARTIAL)


if __name__ == "__main__":
    unittest.main()
