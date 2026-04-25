import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from qsys.ops import build_latest_shadow_model_payload, write_latest_shadow_model
from qsys.ops.state import atomic_write_json
from scripts.ops.check_shadow_status import build_latest_ops_status


class TestShadowOpsStatus(unittest.TestCase):
    def _write_json(self, path: Path, payload: dict) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def _write_daily_run(self, base_dir: Path, *, run_id: str, overall_status: str, decision_status: str, notification_status: str) -> tuple[Path, Path]:
        run_dir = base_dir / "runs" / "2026-04-25" / run_id
        manifest = {
            "run_id": run_id,
            "run_type": "shadow_daily",
            "trade_date": "2026-04-25",
            "overall_status": overall_status,
            "stage_status": {"archive_report": {"status": overall_status, "artifact_pointers": {}}},
        }
        summary = {
            "trade_date": "2026-04-25",
            "run_id": run_id,
            "run_type": "shadow_daily",
            "overall_status": overall_status,
            "decision_status": decision_status,
            "notification_status": notification_status,
            "model_used": {"model_name": "qlib_lgbm_extended"},
        }
        self._write_json(run_dir / "manifest.json", manifest)
        self._write_json(run_dir / "daily_summary.json", summary)
        self._write_json(base_dir / "runs" / "latest_shadow_daily.json", {
            "run_id": run_id,
            "trade_date": "2026-04-25",
            "overall_status": overall_status,
            "manifest_path": str(run_dir / "manifest.json"),
            "daily_summary_path": str(run_dir / "daily_summary.json"),
            "updated_at": "2026-04-25T00:00:00Z",
        })
        return run_dir / "manifest.json", run_dir / "daily_summary.json"

    def _write_weekly_run(self, base_dir: Path, *, run_id: str, overall_status: str, decision_status: str, notification_status: str) -> tuple[Path, Path]:
        run_dir = base_dir / "runs" / "2026-04-25" / run_id
        manifest = {
            "run_id": run_id,
            "run_type": "shadow_retrain_weekly",
            "trade_date": "2026-04-25",
            "overall_status": overall_status,
            "stage_status": {"archive_report": {"status": overall_status, "artifact_pointers": {}}},
        }
        summary = {
            "trade_date": "2026-04-25",
            "run_id": run_id,
            "run_type": "shadow_retrain_weekly",
            "overall_status": overall_status,
            "decision_status": decision_status,
            "notification_status": notification_status,
            "model_used": {"model_name": "qlib_lgbm_extended"},
        }
        self._write_json(run_dir / "manifest.json", manifest)
        self._write_json(run_dir / "daily_summary.json", summary)
        self._write_json(base_dir / "runs" / "latest_shadow_retrain.json", {
            "run_id": run_id,
            "trade_date": "2026-04-25",
            "overall_status": overall_status,
            "manifest_path": str(run_dir / "manifest.json"),
            "updated_at": "2026-04-25T00:00:00Z",
        })
        return run_dir / "manifest.json", run_dir / "daily_summary.json"

    def _write_usable_model(self, base_dir: Path, *, status: str = "success", missing_meta: bool = False) -> Path:
        model_dir = base_dir / "data" / "models" / "qlib_lgbm_extended"
        model_dir.mkdir(parents=True, exist_ok=True)
        for name in ["config_snapshot.json", "training_summary.json", "decisions.json", "model.pkl"]:
            (model_dir / name).write_text("{}\n", encoding="utf-8")
        if not missing_meta:
            (model_dir / "meta.yaml").write_text("meta: true\n", encoding="utf-8")
        payload = build_latest_shadow_model_payload(
            model_name="qlib_lgbm_extended",
            model_path=str(model_dir),
            mainline_object_name="feature_173",
            bundle_id="bundle_feature_173",
            train_run_id="shadow_retrain_2026-04-25_090807",
            trained_at="2026-04-25T09:08:07",
            status=status,
        )
        return write_latest_shadow_model(base_dir, payload)

    def _write_account_and_ledger(self, base_dir: Path) -> None:
        self._write_json(base_dir / "shadow" / "account.json", {
            "cash": 1000.0,
            "market_value": 9000.0,
            "total_value": 10000.0,
            "last_run_id": "shadow_2026-04-25_090807",
        })
        (base_dir / "shadow" / "ledger.csv").parent.mkdir(parents=True, exist_ok=True)
        (base_dir / "shadow" / "ledger.csv").write_text(
            "run_id,trade_date,instrument,side,quantity,price,amount,fee,status,reason\n"
            "shadow_2026-04-25_090807,2026-04-25,SH600000,buy,100,10,1000,0,filled,test\n",
            encoding="utf-8",
        )

    def test_missing_latest_status_is_safe(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = build_latest_ops_status(tmpdir)
        self.assertIn(payload["overall_status"], {"unknown", "degraded"})
        self.assertTrue(any("missing" in item for item in payload["issues"]))

    def test_successful_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            self._write_daily_run(base_dir, run_id="shadow_2026-04-25_090807", overall_status="success", decision_status="shadow_rebalanced", notification_status="success")
            self._write_weekly_run(base_dir, run_id="shadow_retrain_2026-04-25_090807", overall_status="success", decision_status="update_model_pointer", notification_status="success")
            self._write_usable_model(base_dir)
            self._write_account_and_ledger(base_dir)
            payload = build_latest_ops_status(base_dir)
        self.assertEqual(payload["overall_status"], "success")
        self.assertEqual(payload["daily"]["overall_status"], "success")
        self.assertEqual(payload["weekly_retrain"]["overall_status"], "success")
        self.assertTrue(payload["latest_model"]["usable"])
        self.assertEqual(payload["shadow_ledger"]["row_count"], 1)

    def test_model_unusable_forces_failed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            self._write_daily_run(base_dir, run_id="shadow_2026-04-25_090807", overall_status="success", decision_status="shadow_rebalanced", notification_status="success")
            self._write_weekly_run(base_dir, run_id="shadow_retrain_2026-04-25_090807", overall_status="success", decision_status="update_model_pointer", notification_status="success")
            self._write_usable_model(base_dir, missing_meta=True)
            self._write_account_and_ledger(base_dir)
            payload = build_latest_ops_status(base_dir)
        self.assertFalse(payload["latest_model"]["usable"])
        self.assertEqual(payload["overall_status"], "failed")
        self.assertTrue(any("unusable" in item for item in payload["issues"]))

    def test_notification_failed_is_degraded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            self._write_daily_run(base_dir, run_id="shadow_2026-04-25_090807", overall_status="success", decision_status="shadow_rebalanced", notification_status="failed")
            self._write_weekly_run(base_dir, run_id="shadow_retrain_2026-04-25_090807", overall_status="success", decision_status="update_model_pointer", notification_status="success")
            self._write_usable_model(base_dir)
            self._write_account_and_ledger(base_dir)
            payload = build_latest_ops_status(base_dir)
        self.assertEqual(payload["overall_status"], "degraded")
        self.assertTrue(any("notification failed" in item for item in payload["issues"]))

    def test_weekly_fallback_is_degraded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            self._write_daily_run(base_dir, run_id="shadow_2026-04-25_090807", overall_status="success", decision_status="shadow_rebalanced", notification_status="success")
            self._write_weekly_run(base_dir, run_id="shadow_retrain_2026-04-25_090807", overall_status="fallback", decision_status="update_model_pointer", notification_status="success")
            self._write_usable_model(base_dir)
            self._write_account_and_ledger(base_dir)
            payload = build_latest_ops_status(base_dir)
        self.assertEqual(payload["overall_status"], "degraded")
        self.assertTrue(any("fallback" in item for item in payload["issues"]))

    def test_cli_smoke_and_write_latest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            self._write_daily_run(base_dir, run_id="shadow_2026-04-25_090807", overall_status="success", decision_status="shadow_rebalanced", notification_status="success")
            self._write_weekly_run(base_dir, run_id="shadow_retrain_2026-04-25_090807", overall_status="success", decision_status="update_model_pointer", notification_status="success")
            self._write_usable_model(base_dir)
            self._write_account_and_ledger(base_dir)

            json_cmd = ["/home/liuming/.openclaw/workspace/SysQ/.envs/test/bin/python", "scripts/ops/check_shadow_status.py", "--base-dir", tmpdir, "--format", "json", "--write-latest"]
            json_proc = subprocess.run(json_cmd, cwd="/home/liuming/.openclaw/workspace/SysQ", capture_output=True, text=True, check=True)
            payload = json.loads(json_proc.stdout)
            self.assertEqual(payload["overall_status"], "success")
            self.assertTrue((base_dir / "runs" / "latest_ops_status.json").exists())

            text_cmd = ["/home/liuming/.openclaw/workspace/SysQ/.envs/test/bin/python", "scripts/ops/check_shadow_status.py", "--base-dir", tmpdir, "--format", "text"]
            text_proc = subprocess.run(text_cmd, cwd="/home/liuming/.openclaw/workspace/SysQ", capture_output=True, text=True, check=True)
            self.assertIn("overall_status: success", text_proc.stdout)
