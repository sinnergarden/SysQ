import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qsys.ops import build_latest_shadow_model_payload, write_latest_shadow_model
from qsys.ops.notification import _sanitize_notification_text, send_shadow_run_notification, send_wecom_webhook_message
from qsys.ops.state import load_json
from qsys.ops.training import TrainingArtifacts, TrainingInvocationError
from scripts.ops.run_shadow_daily import run_shadow_daily
from scripts.ops.run_shadow_retrain_weekly import run_shadow_retrain_weekly


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


def _fake_data_status() -> dict:
    return {
        "trade_date": "2026-04-25",
        "status": "success",
        "mode": "freshness_check_only",
        "lightweight_check_only": True,
        "mainline_object_name": "feature_173",
        "health_report": {"blocking_issues": []},
    }


def _fake_feature_status() -> dict:
    return {
        "trade_date": "2026-04-25",
        "status": "success",
        "mode": "readiness_check_only",
        "lightweight_check_only": True,
        "mainline_object_name": "feature_173",
        "degradation_level": "core_ok",
        "notes": ["lightweight_check_only"],
    }


def _fake_inference(*, trade_date, model_payload, output_dir, universe):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = output_dir / "predictions.csv"
    predictions_path.write_text(
        "trade_date,instrument,score,model_name,mainline_object_name,bundle_id,train_run_id\n"
        f"{trade_date},SH600000,0.9,{model_payload['model_name']},{model_payload['mainline_object_name']},{model_payload['bundle_id']},{model_payload['train_run_id']}\n",
        encoding="utf-8",
    )
    _write_json(output_dir / "inference_summary.json", {"trade_date": trade_date, "status": "success"})
    return type(
        "InferenceArtifacts",
        (),
        {
            "predictions_path": str(predictions_path),
            "inference_summary_path": str(output_dir / "inference_summary.json"),
            "prediction_count": 1,
        },
    )()


def _fake_rebalance(*, base_dir, run_id, trade_date, predictions_path, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "target_weights.csv").write_text(
        "trade_date,instrument,score,target_weight,model_name,mainline_object_name,strategy_variant\n"
        f"{trade_date},SH600000,0.9,1.0,qlib_lgbm_extended,feature_173,top5_equal_weight\n",
        encoding="utf-8",
    )
    (output_dir / "order_intents.csv").write_text(
        "trade_date,instrument,side,target_weight,current_weight,target_value,current_value,diff_value,requested_qty,reason\n"
        f"{trade_date},SH600000,buy,1.0,0.0,1000000.0,0.0,1000000.0,100000,rebalance_to_target_weight\n",
        encoding="utf-8",
    )
    (output_dir / "positions_after.csv").write_text(
        "instrument,quantity,sellable_quantity,cost_price,last_price,market_value\n"
        "SH600000,100000,100000,10.0,10.0,1000000.0\n",
        encoding="utf-8",
    )
    _write_json(
        output_dir / "execution_summary.json",
        {
            "trade_date": trade_date,
            "run_id": run_id,
            "status": "success",
            "strategy_variant": "top5_equal_weight",
            "price_mode": "shadow_mark_price",
            "order_count": 1,
            "filled_count": 1,
            "rejected_count": 0,
            "turnover": 1000000.0,
            "cash_after": 0.0,
            "total_value_after": 1000000.0,
        },
    )
    _write_json(output_dir / "account_after.json", {"trade_date": trade_date, "cash": 0.0, "total_value": 1000000.0})
    shadow_dir = Path(base_dir) / "shadow"
    shadow_dir.mkdir(parents=True, exist_ok=True)
    _write_json(shadow_dir / "account.json", {"trade_date": trade_date, "cash": 0.0, "total_value": 1000000.0})
    (shadow_dir / "positions.csv").write_text(
        "instrument,quantity,sellable_quantity,cost_price,last_price,market_value\n"
        "SH600000,100000,100000,10.0,10.0,1000000.0\n",
        encoding="utf-8",
    )
    (shadow_dir / "ledger.csv").write_text(
        "run_id,trade_date,instrument,side,quantity,price,amount,fee,status,reason\n"
        f"{run_id},{trade_date},SH600000,buy,100000,10.0,1000000.0,0.0,filled,rebalance_to_target_weight\n",
        encoding="utf-8",
    )
    return type(
        "ShadowRebalanceArtifacts",
        (),
        {
            "execution_summary_path": str(output_dir / "execution_summary.json"),
            "target_weights_path": str(output_dir / "target_weights.csv"),
            "order_intents_path": str(output_dir / "order_intents.csv"),
            "account_after_path": str(output_dir / "account_after.json"),
            "positions_after_path": str(output_dir / "positions_after.csv"),
            "shadow_account_path": str(shadow_dir / "account.json"),
            "shadow_positions_path": str(shadow_dir / "positions.csv"),
            "shadow_ledger_path": str(shadow_dir / "ledger.csv"),
            "order_count": 1,
            "filled_count": 1,
            "rejected_count": 0,
            "turnover": 1000000.0,
            "cash_after": 0.0,
            "total_value_after": 1000000.0,
        },
    )()


