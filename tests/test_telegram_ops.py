import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qsys.config.manager import _resolve_env_placeholders
from qsys.ops.state import load_json
from qsys.ops.telegram import (
    append_gateway_command_log,
    build_command_log_entry,
    hash_chat_id,
    send_shadow_run_telegram_notification,
    send_telegram_message,
)
from scripts.ops.run_shadow_daily import run_shadow_daily
from scripts.ops.run_shadow_retrain_weekly import run_shadow_retrain_weekly
from scripts.ops.run_telegram_gateway import handle_command


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class _FakeResponse:
    def __init__(self, status_code=200, text='{"ok": true}'):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return json.loads(self.text)


class TestTelegramOps(unittest.TestCase):
    def test_env_placeholder_resolution(self):
        os.environ["QSYS_TEST_ENV_VALUE"] = "hello"
        self.assertEqual(_resolve_env_placeholders("ENV:QSYS_TEST_ENV_VALUE"), "hello")

    def test_send_telegram_message_skips_without_token(self):
        with patch("qsys.ops.telegram._resolve_bot_token", return_value=None), patch(
            "qsys.ops.telegram._resolve_chat_id", return_value="123"
        ):
            result = send_telegram_message("hello")
        self.assertEqual(result["status"], "skipped")
        self.assertFalse(result["configured"])

    def test_send_telegram_message_skips_without_chat_id(self):
        with patch("qsys.ops.telegram._resolve_bot_token", return_value="123:ABC"), patch(
            "qsys.ops.telegram._resolve_chat_id", return_value=None
        ):
            result = send_telegram_message("hello")
        self.assertEqual(result["status"], "skipped")
        self.assertFalse(result["chat_id_configured"])

    def test_send_telegram_message_success(self):
        with patch("qsys.ops.telegram._resolve_bot_token", return_value="123:ABC"), patch(
            "qsys.ops.telegram._resolve_chat_id", return_value="42"
        ), patch("qsys.ops.telegram.requests.post", return_value=_FakeResponse(200, '{"ok": true, "result": {}}')):
            result = send_telegram_message("hello")
        self.assertEqual(result["status"], "success")
        self.assertNotIn("123:ABC", json.dumps(result))

    def test_send_telegram_message_ok_false_failed(self):
        with patch("qsys.ops.telegram._resolve_bot_token", return_value="123:ABC"), patch(
            "qsys.ops.telegram._resolve_chat_id", return_value="42"
        ), patch(
            "qsys.ops.telegram.requests.post",
            return_value=_FakeResponse(200, '{"ok": false, "description": "bad token 123:ABC"}'),
        ):
            result = send_telegram_message("hello")
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("123:ABC", json.dumps(result))

    def test_send_telegram_message_exception_failed(self):
        with patch("qsys.ops.telegram._resolve_bot_token", return_value="123:ABC"), patch(
            "qsys.ops.telegram._resolve_chat_id", return_value="42"
        ), patch("qsys.ops.telegram.requests.post", side_effect=RuntimeError("boom 123:ABC")):
            result = send_telegram_message("hello")
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("123:ABC", result["error"])

    def test_send_shadow_run_telegram_notification_builds_message(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "runs" / "2026-04-25" / "shadow_2026-04-25_090807"
            summary_path = _write_json(run_dir / "daily_summary.json", {"run_type": "shadow_daily", "trade_date": "2026-04-25", "run_id": "r1", "decision_status": "shadow_rebalanced"})
            manifest_path = _write_json(run_dir / "manifest.json", {"overall_status": "success"})
            with patch("qsys.ops.telegram.send_telegram_message", return_value={"status": "success", "channel": "telegram", "configured": True, "chat_id_configured": True, "message": "ok", "error": None}) as send_mock:
                result = send_shadow_run_telegram_notification(summary_path, manifest_path)
        self.assertEqual(result["status"], "success")
        self.assertIn("Qsys shadow_daily", send_mock.call_args.args[0])

    def test_daily_summary_writes_telegram_notification_status(self):
        fake_model = {
            "model_name": "qlib_lgbm_extended",
            "model_path": "/tmp/model",
            "mainline_object_name": "feature_173",
            "bundle_id": "bundle_feature_173",
            "train_run_id": "shadow_retrain_1",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("scripts.ops.run_shadow_daily.resolve_daily_trade_date", return_value={"requested_date": "2026-04-25", "resolved_trade_date": "2026-04-25", "last_qlib_date": "2026-04-25", "status": "success", "reason": "ok", "is_exact_match": True}), patch(
                "scripts.ops.run_shadow_daily._build_data_status", return_value={"trade_date": "2026-04-25", "status": "failed", "health_report": {"blocking_issues": ["boom"]}}
            ), patch("scripts.ops.run_shadow_daily._build_feature_status", return_value={"trade_date": "2026-04-25", "status": "success", "degradation_level": "core_ok"}), patch(
                "scripts.ops.run_shadow_daily.send_shadow_run_notification", return_value={"status": "success"}
            ), patch(
                "scripts.ops.run_shadow_daily.send_shadow_run_telegram_notification", return_value={"status": "failed"}
            ):
                result = run_shadow_daily(tmpdir, run_id="shadow_2026-04-25_090807", triggered_by="test")
            summary = load_json(Path(result["run_dir"]) / "daily_summary.json")
            self.assertEqual(summary["telegram_notification_status"], "failed")
            self.assertEqual(result["overall_status"], "failed")

    def test_weekly_summary_writes_telegram_notification_status(self):
        artifacts = type("TrainingArtifacts", (), {
            "mainline_object_name": "feature_173",
            "bundle_id": "bundle_feature_173",
            "model_name": "qlib_lgbm_extended",
            "model_path": None,
            "config_snapshot_path": None,
            "training_summary_path": None,
            "decisions_path": None,
            "training_report_path": None,
            "trained_at": "2026-04-25T09:08:07",
            "train_run_id": "shadow_retrain_1",
        })()
        with tempfile.TemporaryDirectory() as tmpdir:
            model_dir = Path(tmpdir) / "data" / "models" / "qlib_lgbm_extended"
            model_dir.mkdir(parents=True)
            for name in ["config_snapshot.json", "training_summary.json", "decisions.json", "meta.yaml", "model.pkl"]:
                (model_dir / name).write_text("{}\n", encoding="utf-8")
            artifacts.model_path = str(model_dir)
            artifacts.config_snapshot_path = str(model_dir / "config_snapshot.json")
            artifacts.training_summary_path = str(model_dir / "training_summary.json")
            artifacts.decisions_path = str(model_dir / "decisions.json")
            with patch("scripts.ops.run_shadow_retrain_weekly.resolve_training_end_date", return_value={"requested_date": "2026-04-25", "resolved_trade_date": "2026-04-25", "last_qlib_date": "2026-04-25", "status": "success", "reason": "ok", "is_exact_match": True}), patch(
                "scripts.ops.run_shadow_retrain_weekly.run_weekly_shadow_training", return_value=artifacts
            ), patch("scripts.ops.run_shadow_retrain_weekly.send_shadow_run_notification", return_value={"status": "success"}), patch(
                "scripts.ops.run_shadow_retrain_weekly.send_shadow_run_telegram_notification", return_value={"status": "failed"}
            ):
                result = run_shadow_retrain_weekly(tmpdir, run_id="shadow_retrain_2026-04-25_090807", triggered_by="test")
            summary = load_json(Path(result["run_dir"]) / "daily_summary.json")
            self.assertEqual(summary["telegram_notification_status"], "failed")
            self.assertEqual(result["overall_status"], "success")

    def test_gateway_help(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("scripts.ops.run_telegram_gateway._allowed_chat_ids", return_value={"42"}), patch(
                "scripts.ops.run_telegram_gateway.send_telegram_message", return_value={"status": "success"}
            ):
                entry = handle_command(Path(tmpdir), "42", "/help")
        self.assertEqual(entry["command"], "help")
        self.assertEqual(entry["status"], "success")

    def test_gateway_status_allowed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = type("Proc", (), {"returncode": 0, "stdout": "overall_status: success", "stderr": ""})()
            with patch("scripts.ops.run_telegram_gateway._allowed_chat_ids", return_value={"42"}), patch(
                "scripts.ops.run_telegram_gateway.send_telegram_message", return_value={"status": "success"}
            ), patch("scripts.ops.run_telegram_gateway._run_command", return_value=proc):
                entry = handle_command(Path(tmpdir), "42", "/status")
        self.assertEqual(entry["command"], "status")
        self.assertEqual(entry["status"], "success")

    def test_gateway_rejects_non_allowed_chat(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entry = handle_command(Path(tmpdir), "99", "/status")
        self.assertEqual(entry["status"], "rejected")

    def test_gateway_daily_requires_confirm(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("scripts.ops.run_telegram_gateway._allowed_chat_ids", return_value={"42"}), patch(
                "scripts.ops.run_telegram_gateway.send_telegram_message", return_value={"status": "success"}
            ):
                entry = handle_command(Path(tmpdir), "42", "/daily")
                pending = json.loads((Path(tmpdir) / "runs" / "telegram_gateway" / f"pending_{hash_chat_id('42')}.json").read_text())
        self.assertEqual(entry["status"], "success")
        self.assertEqual(pending["command"], "daily")

    def test_gateway_confirm_daily_triggers_daily(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = type("Proc", (), {"returncode": 0, "stdout": '{"run_id": "shadow_1"}', "stderr": ""})()
            with patch("scripts.ops.run_telegram_gateway._allowed_chat_ids", return_value={"42"}), patch(
                "scripts.ops.run_telegram_gateway.send_telegram_message", return_value={"status": "success"}
            ), patch("scripts.ops.run_telegram_gateway._run_command", return_value=proc):
                handle_command(Path(tmpdir), "42", "/daily")
                entry = handle_command(Path(tmpdir), "42", "/confirm daily")
        self.assertEqual(entry["command"], "daily")
        self.assertEqual(entry["status"], "success")
        self.assertEqual(entry["run_id"], "shadow_1")

    def test_gateway_retrain_requires_confirm(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("scripts.ops.run_telegram_gateway._allowed_chat_ids", return_value={"42"}), patch(
                "scripts.ops.run_telegram_gateway.send_telegram_message", return_value={"status": "success"}
            ):
                entry = handle_command(Path(tmpdir), "42", "/retrain")
        self.assertEqual(entry["status"], "success")

    def test_gateway_unknown_and_shell_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("scripts.ops.run_telegram_gateway._allowed_chat_ids", return_value={"42"}), patch(
                "scripts.ops.run_telegram_gateway.send_telegram_message", return_value={"status": "success"}
            ):
                unknown = handle_command(Path(tmpdir), "42", "/wat")
                shell = handle_command(Path(tmpdir), "42", "/shell rm -rf /")
        self.assertEqual(unknown["status"], "rejected")
        self.assertEqual(shell["status"], "rejected")

    def test_gateway_command_log_written(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entry = build_command_log_entry(chat_id="42", command="status", status="success")
            path = append_gateway_command_log(tmpdir, entry)
            content = path.read_text(encoding="utf-8")
        self.assertIn('"command": "status"', content)
        self.assertNotIn('"chat_id":', content)


if __name__ == "__main__":
    unittest.main()
