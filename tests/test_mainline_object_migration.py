from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from click.testing import CliRunner

from qsys.feature.library import FeatureLibrary
from qsys.research import (
    MAINLINE_OBJECTS,
    get_mainline_spec_by_bundle_id,
    get_mainline_spec_by_feature_set,
    mainline_object_summary,
    resolve_mainline_feature_config,
    resolve_mainline_object_name,
)
from scripts.run_backtest import build_backtest_lineage
from scripts.run_strict_eval import build_lineage_payload
from scripts.run_train import main as run_train_main, resolve_training_input


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
        }

    def fit(self, codes, start, end, *, infer_date=None, label_horizon=5):
        return None

    def save(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        (path / "meta.yaml").write_text("saved: true\n", encoding="utf-8")


class _FakeReport:
    def __init__(self):
        self.artifacts = {}

    def to_markdown(self) -> str:
        return "ok"


class TestMainlineObjectMigration(unittest.TestCase):
    def test_mainline_object_resolution_is_stable(self):
        expected = {
            "feature_173": ("bundle_feature_173", "extended"),
            "feature_254": ("bundle_feature_254", "semantic_all_features"),
            "feature_254_absnorm": ("bundle_feature_254_absnorm", "semantic_all_features_absnorm"),
        }
        for mainline_object_name, (bundle_id, feature_set) in expected.items():
            spec = MAINLINE_OBJECTS[mainline_object_name]
            self.assertEqual(spec.bundle_id, bundle_id)
            self.assertEqual(spec.legacy_feature_set_alias, feature_set)
            self.assertEqual(resolve_mainline_object_name(bundle_id=bundle_id), mainline_object_name)
            self.assertEqual(resolve_mainline_object_name(feature_set=feature_set), mainline_object_name)
            self.assertEqual(get_mainline_spec_by_bundle_id(bundle_id).mainline_object_name, mainline_object_name)
            self.assertEqual(get_mainline_spec_by_feature_set(feature_set).mainline_object_name, mainline_object_name)

    def test_alias_mapping_matches_compat_feature_configs(self):
        self.assertEqual(resolve_mainline_feature_config("feature_173"), FeatureLibrary.get_alpha158_extended_config())
        self.assertEqual(resolve_mainline_feature_config("feature_254"), FeatureLibrary.get_semantic_all_features_config())
        self.assertEqual(resolve_mainline_feature_config("feature_254_absnorm"), FeatureLibrary.get_semantic_all_features_absnorm_config())

    def test_mainline_summary_lists_all_objects(self):
        rows = mainline_object_summary()
        names = {row["mainline_object_name"] for row in rows}
        self.assertEqual(names, {"feature_173", "feature_254", "feature_254_absnorm"})

    def test_train_bundle_resolution_uses_mainline_object_mapping(self):
        payload = resolve_training_input(feature_set=None, bundle_id="bundle_feature_254")
        self.assertEqual(payload["mainline_object_name"], "feature_254")
        self.assertEqual(payload["legacy_feature_set_alias"], "semantic_all_features")
        self.assertEqual(payload["bundle_id"], "bundle_feature_254")
        self.assertEqual(payload["factor_variants"], ["feature_254@raw"])
        self.assertEqual(payload["feature_config"], FeatureLibrary.get_semantic_all_features_config())

    def test_train_legacy_feature_set_still_records_mainline_mapping(self):
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
            self.assertEqual(snapshot["mainline_object_name"], "feature_173")
            self.assertEqual(snapshot["legacy_feature_set_alias"], "extended")
            self.assertEqual(snapshot["bundle_id"], "bundle_feature_173")

    def test_backtest_and_strict_eval_lineage_become_mainline_aware(self):
        snapshot = {
            "input_mode": "bundle_id",
            "bundle_id": "bundle_feature_254_absnorm",
            "feature_set": "semantic_all_features_absnorm",
            "legacy_feature_set_alias": "semantic_all_features_absnorm",
            "factor_variants": ["feature_254_absnorm@raw"],
        }
        backtest_lineage = build_backtest_lineage(snapshot)
        strict_lineage = build_lineage_payload(snapshot)
        self.assertEqual(backtest_lineage["mainline_object_name"], "feature_254_absnorm")
        self.assertEqual(strict_lineage["mainline_object_name"], "feature_254_absnorm")
        self.assertEqual(strict_lineage["legacy_feature_set_alias"], "semantic_all_features_absnorm")


if __name__ == "__main__":
    unittest.main()