class _FakeResponse:
    def __init__(self, status_code=200, text='{"errcode":0}'):  # noqa: B008
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class TestShadowOpsNotification(unittest.TestCase):
    def test_send_wecom_webhook_message_skips_when_config_missing(self):
        with patch.object(__import__("qsys.ops.notification", fromlist=["cfg"]).cfg, "get") as cfg_get:
            cfg_get.side_effect = lambda key, default=None: {} if key in {"ops", "notification"} else default
            result = send_wecom_webhook_message("title", "content")
        self.assertEqual(result["status"], "skipped")
        self.assertFalse(result["webhook_configured"])
        self.assertEqual(result["error"], "webhook not configured")

    def test_sanitize_notification_text_masks_webhook_url_and_key(self):
        raw = "boom https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=SECRET and key=SECRET"
        sanitized = _sanitize_notification_text(raw)
        self.assertNotIn("SECRET", sanitized)
        self.assertNotIn("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=SECRET", sanitized)
        self.assertIn("key=***", sanitized)

    def test_send_wecom_webhook_message_success_uses_markdown_payload(self):
        with patch.object(__import__("qsys.ops.notification", fromlist=["cfg"]).cfg, "get") as cfg_get, patch(
            "qsys.ops.notification.requests.post", return_value=_FakeResponse(200, '{"errcode":0,"errmsg":"ok"}')
        ) as post_mock:
            cfg_get.side_effect = lambda key, default=None: {"notification": {"wecom_webhook_url": "https://example.invalid/hook"}} if key == "ops" else default
            result = send_wecom_webhook_message("Qsys Shadow Daily: SUCCESS", "trade_date: 2026-04-25")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["http_status"], 200)
        kwargs = post_mock.call_args.kwargs
        self.assertEqual(kwargs["json"]["msgtype"], "markdown")
        self.assertIn("Qsys Shadow Daily: SUCCESS", kwargs["json"]["markdown"]["content"])
        self.assertNotIn("example.invalid", json.dumps(result))

    def test_send_wecom_webhook_message_nonzero_errcode_is_failed(self):
        with patch.object(__import__("qsys.ops.notification", fromlist=["cfg"]).cfg, "get") as cfg_get, patch(
            "qsys.ops.notification.requests.post",
            return_value=_FakeResponse(200, '{"errcode":93000,"errmsg":"invalid webhook https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=SECRET"}'),
        ):
            cfg_get.side_effect = lambda key, default=None: {"notification": {"wecom_webhook_url": "https://example.invalid/hook"}} if key == "ops" else default
            result = send_wecom_webhook_message("title", "content")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["http_status"], 200)
        self.assertIn("wecom errcode=93000", result["error"])
        self.assertNotIn("SECRET", result["error"])
        self.assertNotIn("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=SECRET", result["error"])
        self.assertNotIn("SECRET", result.get("response_text", ""))

    def test_send_wecom_webhook_message_failure_does_not_raise_and_sanitizes_error(self):
        error_text = "network boom https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=SECRET"
        with patch.object(__import__("qsys.ops.notification", fromlist=["cfg"]).cfg, "get") as cfg_get, patch(
            "qsys.ops.notification.requests.post", side_effect=RuntimeError(error_text)
        ):
            cfg_get.side_effect = lambda key, default=None: {"notification": {"wecom_webhook_url": "https://example.invalid/hook"}} if key == "ops" else default
            result = send_wecom_webhook_message("title", "content")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["channel"], "wecom_webhook")
        self.assertNotIn("SECRET", result["error"])
        self.assertNotIn("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=SECRET", result["error"])

    def test_send_shadow_run_notification_builds_daily_message(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "runs" / "2026-04-25" / "shadow_2026-04-25_090807"
            summary_path = _write_json(
                run_dir / "daily_summary.json",
                {
                    "trade_date": "2026-04-25",
                    "run_id": "shadow_2026-04-25_090807",
                    "run_type": "shadow_daily",
                    "decision_status": "shadow_rebalanced",
                    "shadow_order_count": 5,
                    "filled_count": 5,
                    "rejected_count": 0,
                    "turnover": 123456.78,
                    "total_value_after": 1001234.56,
                    "model_used": {"model_name": "qlib_lgbm_extended"},
                },
            )
            manifest_path = _write_json(
                run_dir / "manifest.json",
                {
                    "run_type": "shadow_daily",
                    "overall_status": "success",
                    "stage_status": {"shadow_rebalance": {"status": "success"}},
                },
            )
            with patch("qsys.ops.notification.send_wecom_webhook_message", return_value={"status": "success", "channel": "wecom_webhook", "webhook_configured": True, "message": "ok", "error": None}) as send_mock:
                result = send_shadow_run_notification(summary_path, manifest_path)
        self.assertEqual(result["status"], "success")
        self.assertIn("Qsys Shadow Daily: SUCCESS", send_mock.call_args.args[0])
        self.assertIn("summary: runs/2026-04-25/shadow_2026-04-25_090807/daily_summary.json", send_mock.call_args.args[1])

    def test_daily_runner_writes_notification_result_on_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _make_usable_latest_model(base_dir)
            with patch("scripts.ops.run_shadow_daily._build_data_status", return_value=_fake_data_status()), patch(
                "scripts.ops.run_shadow_daily._build_feature_status", return_value=_fake_feature_status()
            ), patch("scripts.ops.run_shadow_daily.run_shadow_daily_inference", side_effect=_fake_inference), patch(
                "scripts.ops.run_shadow_daily.run_shadow_rebalance", side_effect=_fake_rebalance
            ), patch(
                "scripts.ops.run_shadow_daily.send_shadow_run_notification",
                return_value={"status": "success", "channel": "wecom_webhook", "webhook_configured": True, "message": "ok", "error": None},
            ):
                result = run_shadow_daily(base_dir, run_id="shadow_2026-04-25_090807", triggered_by="test")
                run_dir = Path(result["run_dir"])
                summary = load_json(run_dir / "daily_summary.json")
                manifest = load_json(run_dir / "manifest.json")
                notification = load_json(run_dir / "06_notification" / "notification_result.json")
                self.assertEqual(summary["notification_status"], "success")
                self.assertEqual(notification["status"], "success")
                self.assertEqual(manifest["stage_status"]["archive_report"]["artifact_pointers"]["notification_result_path"], str(run_dir / "06_notification" / "notification_result.json"))
                self.assertEqual(result["overall_status"], "success")

    def test_daily_runner_writes_notification_result_on_failed_daily_without_polluting_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("scripts.ops.run_shadow_daily._build_data_status", return_value=_fake_data_status()), patch(
                "scripts.ops.run_shadow_daily._build_feature_status", return_value=_fake_feature_status()
            ), patch(
                "scripts.ops.run_shadow_daily.send_shadow_run_notification",
                return_value={"status": "failed", "channel": "wecom_webhook", "webhook_configured": True, "message": "request failed", "error": "boom"},
            ):
                result = run_shadow_daily(tmpdir, run_id="shadow_2026-04-25_090807", triggered_by="test")
                run_dir = Path(result["run_dir"])
                summary = load_json(run_dir / "daily_summary.json")
                notification = load_json(run_dir / "06_notification" / "notification_result.json")
                self.assertEqual(result["overall_status"], "failed")
                self.assertEqual(summary["notification_status"], "failed")
                self.assertEqual(notification["error"], "boom")

    def test_daily_runner_notification_wecom_errcode_failed_does_not_pollute_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("scripts.ops.run_shadow_daily._build_data_status", return_value=_fake_data_status()), patch(
                "scripts.ops.run_shadow_daily._build_feature_status", return_value=_fake_feature_status()
            ), patch(
                "scripts.ops.run_shadow_daily.send_shadow_run_notification",
                return_value={
                    "status": "failed",
                    "channel": "wecom_webhook",
                    "webhook_configured": True,
                    "message": "wecom webhook returned non-zero errcode",
                    "error": "wecom errcode=93000, errmsg=invalid webhook",
                    "http_status": 200,
                },
            ):
                result = run_shadow_daily(tmpdir, run_id="shadow_2026-04-25_090807", triggered_by="test")
                run_dir = Path(result["run_dir"])
                summary = load_json(run_dir / "daily_summary.json")
                notification = load_json(run_dir / "06_notification" / "notification_result.json")
                self.assertEqual(result["overall_status"], "failed")
                self.assertEqual(summary["notification_status"], "failed")
                self.assertEqual(notification["http_status"], 200)
                self.assertIn("wecom errcode=93000", notification["error"])

    def test_weekly_runner_writes_notification_result_for_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            artifacts = TrainingArtifacts(
                mainline_object_name="feature_173",
                bundle_id="bundle_feature_173",
                model_name="qlib_lgbm_extended",
                model_path=str(Path(tmpdir) / "data" / "models" / "qlib_lgbm_extended"),
                config_snapshot_path=str(Path(tmpdir) / "data" / "models" / "qlib_lgbm_extended" / "config_snapshot.json"),
                training_summary_path=str(Path(tmpdir) / "data" / "models" / "qlib_lgbm_extended" / "training_summary.json"),
                decisions_path=str(Path(tmpdir) / "data" / "models" / "qlib_lgbm_extended" / "decisions.json"),
                training_report_path=str(Path(tmpdir) / "runs" / "2026-04-25" / "shadow_retrain_2026-04-25_090807" / "training_report.json"),
                trained_at="2026-04-25T09:08:07",
                train_run_id="shadow_retrain_2026-04-25_090807",
                command=["python", "scripts/run_train.py"],
            )
            model_dir = Path(artifacts.model_path)
            model_dir.mkdir(parents=True)
            for name in ["training_summary.json", "config_snapshot.json", "decisions.json", "meta.yaml", "model.pkl"]:
                (model_dir / name).write_text("{}\n", encoding="utf-8")
            Path(artifacts.training_report_path).parent.mkdir(parents=True, exist_ok=True)
            Path(artifacts.training_report_path).write_text("{}\n", encoding="utf-8")
            with patch("scripts.ops.run_shadow_retrain_weekly.run_weekly_shadow_training", return_value=artifacts), patch(
                "scripts.ops.run_shadow_retrain_weekly.send_shadow_run_notification",
                return_value={"status": "success", "channel": "wecom_webhook", "webhook_configured": True, "message": "ok", "error": None},
            ):
                result = run_shadow_retrain_weekly(tmpdir, run_id="shadow_retrain_2026-04-25_090807", triggered_by="test")
                run_dir = Path(result["run_dir"])
                summary = load_json(run_dir / "daily_summary.json")
                notification = load_json(run_dir / "06_notification" / "notification_result.json")
                self.assertEqual(summary["notification_status"], "success")
                self.assertEqual(notification["status"], "success")
                self.assertEqual(result["overall_status"], "success")

    def test_weekly_runner_notification_failure_does_not_change_fallback_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _make_usable_latest_model(base_dir)
            exc = TrainingInvocationError(
                "train failed",
                command=["python", "scripts/run_train.py"],
                returncode=1,
                stdout_tail="",
                stderr_tail="boom",
            )
            with patch("scripts.ops.run_shadow_retrain_weekly.run_weekly_shadow_training", side_effect=exc), patch(
                "scripts.ops.run_shadow_retrain_weekly.send_shadow_run_notification",
                return_value={"status": "failed", "channel": "wecom_webhook", "webhook_configured": True, "message": "request failed", "error": "boom"},
            ):
                result = run_shadow_retrain_weekly(base_dir, run_id="shadow_retrain_2026-04-25_090807", triggered_by="test")
                run_dir = Path(result["run_dir"])
                summary = load_json(run_dir / "daily_summary.json")
                notification = load_json(run_dir / "06_notification" / "notification_result.json")
                self.assertEqual(result["overall_status"], "fallback")
                self.assertEqual(summary["notification_status"], "failed")
                self.assertEqual(notification["error"], "boom")
