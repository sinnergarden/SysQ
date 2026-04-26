import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from qsys.ops import build_latest_shadow_model_payload, write_latest_shadow_model
from qsys.ops.state import load_json
from scripts.ops.run_shadow_daily import _build_feature_status, run_shadow_daily


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _make_usable_latest_model(base_dir: Path) -> None:
    model_dir = base_dir / "data" / "models" / "qlib_lgbm_extended"
    model_dir.mkdir(parents=True)
    for name in ["config_snapshot.json", "training_summary.json", "decisions.json", "meta.yaml", "model.pkl"]:
        (model_dir / name).write_text("{}\n", encoding="utf-8")
    payload = build_latest_shadow_model_payload(
        model_name="qlib_lgbm_extended",
        model_path=str(model_dir),
        mainline_object_name="feature_173",
        bundle_id="bundle_feature_173",
        train_run_id="shadow_retrain_2026-04-25_090807",
        trained_at="2026-04-25T09:08:07",
        status="success",
    )
    write_latest_shadow_model(base_dir, payload)


class TestShadowBusinessGating(unittest.TestCase):
    def setUp(self):
        self.date_resolution = {
            "requested_date": "2026-04-25",
            "resolved_trade_date": "2026-04-25",
            "last_qlib_date": "2026-04-25",
            "status": "success",
            "reason": "requested_date is available in qlib",
            "is_exact_match": True,
        }
        self.data_status = {
            "trade_date": "2026-04-25",
            "status": "success",
            "mode": "freshness_check_only",
            "lightweight_check_only": True,
            "mainline_object_name": "feature_173",
            "health_report": {"blocking_issues": []},
        }
        self.feature_status = {
            "trade_date": "2026-04-25",
            "status": "success",
            "mode": "readiness_check_only",
            "lightweight_check_only": True,
            "mainline_object_name": "feature_173",
            "field_count": 173,
            "usable_field_count": 40,
            "degradation_level": "extended_blocked",
            "notes": ["core_chain_has_blocking_fields"],
        }

    def test_readiness_blocked_blocks_downstream(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _make_usable_latest_model(base_dir)
            with patch("scripts.ops.run_shadow_daily.resolve_daily_trade_date", return_value=self.date_resolution), patch(
                "scripts.ops.run_shadow_daily._build_data_status", return_value=self.data_status
            ), patch("scripts.ops.run_shadow_daily._build_feature_status", return_value=self.feature_status), patch(
                "scripts.ops.run_shadow_daily.run_shadow_daily_inference"
            ) as inference_mock, patch("scripts.ops.run_shadow_daily.run_shadow_rebalance") as rebalance_mock:
                result = run_shadow_daily(base_dir, run_id="shadow_2026-04-25_090807", triggered_by="test")

            summary = load_json(Path(result["run_dir"]) / "daily_summary.json")
            manifest = load_json(Path(result["run_dir"]) / "manifest.json")
            self.assertEqual(summary["decision_status"], "blocked_feature_refresh")
            self.assertEqual(summary["overall_status"], "failed")
            self.assertEqual(summary["readiness_status"], "blocked")
            self.assertEqual(manifest["stage_status"]["feature_refresh"]["status"], "failed")
            self.assertEqual(manifest["stage_status"]["inference"]["status"], "skipped")
            self.assertEqual(manifest["stage_status"]["shadow_rebalance"]["status"], "skipped")
            inference_mock.assert_not_called()
            rebalance_mock.assert_not_called()

    def test_model_input_coverage_prevents_false_readiness_block(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            model_dir = base_dir / "data" / "models" / "qlib_lgbm_extended"
            model_dir.mkdir(parents=True, exist_ok=True)
            feature_frame = pd.DataFrame({"Ref($close, 5)/$close": [None, None]}, index=pd.Index([0, 1]))
            model_input_frame = pd.DataFrame({"Ref($close, 5)/$close": [0.0, 1.0]}, index=pd.Index([0, 1]))
            with patch("qsys.research.readiness.resolve_mainline_feature_config", return_value=["Ref($close, 5)/$close"]), \
                 patch("scripts.ops.run_shadow_daily.resolve_mainline_feature_config", return_value=["Ref($close, 5)/$close"]), \
                 patch("scripts.ops.run_shadow_daily.QlibAdapter") as adapter_cls, \
                 patch("scripts.ops.run_shadow_daily.build_model_input_frame", return_value=model_input_frame):
                adapter = adapter_cls.return_value
                adapter.init_qlib.return_value = None
                adapter.get_features.return_value = feature_frame
                status = _build_feature_status(trade_date="2026-04-17", universe="csi300", mainline_object_name="feature_173")

            self.assertEqual(status["usable_field_count"], 1)
            self.assertEqual(status["degradation_level"], "core_ok")

    def test_low_prediction_coverage_blocks_rebalance_and_preserves_shadow_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _make_usable_latest_model(base_dir)
            shadow_dir = base_dir / "shadow"
            shadow_dir.mkdir(parents=True, exist_ok=True)
            _write_json(shadow_dir / "account.json", {"cash": 100.0, "last_run_id": "prev", "initial_capital": 100.0})
            (shadow_dir / "ledger.csv").write_text("run_id,trade_date,instrument,side,quantity,price,amount,fee,status,reason\n", encoding="utf-8")
            account_before = (shadow_dir / "account.json").read_text(encoding="utf-8")
            ledger_before = (shadow_dir / "ledger.csv").read_text(encoding="utf-8")
            feature_warn = dict(self.feature_status)
            feature_warn["degradation_level"] = "extended_warn"
            feature_warn["usable_field_count"] = 160
            feature_warn["notes"] = []
            inference_artifacts = type("InferenceArtifacts", (), {
                "predictions_path": str(base_dir / "runs" / "2026-04-25" / "shadow_2026-04-25_090807" / "04_inference" / "predictions.csv"),
                "inference_summary_path": str(base_dir / "runs" / "2026-04-25" / "shadow_2026-04-25_090807" / "04_inference" / "inference_summary.json"),
                "prediction_count": 3,
            })()
            pred_path = Path(inference_artifacts.predictions_path)
            pred_path.parent.mkdir(parents=True, exist_ok=True)
            pred_path.write_text(
                "trade_date,instrument,score,model_name,mainline_object_name,bundle_id,train_run_id\n"
                "2026-04-25,000001.SZ,0.3,qlib_lgbm_extended,feature_173,bundle_feature_173,rid\n",
                encoding="utf-8",
            )
            _write_json(Path(inference_artifacts.inference_summary_path), {"status": "success", "prediction_count": 3})
            with patch("scripts.ops.run_shadow_daily.resolve_daily_trade_date", return_value=self.date_resolution), patch(
                "scripts.ops.run_shadow_daily._build_data_status", return_value=self.data_status
            ), patch("scripts.ops.run_shadow_daily._build_feature_status", return_value=feature_warn), patch(
                "scripts.ops.run_shadow_daily.run_shadow_daily_inference", return_value=inference_artifacts
            ), patch("scripts.ops.run_shadow_daily.DEFAULT_MIN_PREDICTION_COUNT", 50), patch(
                "scripts.ops.run_shadow_daily.run_shadow_rebalance"
            ) as rebalance_mock:
                result = run_shadow_daily(base_dir, run_id="shadow_2026-04-25_090807", triggered_by="test")

            run_dir = Path(result["run_dir"])
            summary = load_json(run_dir / "daily_summary.json")
            execution_summary = load_json(run_dir / "05_shadow" / "execution_summary.json")
            self.assertEqual(summary["decision_status"], "blocked_low_prediction_coverage")
            self.assertEqual(summary["overall_status"], "failed")
            self.assertEqual(summary["prediction_count"], 3)
            self.assertEqual(summary["min_prediction_count"], 50)
            self.assertEqual(summary["prediction_coverage_status"], "blocked_low_prediction_coverage")
            self.assertEqual(load_json(run_dir / "manifest.json")["stage_status"]["shadow_rebalance"]["status"], "failed")
            self.assertEqual(execution_summary["error"], "prediction_count below min_prediction_count")
            self.assertEqual(execution_summary["prediction_count"], 3)
            self.assertEqual(execution_summary["min_prediction_count"], 50)
            self.assertEqual((shadow_dir / "account.json").read_text(encoding="utf-8"), account_before)
            self.assertEqual((shadow_dir / "ledger.csv").read_text(encoding="utf-8"), ledger_before)
            rebalance_mock.assert_not_called()
