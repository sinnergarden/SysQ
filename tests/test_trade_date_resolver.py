import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from qsys.ops.state import load_json
from qsys.ops.trade_date import resolve_daily_trade_date, resolve_training_end_date
from scripts.ops.run_shadow_retrain_weekly import run_shadow_retrain_weekly


class TestTradeDateResolver(unittest.TestCase):
    def _mock_calendar(self, adapter, dates: list[str]) -> None:
        cal_dir = Path(tempfile.mkdtemp()) / "calendars"
        cal_dir.mkdir(parents=True, exist_ok=True)
        (cal_dir / "day.txt").write_text("\n".join(dates) + "\n", encoding="utf-8")
        adapter.qlib_dir = cal_dir.parent

    def test_exact_match(self):
        with patch("qsys.ops.trade_date.QlibAdapter") as adapter_cls:
            adapter = adapter_cls.return_value
            adapter.get_last_qlib_date.return_value = pd.Timestamp("2026-04-25")
            adapter.get_features.return_value = pd.DataFrame({"$close": [1.0]})

            payload = resolve_daily_trade_date("2026-04-25")

        self.assertEqual(payload["requested_date"], "2026-04-25")
        self.assertEqual(payload["resolved_trade_date"], "2026-04-25")
        self.assertEqual(payload["last_qlib_date"], "2026-04-25")
        self.assertEqual(payload["status"], "success")
        self.assertTrue(payload["is_exact_match"])

    def test_requested_after_global_latest_can_fallback_to_global_latest(self):
        with patch("qsys.ops.trade_date.QlibAdapter") as adapter_cls:
            adapter = adapter_cls.return_value
            adapter.get_last_qlib_date.return_value = pd.Timestamp("2026-04-17")
            self._mock_calendar(adapter, ["2026-04-17"])
            adapter.get_features.side_effect = [
                pd.DataFrame(columns=["$close"]),
                pd.DataFrame({"$close": [1.0]}),
            ]

            payload = resolve_daily_trade_date("2026-04-25")

        self.assertEqual(payload["resolved_trade_date"], "2026-04-17")
        self.assertEqual(payload["status"], "fallback_to_latest_available")
        self.assertFalse(payload["is_exact_match"])

    def test_requested_before_global_latest_must_not_fallback_to_future(self):
        with patch("qsys.ops.trade_date.QlibAdapter") as adapter_cls:
            adapter = adapter_cls.return_value
            adapter.get_last_qlib_date.return_value = pd.Timestamp("2026-04-24")
            self._mock_calendar(adapter, ["2026-04-17", "2026-04-24"])
            adapter.get_features.side_effect = [
                pd.DataFrame(columns=["$close"]),
                pd.DataFrame({"$close": [1.0]}),
            ]

            payload = resolve_daily_trade_date("2026-04-20")

        self.assertEqual(payload["resolved_trade_date"], "2026-04-17")
        self.assertLessEqual(payload["resolved_trade_date"], payload["requested_date"])
        self.assertNotEqual(payload["resolved_trade_date"], "2026-04-24")

    def test_requested_before_earliest_available_date_should_fail(self):
        with patch("qsys.ops.trade_date.QlibAdapter") as adapter_cls:
            adapter = adapter_cls.return_value
            adapter.get_last_qlib_date.return_value = pd.Timestamp("2026-04-17")
            self._mock_calendar(adapter, ["2026-04-17"])
            adapter.get_features.return_value = pd.DataFrame(columns=["$close"])

            payload = resolve_daily_trade_date("2026-01-01")

        self.assertEqual(payload["status"], "failed")
        self.assertIsNone(payload["resolved_trade_date"])

    def test_no_available_data(self):
        with patch("qsys.ops.trade_date.QlibAdapter") as adapter_cls:
            adapter = adapter_cls.return_value
            adapter.get_last_qlib_date.return_value = None

            payload = resolve_daily_trade_date("2026-04-25")

        self.assertEqual(payload["status"], "failed")
        self.assertIsNone(payload["resolved_trade_date"])
        self.assertIsNone(payload["last_qlib_date"])

    def test_training_resolver_reason_is_training_specific(self):
        with patch("qsys.ops.trade_date.QlibAdapter") as adapter_cls:
            adapter = adapter_cls.return_value
            adapter.get_last_qlib_date.return_value = pd.Timestamp("2026-04-17")
            self._mock_calendar(adapter, ["2026-04-17"])
            adapter.get_features.side_effect = [
                pd.DataFrame(columns=["$close"]),
                pd.DataFrame({"$close": [1.0]}),
            ]

            payload = resolve_training_end_date("2026-04-25")

        self.assertEqual(payload["status"], "fallback_to_latest_available")
        self.assertIn("train_end", payload["reason"])

    def test_training_resolver_must_not_fallback_to_future(self):
        with patch("qsys.ops.trade_date.QlibAdapter") as adapter_cls:
            adapter = adapter_cls.return_value
            adapter.get_last_qlib_date.return_value = pd.Timestamp("2026-04-24")
            self._mock_calendar(adapter, ["2026-04-17", "2026-04-24"])
            adapter.get_features.side_effect = [
                pd.DataFrame(columns=["$close"]),
                pd.DataFrame({"$close": [1.0]}),
            ]

            payload = resolve_training_end_date("2026-04-20")

        self.assertEqual(payload["resolved_trade_date"], "2026-04-17")
        self.assertLessEqual(payload["resolved_trade_date"], payload["requested_date"])

    def test_weekly_runner_uses_resolved_train_end(self):
        resolved = {
            "requested_date": "2026-04-25",
            "resolved_trade_date": "2026-04-17",
            "last_qlib_date": "2026-04-17",
            "status": "fallback_to_latest_available",
            "reason": "requested train_end has no qlib feature rows; using latest available trading date",
            "is_exact_match": False,
        }
        artifacts = type("TrainingArtifacts", (), {
            "mainline_object_name": "feature_173",
            "bundle_id": "bundle_feature_173",
            "model_name": "qlib_lgbm_extended",
            "model_path": "/tmp/model",
            "config_snapshot_path": "/tmp/model/config_snapshot.json",
            "training_summary_path": "/tmp/model/training_summary.json",
            "decisions_path": "/tmp/model/decisions.json",
            "training_report_path": None,
            "trained_at": "2026-04-25T09:08:07",
            "train_run_id": "shadow_retrain_2026-04-25_090807",
            "command": ["python", "scripts/run_train.py", "--end", "2026-04-17"],
        })()
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            model_dir = base_dir / "data" / "models" / "qlib_lgbm_extended"
            model_dir.mkdir(parents=True)
            for name in ["config_snapshot.json", "training_summary.json", "decisions.json", "meta.yaml", "model.pkl"]:
                (model_dir / name).write_text("{}\n", encoding="utf-8")
            report_dir = base_dir / "experiments" / "reports"
            report_dir.mkdir(parents=True)
            (report_dir / "train_success.json").write_text("{}\n", encoding="utf-8")
            artifacts.model_path = str(model_dir)
            artifacts.config_snapshot_path = str(model_dir / "config_snapshot.json")
            artifacts.training_summary_path = str(model_dir / "training_summary.json")
            artifacts.decisions_path = str(model_dir / "decisions.json")

            with patch("scripts.ops.run_shadow_retrain_weekly.resolve_training_end_date", return_value=resolved), patch(
                "scripts.ops.run_shadow_retrain_weekly.run_weekly_shadow_training", return_value=artifacts
            ) as training_mock:
                result = run_shadow_retrain_weekly(base_dir, run_id="shadow_retrain_2026-04-25_090807", triggered_by="test")

            summary = load_json(Path(result["run_dir"]) / "daily_summary.json")
            manifest = load_json(Path(result["run_dir"]) / "manifest.json")
            self.assertEqual(training_mock.call_args.kwargs["extra_args"], ["--end", "2026-04-17", "--infer_date", "2026-04-17"])
            self.assertEqual(summary["requested_date"], "2026-04-25")
            self.assertEqual(summary["trade_date"], "2026-04-17")
            self.assertEqual(summary["date_resolution_status"], "fallback_to_latest_available")
            self.assertEqual(manifest["date_resolution"]["resolved_trade_date"], "2026-04-17")


if __name__ == "__main__":
    unittest.main()
