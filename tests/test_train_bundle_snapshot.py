from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from qsys.research.manifest import FactorManifestRegistry
from qsys.research.schemas import FactorBundle, FactorDefinition
from scripts.run_train import build_training_snapshot, main as run_train_main, resolve_training_input


class _FakeHealth:
    def to_markdown(self) -> str:
        return "ok"


class _FakeAdapter:
    def init_qlib(self) -> None:
        return None

    def get_data_status_report(self) -> dict:
        return {
            "raw_latest": "2026-04-18",
            "qlib_latest": "2026-04-18",
            "aligned": True,
        }


class _FakeModel:
    def __init__(self, name, model_config, feature_config):
        self.name = name
        self.model_config = model_config
        self.feature_config = list(feature_config)
        self.training_summary = {
            "training_mode": "qlib_native",
            "train_end_requested": "2025-01-31",
            "train_end_effective": "2025-01-24",
            "infer_date": "2025-01-31",
            "last_train_sample_date": "2025-01-24",
            "max_label_date_used": "2025-01-31",
            "is_label_mature_at_infer_time": True,
            "sample_count": 12,
            "feature_count": len(self.feature_config),
            "mse": 0.1,
            "rank_ic": 0.2,
        }

    def fit(self, codes, start, end, *, infer_date=None, label_horizon=5):
        self.training_summary.update({
            "train_start": start,
            "train_end": end,
            "infer_date": infer_date or end,
            "label_horizon": label_horizon,
        })

    def save(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        (path / "meta.yaml").write_text("saved: true\n", encoding="utf-8")


class _FakeReport:
    def __init__(self):
        self.artifacts = {}

    def to_markdown(self) -> str:
        return "ok"


class TestTrainBundleSnapshot(unittest.TestCase):
    def test_resolve_training_input_supports_bundle_id(self):
        payload = resolve_training_input(feature_set=None, bundle_id="bundle_semantic_demo")
        self.assertEqual(payload["input_mode"], "bundle_id")
        self.assertEqual(payload["bundle_id"], "bundle_semantic_demo")
        self.assertEqual(payload["factor_variants"], ["close@raw", "ret_1d@raw"])
        self.assertEqual(payload["feature_config"], ["close", "ret_1d"])

    def test_resolve_training_input_rejects_unknown_bundle(self):
        with self.assertRaisesRegex(ValueError, "Unknown bundle_id"):
            resolve_training_input(feature_set=None, bundle_id="bundle_does_not_exist")

    def test_resolve_training_input_rejects_bundle_with_missing_variant(self):
        registry = FactorManifestRegistry(
            definitions={
                "close": FactorDefinition(
                    factor_id="close",
                    name="Close",
                    family="price",
                    kind="semantic_feature",
                    dependencies=["market_data.close"],
                    builder="semantic_registry",
                    timing_semantics="t_close_for_t_plus_1",
                    description="demo",
                ),
            },
            variants={},
            bundles={
                "bundle_broken": FactorBundle(
                    bundle_id="bundle_broken",
                    purpose="demo",
                    factor_variants=["close@raw"],
                    intended_usage="demo",
                    change_log=["init"],
                )
            },
        )
        with patch("scripts.run_train.load_factor_registry", return_value=registry):
            with self.assertRaisesRegex(ValueError, "unknown variant_id"):
                resolve_training_input(feature_set=None, bundle_id="bundle_broken")

    def test_build_training_snapshot_round_trip_preserves_bundle_lineage(self):
        payload = build_training_snapshot(
            input_payload={
                "input_mode": "bundle_id",
                "feature_set": None,
                "bundle_id": "bundle_semantic_demo",
                "factor_variants": ["close@raw", "ret_1d@raw"],
                "bundle_source": "research/factors/bundles",
                "bundle_resolution_status": "resolved_via_manifest_compat_layer",
                "object_layer_status": "bundle_manifest_resolved_for_train_v1",
            },
            model_name="qlib_lgbm_bundle_bundle_semantic_demo",
            model_type="qlib_lgbm",
            universe="csi300",
            start="2025-01-01",
            end="2025-01-31",
            infer_date="2025-01-31",
            label_horizon=5,
            training_summary={"train_end_effective": "2025-01-24", "training_mode": "qlib_native"},
            mlflow_root=None,
        )
        restored = json.loads(json.dumps(payload))
        self.assertEqual(restored["bundle_id"], "bundle_semantic_demo")
        self.assertEqual(restored["factor_variants"], ["close@raw", "ret_1d@raw"])
        self.assertEqual(restored["input_mode"], "bundle_id")

    def test_run_train_rejects_feature_set_and_bundle_id_together(self):
        runner = CliRunner()
        result = runner.invoke(
            run_train_main,
            [
                "--model", "qlib_lgbm",
                "--feature_set", "extended",
                "--bundle_id", "bundle_semantic_demo",
            ],
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Provide exactly one of feature_set or bundle_id", result.output)

    def test_run_train_legacy_feature_set_still_writes_legacy_snapshot(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fake_report = _FakeReport()
            with patch("scripts.run_train.assert_qlib_data_ready", return_value=_FakeHealth()), \
                 patch("scripts.run_train.QlibAdapter", _FakeAdapter), \
                 patch("scripts.run_train.cfg.get_path", return_value=root), \
                 patch("scripts.run_train.TrainingReport.generate", return_value=fake_report), \
                 patch("scripts.run_train.TrainingReport.save", return_value=str(root / "report.json")), \
                 patch("qsys.model.zoo.qlib_native.QlibNativeModel", _FakeModel):
                result = runner.invoke(
                    run_train_main,
                    [
                        "--model", "qlib_lgbm",
                        "--feature_set", "extended",
                        "--start", "2025-01-01",
                        "--end", "2025-01-31",
                    ],
                )
            self.assertEqual(result.exit_code, 0, msg=result.output)
            snapshot = json.loads((root / "models" / "qlib_lgbm_extended" / "config_snapshot.json").read_text(encoding="utf-8"))
            self.assertEqual(snapshot["input_mode"], "feature_set")
            self.assertEqual(snapshot["feature_set"], "extended")
            self.assertIsNone(snapshot["bundle_id"])

    def test_run_train_bundle_id_writes_bundle_snapshot(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fake_report = _FakeReport()
            with patch("scripts.run_train.assert_qlib_data_ready", return_value=_FakeHealth()), \
                 patch("scripts.run_train.QlibAdapter", _FakeAdapter), \
                 patch("scripts.run_train.cfg.get_path", return_value=root), \
                 patch("scripts.run_train.TrainingReport.generate", return_value=fake_report), \
                 patch("scripts.run_train.TrainingReport.save", return_value=str(root / "report.json")), \
                 patch("qsys.model.zoo.qlib_native.QlibNativeModel", _FakeModel):
                result = runner.invoke(
                    run_train_main,
                    [
                        "--model", "qlib_lgbm",
                        "--bundle_id", "bundle_semantic_demo",
                        "--start", "2025-01-01",
                        "--end", "2025-01-31",
                    ],
                )
            self.assertEqual(result.exit_code, 0, msg=result.output)
            model_dir = root / "models" / "qlib_lgbm_bundle_bundle_semantic_demo"
            snapshot = json.loads((model_dir / "config_snapshot.json").read_text(encoding="utf-8"))
            self.assertEqual(snapshot["input_mode"], "bundle_id")
            self.assertEqual(snapshot["bundle_id"], "bundle_semantic_demo")
            self.assertEqual(snapshot["factor_variants"], ["close@raw", "ret_1d@raw"])
            self.assertEqual(snapshot["bundle_resolution_status"], "resolved_via_manifest_compat_layer")


if __name__ == "__main__":
    unittest.main()
