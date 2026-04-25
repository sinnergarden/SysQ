import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qsys.ops import build_latest_shadow_model_payload, write_latest_shadow_model
from qsys.ops.inference import InferenceInvocationError
from qsys.ops.state import load_json
from scripts.ops.run_shadow_daily import run_shadow_daily


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _make_usable_latest_model(base_dir: Path) -> dict[str, str]:
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
    return payload


class TestShadowDailyInference(unittest.TestCase):
    def setUp(self):
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
            "degradation_level": "core_ok",
            "notes": ["lightweight_check_only"],
        }

    def test_no_model_blocks_inference_and_rebalance(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("scripts.ops.run_shadow_daily._build_data_status", return_value=self.data_status), patch(
                "scripts.ops.run_shadow_daily._build_feature_status", return_value=self.feature_status
            ):
                result = run_shadow_daily(tmpdir, run_id="shadow_2026-04-25_090807", triggered_by="test")

            run_dir = Path(result["run_dir"])
            manifest = load_json(run_dir / "manifest.json")
            summary = load_json(run_dir / "daily_summary.json")
            execution_summary = load_json(run_dir / "05_shadow" / "execution_summary.json")
            self.assertEqual(result["overall_status"], "failed")
            self.assertEqual(manifest["stage_status"]["select_model"]["status"], "failed")
            self.assertEqual(manifest["stage_status"]["inference"]["status"], "skipped")
            self.assertEqual(manifest["stage_status"]["shadow_rebalance"]["status"], "skipped")
            self.assertEqual(summary["overall_status"], "failed")
            self.assertEqual(summary["decision_status"], "failed")
            self.assertEqual(summary["error"], "no usable latest model")
            self.assertEqual(execution_summary["status"], "failed")
            self.assertFalse((run_dir / "05_shadow" / "order_intents.csv").exists())

    def test_inference_failure_does_not_trigger_rebalance(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _make_usable_latest_model(base_dir)
            with patch("scripts.ops.run_shadow_daily._build_data_status", return_value=self.data_status), patch(
                "scripts.ops.run_shadow_daily._build_feature_status", return_value=self.feature_status
            ), patch(
                "scripts.ops.run_shadow_daily.run_shadow_daily_inference",
                side_effect=InferenceInvocationError("mock inference boom"),
            ), patch("scripts.ops.run_shadow_daily.run_shadow_rebalance") as rebalance_mock:
                result = run_shadow_daily(base_dir, run_id="shadow_2026-04-25_090807", triggered_by="test")

            run_dir = Path(result["run_dir"])
            manifest = load_json(run_dir / "manifest.json")
            summary = load_json(run_dir / "daily_summary.json")
            execution_summary = load_json(run_dir / "05_shadow" / "execution_summary.json")
            self.assertEqual(result["overall_status"], "failed")
            self.assertEqual(manifest["stage_status"]["select_model"]["status"], "success")
            self.assertEqual(manifest["stage_status"]["inference"]["status"], "failed")
            self.assertEqual(manifest["stage_status"]["shadow_rebalance"]["status"], "skipped")
            self.assertEqual(summary["decision_status"], "failed")
            self.assertEqual(summary["error"], "mock inference boom")
            self.assertEqual(execution_summary["status"], "failed")
            rebalance_mock.assert_not_called()

    def test_daily_runner_still_does_not_retrain(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _make_usable_latest_model(base_dir)
            called = {"count": 0}

            def fake_rebalance(**kwargs):
                called["count"] += 1
                output_dir = Path(kwargs["output_dir"])
                _write_json(
                    output_dir / "execution_summary.json",
                    {
                        "trade_date": kwargs["trade_date"],
                        "run_id": kwargs["run_id"],
                        "status": "success",
                        "strategy_variant": "top5_equal_weight",
                        "top_k": 5,
                        "turnover_buffer": 0.0,
                        "price_mode": "shadow_mark_price",
                        "order_count": 0,
                        "buy_count": 0,
                        "sell_count": 0,
                        "skipped_count": 0,
                        "filled_count": 0,
                        "rejected_count": 0,
                        "cash_before": 1000000.0,
                        "cash_after": 1000000.0,
                        "market_value_before": 0.0,
                        "market_value_after": 0.0,
                        "total_value_before": 1000000.0,
                        "total_value_after": 1000000.0,
                        "turnover": 0.0,
                        "notes": [],
                    },
                )
                return type("RebalanceArtifacts", (), {
                    "execution_summary_path": str(output_dir / "execution_summary.json"),
                    "target_weights_path": str(output_dir / "target_weights.csv"),
                    "order_intents_path": str(output_dir / "order_intents.csv"),
                    "account_after_path": str(output_dir / "account_after.json"),
                    "positions_after_path": str(output_dir / "positions_after.csv"),
                    "shadow_account_path": str(base_dir / "shadow" / "account.json"),
                    "shadow_positions_path": str(base_dir / "shadow" / "positions.csv"),
                    "shadow_ledger_path": str(base_dir / "shadow" / "ledger.csv"),
                    "order_count": 0,
                    "filled_count": 0,
                    "rejected_count": 0,
                    "turnover": 0.0,
                })()

            with patch("scripts.ops.run_shadow_daily._build_data_status", return_value=self.data_status), patch(
                "scripts.ops.run_shadow_daily._build_feature_status", return_value=self.feature_status
            ), patch("scripts.ops.run_shadow_daily.run_shadow_daily_inference") as inference_mock, patch(
                "scripts.ops.run_shadow_daily.run_shadow_rebalance", side_effect=fake_rebalance
            ):
                inference_mock.return_value = type("InferenceArtifacts", (), {
                    "predictions_path": str(base_dir / "runs" / "2026-04-25" / "shadow_2026-04-25_090807" / "04_inference" / "predictions.csv"),
                    "inference_summary_path": str(base_dir / "runs" / "2026-04-25" / "shadow_2026-04-25_090807" / "04_inference" / "inference_summary.json"),
                    "prediction_count": 1,
                })()
                pred_path = Path(inference_mock.return_value.predictions_path)
                pred_path.parent.mkdir(parents=True, exist_ok=True)
                pred_path.write_text(
                    "trade_date,instrument,score,model_name,mainline_object_name,bundle_id,train_run_id\n"
                    "2026-04-25,SH600000,0.2,qlib_lgbm_extended,feature_173,bundle_feature_173,shadow_retrain_2026-04-25_090807\n",
                    encoding="utf-8",
                )
                _write_json(Path(inference_mock.return_value.inference_summary_path), {"status": "success"})
                result = run_shadow_daily(base_dir, run_id="shadow_2026-04-25_090807", triggered_by="test")

            self.assertEqual(result["overall_status"], "success")
            self.assertEqual(called["count"], 1)
