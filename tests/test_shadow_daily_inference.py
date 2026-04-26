import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qsys.ops import build_latest_shadow_model_payload, write_latest_shadow_model
import pandas as pd

from qsys.ops.inference import InferenceInvocationError, _build_prediction_frame, run_shadow_daily_inference
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
    def test_no_hidden_top3_truncation(self):
        index = pd.MultiIndex.from_tuples(
            [(f"000{i:03d}.SZ", pd.Timestamp("2026-04-17")) for i in range(100)],
            names=["instrument", "datetime"],
        )
        scores = pd.Series(range(100), index=index)
        frame = _build_prediction_frame(
            scores=scores,
            trade_date="2026-04-17",
            model_payload={
                "model_name": "qlib_lgbm_extended",
                "mainline_object_name": "feature_173",
                "bundle_id": "bundle_feature_173",
                "train_run_id": "shadow_retrain_x",
            },
        )
        self.assertEqual(len(frame), 100)

    def test_inference_requests_full_universe_not_sample_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            model_payload = _make_usable_latest_model(base_dir)
            feature_frame = pd.DataFrame(
                {"$close": list(range(100))},
                index=pd.MultiIndex.from_tuples(
                    [(f"000{i:03d}.SZ", pd.Timestamp("2026-04-17")) for i in range(100)],
                    names=["instrument", "datetime"],
                ),
            )
            with patch("qsys.ops.inference.resolve_mainline_feature_config", return_value=["$close"]), \
                 patch("qsys.ops.inference.QlibAdapter") as adapter_cls, \
                 patch("qsys.ops.inference.SignalGenerator") as generator_cls:
                adapter = adapter_cls.return_value
                adapter.init_qlib.return_value = None
                adapter.get_features.return_value = feature_frame
                generator_cls.return_value.predict.return_value = pd.Series(range(100), index=feature_frame.index)
                artifacts = run_shadow_daily_inference(
                    trade_date="2026-04-17",
                    model_payload=model_payload,
                    output_dir=base_dir / "out",
                    universe="csi300",
                )

            self.assertEqual(adapter.get_features.call_args.args[0], "csi300")
            self.assertEqual(artifacts.prediction_count, 100)

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
            "degradation_level": "core_ok",
            "notes": ["lightweight_check_only"],
        }

    def test_no_model_blocks_inference_and_rebalance(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("scripts.ops.run_shadow_daily.resolve_daily_trade_date", return_value=self.date_resolution), patch(
                "scripts.ops.run_shadow_daily._build_data_status", return_value=self.data_status
            ), patch("scripts.ops.run_shadow_daily._build_feature_status", return_value=self.feature_status):
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

    def test_data_sync_hard_gate_blocks_low_active_instruments(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            shadow_dir = base_dir / "shadow"
            shadow_dir.mkdir(parents=True, exist_ok=True)
            account_path = _write_json(shadow_dir / "account.json", {"cash": 1.0, "last_run_id": "shadow_prev"})
            ledger_path = shadow_dir / "ledger.csv"
            ledger_path.write_text("run_id,trade_date\n", encoding="utf-8")
            account_before = account_path.read_text(encoding="utf-8")
            ledger_before = ledger_path.read_text(encoding="utf-8")
            data_failed = dict(self.data_status)
            data_failed["status"] = "failed"
            data_failed["active_instruments"] = 3
            data_failed["min_active_instruments"] = 50
            data_failed["instrument_coverage_status"] = "mismatch"
            data_failed["health_report"] = {"blocking_issues": ["instrument_coverage_mismatch: active_instruments=3 < min_active_instruments=50"]}
            with patch("scripts.ops.run_shadow_daily.resolve_daily_trade_date", return_value=self.date_resolution), patch(
                "scripts.ops.run_shadow_daily._build_data_status", return_value=data_failed
            ), patch("scripts.ops.run_shadow_daily._build_feature_status", return_value=self.feature_status), patch(
                "scripts.ops.run_shadow_daily.run_shadow_daily_inference"
            ) as inference_mock, patch(
                "scripts.ops.run_shadow_daily.run_shadow_rebalance"
            ) as rebalance_mock:
                result = run_shadow_daily(base_dir, run_id="shadow_2026-04-25_090807", triggered_by="test")

            run_dir = Path(result["run_dir"])
            summary = load_json(run_dir / "daily_summary.json")
            manifest = load_json(run_dir / "manifest.json")
            self.assertEqual(summary["decision_status"], "blocked_data_sync")
            self.assertEqual(manifest["stage_status"]["inference"]["status"], "skipped")
            self.assertEqual(manifest["stage_status"]["shadow_rebalance"]["status"], "skipped")
            self.assertEqual(account_path.read_text(encoding="utf-8"), account_before)
            self.assertEqual(ledger_path.read_text(encoding="utf-8"), ledger_before)
            inference_mock.assert_not_called()
            rebalance_mock.assert_not_called()

    def test_data_sync_failure_blocks_downstream_and_keeps_shadow_state_untouched(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            shadow_dir = base_dir / "shadow"
            shadow_dir.mkdir(parents=True, exist_ok=True)
            original_account = {
                "trade_date": "2026-04-24",
                "cash": 1000000.0,
                "available_cash": 1000000.0,
                "market_value": 0.0,
                "total_value": 1000000.0,
                "last_run_id": "shadow_2026-04-24_090807",
                "initial_capital": 1000000.0,
            }
            account_path = _write_json(shadow_dir / "account.json", original_account)
            ledger_path = shadow_dir / "ledger.csv"
            ledger_path.write_text("run_id,trade_date,instrument,side,quantity,price,amount,fee,status,reason\n", encoding="utf-8")
            account_before = account_path.read_text(encoding="utf-8")
            ledger_before = ledger_path.read_text(encoding="utf-8")
            data_failed = dict(self.data_status)
            data_failed["status"] = "failed"
            data_failed["health_report"] = {"blocking_issues": ["raw_latest stale"]}
            with patch("scripts.ops.run_shadow_daily.resolve_daily_trade_date", return_value=self.date_resolution), patch(
                "scripts.ops.run_shadow_daily._build_data_status", return_value=data_failed
            ), patch("scripts.ops.run_shadow_daily._build_feature_status", return_value=self.feature_status), patch(
                "scripts.ops.run_shadow_daily.run_shadow_daily_inference"
            ) as inference_mock, patch(
                "scripts.ops.run_shadow_daily.run_shadow_rebalance"
            ) as rebalance_mock:
                result = run_shadow_daily(base_dir, run_id="shadow_2026-04-25_090807", triggered_by="test")

            run_dir = Path(result["run_dir"])
            manifest = load_json(run_dir / "manifest.json")
            summary = load_json(run_dir / "daily_summary.json")
            inference_summary = load_json(run_dir / "04_inference" / "inference_summary.json")
            execution_summary = load_json(run_dir / "05_shadow" / "execution_summary.json")
            self.assertEqual(result["overall_status"], "failed")
            self.assertEqual(manifest["stage_status"]["select_model"]["status"], "skipped")
            self.assertEqual(manifest["stage_status"]["inference"]["status"], "skipped")
            self.assertEqual(manifest["stage_status"]["shadow_rebalance"]["status"], "skipped")
            self.assertEqual(summary["overall_status"], "failed")
            self.assertEqual(summary["decision_status"], "blocked_data_sync")
            self.assertEqual(inference_summary["status"], "failed")
            self.assertEqual(execution_summary["status"], "failed")
            self.assertEqual(account_path.read_text(encoding="utf-8"), account_before)
            self.assertEqual(ledger_path.read_text(encoding="utf-8"), ledger_before)
            inference_mock.assert_not_called()
            rebalance_mock.assert_not_called()

    def test_feature_refresh_failure_blocks_downstream_and_does_not_create_shadow_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            feature_failed = dict(self.feature_status)
            feature_failed["status"] = "failed"
            feature_failed["degradation_level"] = "blocked"
            feature_failed["notes"] = ["feature coverage insufficient"]
            with patch("scripts.ops.run_shadow_daily.resolve_daily_trade_date", return_value=self.date_resolution), patch(
                "scripts.ops.run_shadow_daily._build_data_status", return_value=self.data_status
            ), patch("scripts.ops.run_shadow_daily._build_feature_status", return_value=feature_failed), patch(
                "scripts.ops.run_shadow_daily.run_shadow_daily_inference"
            ) as inference_mock, patch(
                "scripts.ops.run_shadow_daily.run_shadow_rebalance"
            ) as rebalance_mock:
                result = run_shadow_daily(base_dir, run_id="shadow_2026-04-25_090807", triggered_by="test")

            run_dir = Path(result["run_dir"])
            manifest = load_json(run_dir / "manifest.json")
            summary = load_json(run_dir / "daily_summary.json")
            self.assertEqual(manifest["stage_status"]["select_model"]["status"], "skipped")
            self.assertEqual(manifest["stage_status"]["inference"]["status"], "skipped")
            self.assertEqual(manifest["stage_status"]["shadow_rebalance"]["status"], "skipped")
            self.assertEqual(summary["overall_status"], "failed")
            self.assertEqual(summary["decision_status"], "blocked_feature_refresh")
            self.assertEqual(summary["readiness_status"], "blocked")
            self.assertFalse((base_dir / "shadow" / "account.json").exists())
            self.assertFalse((base_dir / "shadow" / "ledger.csv").exists())
            inference_mock.assert_not_called()
            rebalance_mock.assert_not_called()

    def test_feature_refresh_extended_warn_still_allows_rebalance(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _make_usable_latest_model(base_dir)
            feature_warn = dict(self.feature_status)
            feature_warn["status"] = "warn"
            feature_warn["degradation_level"] = "extended_warn"
            with patch("scripts.ops.run_shadow_daily.resolve_daily_trade_date", return_value=self.date_resolution), patch(
                "scripts.ops.run_shadow_daily._build_data_status", return_value=self.data_status
            ), patch("scripts.ops.run_shadow_daily._build_feature_status", return_value=feature_warn), patch(
                "scripts.ops.run_shadow_daily.run_shadow_daily_inference"
            ) as inference_mock, patch(
                "scripts.ops.run_shadow_daily.run_shadow_rebalance"
            ) as rebalance_mock:
                inference_mock.return_value = type("InferenceArtifacts", (), {
                    "predictions_path": str(base_dir / "runs" / "2026-04-25" / "shadow_2026-04-25_090807" / "04_inference" / "predictions.csv"),
                    "inference_summary_path": str(base_dir / "runs" / "2026-04-25" / "shadow_2026-04-25_090807" / "04_inference" / "inference_summary.json"),
                    "prediction_count": 60,
                })()
                pred_path = Path(inference_mock.return_value.predictions_path)
                pred_path.parent.mkdir(parents=True, exist_ok=True)
                pred_path.write_text(
                    "trade_date,instrument,score,model_name,mainline_object_name,bundle_id,train_run_id\n"
                    "2026-04-25,SH600000,0.2,qlib_lgbm_extended,feature_173,bundle_feature_173,shadow_retrain_2026-04-25_090807\n",
                    encoding="utf-8",
                )
                _write_json(Path(inference_mock.return_value.inference_summary_path), {"status": "success"})
                rebalance_mock.return_value = type("RebalanceArtifacts", (), {
                    "execution_summary_path": str(base_dir / "runs" / "2026-04-25" / "shadow_2026-04-25_090807" / "05_shadow" / "execution_summary.json"),
                    "target_weights_path": str(base_dir / "runs" / "2026-04-25" / "shadow_2026-04-25_090807" / "05_shadow" / "target_weights.csv"),
                    "order_intents_path": str(base_dir / "runs" / "2026-04-25" / "shadow_2026-04-25_090807" / "05_shadow" / "order_intents.csv"),
                    "account_after_path": str(base_dir / "runs" / "2026-04-25" / "shadow_2026-04-25_090807" / "05_shadow" / "account_after.json"),
                    "positions_after_path": str(base_dir / "runs" / "2026-04-25" / "shadow_2026-04-25_090807" / "05_shadow" / "positions_after.csv"),
                    "shadow_account_path": str(base_dir / "shadow" / "account.json"),
                    "shadow_positions_path": str(base_dir / "shadow" / "positions.csv"),
                    "shadow_ledger_path": str(base_dir / "shadow" / "ledger.csv"),
                    "rebalance_audit_path": str(base_dir / "runs" / "2026-04-25" / "shadow_2026-04-25_090807" / "05_shadow" / "rebalance_audit.csv"),
                    "order_count": 0,
                    "filled_count": 0,
                    "rejected_count": 0,
                    "turnover": 0.0,
                    "cash_after": 1000000.0,
                    "total_value_after": 1000000.0,
                })()
                Path(rebalance_mock.return_value.execution_summary_path).parent.mkdir(parents=True, exist_ok=True)
                _write_json(Path(rebalance_mock.return_value.execution_summary_path), {"status": "success"})
                result = run_shadow_daily(base_dir, run_id="shadow_2026-04-25_090807", triggered_by="test")

            manifest = load_json(Path(result["run_dir"]) / "manifest.json")
            self.assertEqual(manifest["stage_status"]["feature_refresh"]["status"], "success")
            self.assertEqual(manifest["stage_status"]["shadow_rebalance"]["status"], "success")
            rebalance_mock.assert_called_once()

    def test_daily_runner_uses_resolved_trade_date(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _make_usable_latest_model(base_dir)
            fallback_resolution = {
                "requested_date": "2026-04-25",
                "resolved_trade_date": "2026-04-17",
                "last_qlib_date": "2026-04-17",
                "status": "fallback_to_latest_available",
                "reason": "requested_date has no qlib feature rows; using latest available trading date",
                "is_exact_match": False,
            }
            data_status = dict(self.data_status)
            data_status["trade_date"] = "2026-04-17"
            feature_status = dict(self.feature_status)
            feature_status["trade_date"] = "2026-04-17"
            with patch("scripts.ops.run_shadow_daily.resolve_daily_trade_date", return_value=fallback_resolution), patch(
                "scripts.ops.run_shadow_daily._build_data_status", return_value=data_status
            ), patch("scripts.ops.run_shadow_daily._build_feature_status", return_value=feature_status), patch(
                "scripts.ops.run_shadow_daily.run_shadow_daily_inference"
            ) as inference_mock, patch("scripts.ops.run_shadow_daily.run_shadow_rebalance") as rebalance_mock:
                inference_mock.return_value = type("InferenceArtifacts", (), {
                    "predictions_path": str(base_dir / "runs" / "2026-04-25" / "shadow_2026-04-25_090807" / "04_inference" / "predictions.csv"),
                    "inference_summary_path": str(base_dir / "runs" / "2026-04-25" / "shadow_2026-04-25_090807" / "04_inference" / "inference_summary.json"),
                    "prediction_count": 60,
                })()
                pred_path = Path(inference_mock.return_value.predictions_path)
                pred_path.parent.mkdir(parents=True, exist_ok=True)
                pred_path.write_text(
                    "trade_date,instrument,score,model_name,mainline_object_name,bundle_id,train_run_id\n"
                    "2026-04-17,SH600000,0.2,qlib_lgbm_extended,feature_173,bundle_feature_173,shadow_retrain_2026-04-25_090807\n",
                    encoding="utf-8",
                )
                _write_json(Path(inference_mock.return_value.inference_summary_path), {"status": "success", "trade_date": "2026-04-17"})
                rebalance_mock.return_value = type("RebalanceArtifacts", (), {
                    "execution_summary_path": str(base_dir / "runs" / "2026-04-25" / "shadow_2026-04-25_090807" / "05_shadow" / "execution_summary.json"),
                    "target_weights_path": str(base_dir / "runs" / "2026-04-25" / "shadow_2026-04-25_090807" / "05_shadow" / "target_weights.csv"),
                    "order_intents_path": str(base_dir / "runs" / "2026-04-25" / "shadow_2026-04-25_090807" / "05_shadow" / "order_intents.csv"),
                    "account_after_path": str(base_dir / "runs" / "2026-04-25" / "shadow_2026-04-25_090807" / "05_shadow" / "account_after.json"),
                    "positions_after_path": str(base_dir / "runs" / "2026-04-25" / "shadow_2026-04-25_090807" / "05_shadow" / "positions_after.csv"),
                    "shadow_account_path": str(base_dir / "shadow" / "account.json"),
                    "shadow_positions_path": str(base_dir / "shadow" / "positions.csv"),
                    "shadow_ledger_path": str(base_dir / "shadow" / "ledger.csv"),
                    "rebalance_audit_path": str(base_dir / "runs" / "2026-04-25" / "shadow_2026-04-25_090807" / "05_shadow" / "rebalance_audit.csv"),
                    "order_count": 0,
                    "filled_count": 0,
                    "rejected_count": 0,
                    "turnover": 0.0,
                    "cash_after": 1000000.0,
                    "total_value_after": 1000000.0,
                })()
                Path(rebalance_mock.return_value.execution_summary_path).parent.mkdir(parents=True, exist_ok=True)
                _write_json(Path(rebalance_mock.return_value.execution_summary_path), {"status": "success"})
                result = run_shadow_daily(base_dir, run_id="shadow_2026-04-25_090807", triggered_by="test")

            summary = load_json(Path(result["run_dir"]) / "daily_summary.json")
            manifest = load_json(Path(result["run_dir"]) / "manifest.json")
            self.assertEqual(summary["trade_date"], "2026-04-17")
            self.assertEqual(summary["requested_date"], "2026-04-25")
            self.assertEqual(summary["date_resolution_status"], "fallback_to_latest_available")
            self.assertEqual(manifest["date_resolution"]["resolved_trade_date"], "2026-04-17")
            self.assertEqual(inference_mock.call_args.kwargs["trade_date"], "2026-04-17")
            self.assertEqual(rebalance_mock.call_args.kwargs["trade_date"], "2026-04-17")

    def test_daily_resolver_failed_blocks_downstream(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            shadow_dir = base_dir / "shadow"
            shadow_dir.mkdir(parents=True, exist_ok=True)
            account_path = _write_json(shadow_dir / "account.json", {"cash": 1.0, "last_run_id": "shadow_prev"})
            ledger_path = shadow_dir / "ledger.csv"
            ledger_path.write_text("run_id,trade_date\n", encoding="utf-8")
            account_before = account_path.read_text(encoding="utf-8")
            ledger_before = ledger_path.read_text(encoding="utf-8")
            failed_resolution = {
                "requested_date": "2026-04-25",
                "resolved_trade_date": None,
                "last_qlib_date": None,
                "status": "failed",
                "reason": "no available qlib trading date",
                "is_exact_match": False,
            }
            with patch("scripts.ops.run_shadow_daily.resolve_daily_trade_date", return_value=failed_resolution), patch(
                "scripts.ops.run_shadow_daily._build_feature_status", return_value=self.feature_status
            ), patch("scripts.ops.run_shadow_daily.run_shadow_daily_inference") as inference_mock, patch(
                "scripts.ops.run_shadow_daily.run_shadow_rebalance"
            ) as rebalance_mock:
                result = run_shadow_daily(base_dir, run_id="shadow_2026-04-25_090807", triggered_by="test")

            run_dir = Path(result["run_dir"])
            manifest = load_json(run_dir / "manifest.json")
            summary = load_json(run_dir / "daily_summary.json")
            self.assertEqual(manifest["stage_status"]["data_sync"]["status"], "failed")
            self.assertEqual(manifest["stage_status"]["select_model"]["status"], "skipped")
            self.assertEqual(manifest["stage_status"]["inference"]["status"], "skipped")
            self.assertEqual(manifest["stage_status"]["shadow_rebalance"]["status"], "skipped")
            self.assertEqual(summary["date_resolution_status"], "failed")
            self.assertEqual(account_path.read_text(encoding="utf-8"), account_before)
            self.assertEqual(ledger_path.read_text(encoding="utf-8"), ledger_before)
            inference_mock.assert_not_called()
            rebalance_mock.assert_not_called()

    def test_inference_failure_does_not_trigger_rebalance(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _make_usable_latest_model(base_dir)
            with patch("scripts.ops.run_shadow_daily.resolve_daily_trade_date", return_value=self.date_resolution), patch(
                "scripts.ops.run_shadow_daily._build_data_status", return_value=self.data_status
            ), patch("scripts.ops.run_shadow_daily._build_feature_status", return_value=self.feature_status), patch(
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
                    "rebalance_audit_path": str(output_dir / "rebalance_audit.csv"),
                    "order_count": 0,
                    "filled_count": 0,
                    "rejected_count": 0,
                    "turnover": 0.0,
                })()

            with patch("scripts.ops.run_shadow_daily.resolve_daily_trade_date", return_value=self.date_resolution), patch(
                "scripts.ops.run_shadow_daily._build_data_status", return_value=self.data_status
            ), patch("scripts.ops.run_shadow_daily._build_feature_status", return_value=self.feature_status), patch(
                "scripts.ops.run_shadow_daily.run_shadow_daily_inference"
            ) as inference_mock, patch(
                "scripts.ops.run_shadow_daily.run_shadow_rebalance", side_effect=fake_rebalance
            ):
                inference_mock.return_value = type("InferenceArtifacts", (), {
                    "predictions_path": str(base_dir / "runs" / "2026-04-25" / "shadow_2026-04-25_090807" / "04_inference" / "predictions.csv"),
                    "inference_summary_path": str(base_dir / "runs" / "2026-04-25" / "shadow_2026-04-25_090807" / "04_inference" / "inference_summary.json"),
                    "prediction_count": 60,
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
