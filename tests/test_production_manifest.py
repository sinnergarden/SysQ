import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml


class TestProductionManifest(unittest.TestCase):
    def test_resolve_production_model_from_manifest(self):
        from qsys.live.scheduler import ModelScheduler

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            models_dir = root / "models"
            models_dir.mkdir(parents=True, exist_ok=True)

            model_dir = models_dir / "qlib_lgbm_test"
            model_dir.mkdir(parents=True, exist_ok=True)
            (model_dir / "model.pkl").touch()

            manifest_path = models_dir / "production_manifest.yaml"
            manifest = {
                "model_path": str(model_dir),
                "version": "test-v1",
                "status": "active",
                "note": "Test manifest",
            }
            manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")

            with patch("qsys.live.scheduler.cfg") as mock_cfg:
                mock_cfg.get_path.return_value = root
                resolved = ModelScheduler.resolve_production_model()

            self.assertEqual(resolved, str(model_dir))

    def test_resolve_production_model_fallback(self):
        from qsys.live.scheduler import ModelScheduler

        with patch.object(ModelScheduler, "find_latest_model", return_value=Path("data/models/qlib_lgbm_phase123")):
            with patch("qsys.live.scheduler.cfg") as mock_cfg:
                mock_cfg.get_path.return_value = Path("/tmp/nonexistent")
                resolved = ModelScheduler.resolve_production_model()

        self.assertEqual(resolved, "data/models/qlib_lgbm_phase123")

    def test_manifest_file_structure(self):
        manifest_path = Path(__file__).resolve().parent.parent / "data" / "models" / "production_manifest.yaml"
        self.assertTrue(manifest_path.exists())

        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        self.assertIn("model_path", manifest)
        self.assertIn("version", manifest)
        self.assertIn("status", manifest)


class TestStrictEvaluator(unittest.TestCase):
    def test_evaluator_import(self):
        from qsys.evaluation import EvaluationReport, StrictEvaluator

        self.assertIsNotNone(StrictEvaluator)
        self.assertIsNotNone(EvaluationReport)

    def test_evaluator_default_values(self):
        from qsys.evaluation import DEFAULT_MAIN_START, DEFAULT_TOP_K, StrictEvaluator

        evaluator = StrictEvaluator()
        self.assertEqual(evaluator.top_k, DEFAULT_TOP_K)
        self.assertEqual(evaluator.main_window_start, DEFAULT_MAIN_START)

    def test_evaluation_report_to_dataframe(self):
        from qsys.evaluation import EvaluationReport, EvaluationResult, ModelMetrics

        metrics = ModelMetrics(
            total_return=0.15,
            annual_return=0.20,
            sharpe=1.5,
            max_drawdown=-0.08,
            trade_count=100,
        )
        result = EvaluationResult(
            period="test",
            model_name="Baseline",
            model_path="/test/path",
            start_date="2025-01-01",
            end_date="2025-12-31",
            top_k=5,
            metrics=metrics,
        )

        report = EvaluationReport(results=[result])
        df = report.to_dataframe()
        self.assertFalse(df.empty)
        self.assertIn("Model", df.columns)
        self.assertIn("Sharpe", df.columns)


if __name__ == "__main__":
    unittest.main()
