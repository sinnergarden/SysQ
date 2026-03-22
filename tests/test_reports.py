import tempfile
import unittest
from pathlib import Path

from qsys.reports import DailyOpsReport, ReportStatus, RunReport, TrainingReport, load_report, save_report


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


if __name__ == "__main__":
    unittest.main()
