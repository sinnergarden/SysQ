import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from qsys.ops.manifest import DAILY_STAGES, RETRAIN_STAGES, build_run_context, finalize_run, format_run_id, initialize_run, update_stage_status
from qsys.ops.model_registry import (
    build_latest_shadow_model_payload,
    latest_shadow_model_is_usable,
    read_latest_shadow_model,
    write_latest_shadow_model,
)
from qsys.ops.state import ALLOWED_STATUSES, load_json, summarize_overall_status, validate_status, write_latest_pointer
from qsys.ops.training import TrainingArtifacts, TrainingInvocationError
from scripts.ops.run_shadow_daily import run_shadow_daily
from scripts.ops.run_shadow_retrain_weekly import run_shadow_retrain_weekly


class TestShadowOpsProtocol(unittest.TestCase):
    def test_validate_status_rejects_unknown_values(self):
        for status in ALLOWED_STATUSES:
            self.assertEqual(validate_status(status), status)
        with self.assertRaises(ValueError):
            validate_status("partial")

    def test_format_run_id_matches_contract(self):
        dt = datetime(2026, 4, 25, 9, 8, 7)
        self.assertEqual(format_run_id("daily", dt), "shadow_2026-04-25_090807")
        self.assertEqual(format_run_id("weekly_retrain", dt), "shadow_retrain_2026-04-25_090807")

    def test_manifest_contract_and_finalize_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            context = initialize_run(
                tmpdir,
                run_type="daily",
                run_id="shadow_2026-04-25_090807",
                mainline_object_name="mainline_a",
                bundle_id="bundle_a",
                model_name="model_a",
                model_snapshot_path="models/model_a.bin",
                latest_model_pointer="models/latest_shadow_model.json",
                data_snapshot={"raw_latest": "2026-04-24"},
                fallback_summary={"used": False},
                notes=["stub"],
            )
            manifest = load_json(context.manifest_path)
            self.assertEqual(manifest["run_id"], "shadow_2026-04-25_090807")
            self.assertEqual(manifest["run_type"], "shadow_daily")
            self.assertEqual(manifest["trade_date"], "2026-04-25")
            self.assertEqual(set(manifest["stage_status"].keys()), set(DAILY_STAGES))
            self.assertEqual(sorted(manifest.keys()), sorted([
                "run_id",
                "run_type",
                "trade_date",
                "mainline_object_name",
                "bundle_id",
                "model_name",
                "model_snapshot_path",
                "latest_model_pointer",
                "stage_status",
                "overall_status",
                "data_snapshot",
                "fallback_summary",
                "started_at",
                "ended_at",
                "notes",
            ]))

            update_stage_status(context, stage_name="data_sync", status="success", message="ok")
            update_stage_status(context, stage_name="feature_refresh", status="fallback", message="fallback")
            for stage_name in DAILY_STAGES[2:]:
                update_stage_status(context, stage_name=stage_name, status="success", message="ok")

            summary = finalize_run(
                context,
                daily_summary={
                    "trade_date": "2026-04-25",
                    "run_id": context.run_id,
                    "run_type": "shadow_daily",
                    "data_status": "success",
                    "feature_status": "fallback",
                    "train_status": "success",
                    "model_used": {"model_name": "model_a"},
                    "inference_status": "success",
                    "rebalance_status": "success",
                    "shadow_order_count": 0,
                    "degradation_level": "low",
                    "decision_status": "fallback",
                    "notes": ["done"],
                },
                notes=["done"],
                fallback_summary={"used": True, "reason": "stub"},
            )
            manifest = load_json(context.manifest_path)
            latest = load_json(context.latest_pointer_path)
            self.assertEqual(manifest["overall_status"], "fallback")
            self.assertEqual(summary["decision_status"], "fallback")
            self.assertEqual(manifest["stage_status"]["feature_refresh"]["status"], "fallback")
            self.assertEqual(set(manifest["stage_status"]["data_sync"].keys()), {"status", "started_at", "ended_at", "message", "artifact_pointers"})
            self.assertEqual(latest["run_id"], context.run_id)
            self.assertEqual(latest["daily_summary_path"], str(context.summary_path))

    def test_write_latest_pointer_overwrites_atomically(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pointer_path = Path(tmpdir) / "runs" / "latest_shadow_daily.json"
            write_latest_pointer(pointer_path, {"run_id": "shadow_1", "overall_status": "success"})
            write_latest_pointer(pointer_path, {"run_id": "shadow_2", "overall_status": "failed"})
            payload = load_json(pointer_path)
            self.assertEqual(payload, {"overall_status": "failed", "run_id": "shadow_2"})

    def test_write_latest_shadow_model_contract(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            model_dir = Path(tmpdir) / "data" / "models" / "model_a"
            model_dir.mkdir(parents=True)
            path = write_latest_shadow_model(
                tmpdir,
                build_latest_shadow_model_payload(
                    model_name="model_a",
                    model_path=str(model_dir),
                    mainline_object_name="mainline_a",
                    bundle_id="bundle_a",
                    train_run_id="shadow_retrain_2026-04-25_090807",
                    trained_at="2026-04-25T09:08:07",
                    status="success",
                ),
            )
            payload = load_json(path)
            self.assertEqual(path.name, "latest_shadow_model.json")
            self.assertEqual(sorted(payload.keys()), sorted([
                "model_name",
                "model_path",
                "mainline_object_name",
                "bundle_id",
                "train_run_id",
                "trained_at",
                "status",
            ]))
            self.assertEqual(payload["train_run_id"], "shadow_retrain_2026-04-25_090807")
            self.assertEqual(read_latest_shadow_model(tmpdir)["model_name"], "model_a")
            self.assertTrue(latest_shadow_model_is_usable(tmpdir))

    def test_daily_runner_writes_complete_stub_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_shadow_daily(tmpdir, run_id="shadow_2026-04-25_090807", triggered_by="test")
            run_dir = Path(result["run_dir"])
            manifest = load_json(run_dir / "manifest.json")
            summary = load_json(run_dir / "daily_summary.json")
            latest = load_json(Path(tmpdir) / "runs" / "latest_shadow_daily.json")
            self.assertEqual(result["overall_status"], "success")
            self.assertEqual(manifest["run_type"], "shadow_daily")
            self.assertEqual(summary["run_type"], "shadow_daily")
            self.assertEqual(latest["run_id"], result["run_id"])
            self.assertIn("daily_summary_path", latest)
            for stage_name in DAILY_STAGES:
                self.assertEqual(manifest["stage_status"][stage_name]["status"], "success")
                self.assertTrue((run_dir / f"{stage_name}.json").exists())

    def test_weekly_runner_success_updates_model_pointer_and_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            model_dir = base_dir / "data" / "models" / "qlib_lgbm_extended"
            model_dir.mkdir(parents=True)
            (model_dir / "training_summary.json").write_text("{}\n", encoding="utf-8")
            (model_dir / "config_snapshot.json").write_text("{}\n", encoding="utf-8")
            (model_dir / "decisions.json").write_text("{}\n", encoding="utf-8")
            report_path = base_dir / "experiments" / "reports" / "train_success.json"
            report_path.parent.mkdir(parents=True)
            report_path.write_text("{}\n", encoding="utf-8")

            artifacts = TrainingArtifacts(
                mainline_object_name="feature_173",
                bundle_id="bundle_feature_173",
                model_name="qlib_lgbm_extended",
                model_path=str(model_dir),
                config_snapshot_path=str(model_dir / "config_snapshot.json"),
                training_summary_path=str(model_dir / "training_summary.json"),
                decisions_path=str(model_dir / "decisions.json"),
                training_report_path=str(report_path),
                trained_at="2026-04-25T09:08:07",
                train_run_id="shadow_retrain_2026-04-25_090807",
                command=["python", "scripts/run_train.py"],
            )

            with patch("scripts.ops.run_shadow_retrain_weekly.run_weekly_shadow_training", return_value=artifacts):
                result = run_shadow_retrain_weekly(base_dir, run_id="shadow_retrain_2026-04-25_090807", triggered_by="test")

            run_dir = Path(result["run_dir"])
            manifest = load_json(run_dir / "manifest.json")
            summary = load_json(run_dir / "daily_summary.json")
            latest_run = load_json(base_dir / "runs" / "latest_shadow_retrain.json")
            latest_model = load_json(base_dir / "models" / "latest_shadow_model.json")
            self.assertEqual(result["overall_status"], "success")
            self.assertEqual(manifest["mainline_object_name"], "feature_173")
            self.assertEqual(manifest["bundle_id"], "bundle_feature_173")
            self.assertEqual(manifest["model_name"], "qlib_lgbm_extended")
            self.assertEqual(manifest["model_snapshot_path"], str(model_dir))
            self.assertEqual(latest_run["run_id"], result["run_id"])
            self.assertEqual(latest_model["train_run_id"], result["run_id"])
            self.assertEqual(summary["train_status"], "success")
            self.assertFalse(summary["model_used"]["fallback"])
            self.assertEqual(summary["model_used"]["model_name"], "qlib_lgbm_extended")
            training_artifacts = manifest["stage_status"]["run_training"]["artifact_pointers"]
            self.assertEqual(training_artifacts["training_report_path"], str(report_path))
            self.assertEqual(training_artifacts["training_summary_path"], str(model_dir / "training_summary.json"))
            self.assertEqual(training_artifacts["config_snapshot_path"], str(model_dir / "config_snapshot.json"))
            self.assertEqual(training_artifacts["decisions_path"], str(model_dir / "decisions.json"))
            self.assertEqual(training_artifacts["model_path"], str(model_dir))
            self.assertEqual(training_artifacts["latest_model_pointer_path"], str(base_dir / "models" / "latest_shadow_model.json"))
            self.assertEqual(load_json(run_dir / "run_training.json")["artifact_pointers"]["model_path"], str(model_dir))

    def test_weekly_runner_fallback_retains_previous_model_pointer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            retained_model_dir = base_dir / "data" / "models" / "retained_model"
            retained_model_dir.mkdir(parents=True)
            old_payload = build_latest_shadow_model_payload(
                model_name="retained_model",
                model_path=str(retained_model_dir),
                mainline_object_name="feature_173",
                bundle_id="bundle_feature_173",
                train_run_id="shadow_retrain_2026-04-18_090807",
                trained_at="2026-04-18T09:08:07",
                status="success",
            )
            write_latest_shadow_model(base_dir, old_payload)

            with patch(
                "scripts.ops.run_shadow_retrain_weekly.run_weekly_shadow_training",
                side_effect=TrainingInvocationError("boom"),
            ):
                result = run_shadow_retrain_weekly(base_dir, run_id="shadow_retrain_2026-04-25_090807", triggered_by="test")

            run_dir = Path(result["run_dir"])
            manifest = load_json(run_dir / "manifest.json")
            summary = load_json(run_dir / "daily_summary.json")
            latest_model = load_json(base_dir / "models" / "latest_shadow_model.json")
            self.assertEqual(result["overall_status"], "fallback")
            self.assertEqual(manifest["stage_status"]["run_training"]["status"], "fallback")
            self.assertEqual(manifest["stage_status"]["update_model_pointer"]["status"], "fallback")
            self.assertEqual(latest_model, old_payload)
            self.assertTrue(manifest["fallback_summary"]["used"])
            self.assertEqual(manifest["fallback_summary"]["retained_previous_model"]["model_name"], "retained_model")
            self.assertEqual(summary["train_status"], "fallback")
            self.assertTrue(summary["model_used"]["fallback"])
            self.assertEqual(summary["model_used"]["model_name"], "retained_model")
            joined_notes = " ".join(summary["notes"])
            self.assertIn("Retained previous model", joined_notes)

    def test_weekly_runner_hard_failure_without_previous_model(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "scripts.ops.run_shadow_retrain_weekly.run_weekly_shadow_training",
                side_effect=TrainingInvocationError("boom"),
            ):
                result = run_shadow_retrain_weekly(tmpdir, run_id="shadow_retrain_2026-04-25_090807", triggered_by="test")

            run_dir = Path(result["run_dir"])
            manifest = load_json(run_dir / "manifest.json")
            summary = load_json(run_dir / "daily_summary.json")
            self.assertEqual(result["overall_status"], "failed")
            self.assertEqual(manifest["stage_status"]["run_training"]["status"], "failed")
            self.assertEqual(manifest["stage_status"]["update_model_pointer"]["status"], "failed")
            self.assertFalse((Path(tmpdir) / "models" / "latest_shadow_model.json").exists())
            self.assertEqual(summary["train_status"], "failed")
            self.assertTrue(summary["model_used"]["fallback"] is False)
            self.assertIn("No usable model", " ".join(summary["notes"]))

    def test_weekly_runner_artifact_contract_paths_are_traceable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            model_dir = base_dir / "data" / "models" / "qlib_lgbm_extended"
            model_dir.mkdir(parents=True)
            for name in ["training_summary.json", "config_snapshot.json", "decisions.json"]:
                (model_dir / name).write_text("{}\n", encoding="utf-8")
            report_path = base_dir / "experiments" / "reports" / "train_success.json"
            report_path.parent.mkdir(parents=True)
            report_path.write_text("{}\n", encoding="utf-8")

            artifacts = TrainingArtifacts(
                mainline_object_name="feature_173",
                bundle_id="bundle_feature_173",
                model_name="qlib_lgbm_extended",
                model_path=str(model_dir),
                config_snapshot_path=str(model_dir / "config_snapshot.json"),
                training_summary_path=str(model_dir / "training_summary.json"),
                decisions_path=str(model_dir / "decisions.json"),
                training_report_path=str(report_path),
                trained_at="2026-04-25T09:08:07",
                train_run_id="shadow_retrain_2026-04-25_090807",
                command=["python", "scripts/run_train.py"],
            )

            with patch("scripts.ops.run_shadow_retrain_weekly.run_weekly_shadow_training", return_value=artifacts):
                result = run_shadow_retrain_weekly(base_dir, run_id="shadow_retrain_2026-04-25_090807", triggered_by="test")

            run_dir = Path(result["run_dir"])
            manifest = load_json(run_dir / "manifest.json")
            self.assertEqual(manifest["model_snapshot_path"], str(model_dir))
            self.assertEqual(manifest["latest_model_pointer"], str(base_dir / "models" / "latest_shadow_model.json"))
            self.assertEqual(manifest["stage_status"]["run_training"]["artifact_pointers"]["model_path"], str(model_dir))
            self.assertEqual(manifest["stage_status"]["update_model_pointer"]["artifact_pointers"]["latest_model_pointer_path"], str(base_dir / "models" / "latest_shadow_model.json"))
            for relative_name in [
                "prepare_training.json",
                "run_training.json",
                "update_model_pointer.json",
                "archive_report.json",
                "manifest.json",
                "daily_summary.json",
            ]:
                self.assertTrue((run_dir / relative_name).exists())

    def test_build_run_context_uses_stable_directory(self):
        context = build_run_context("/tmp/project", run_type="daily", run_id="shadow_2026-04-25_090807")
        self.assertEqual(context.run_dir, Path("/tmp/project") / "runs" / "2026-04-25" / "shadow_2026-04-25_090807")
        self.assertEqual(context.latest_pointer_path, Path("/tmp/project") / "runs" / "latest_shadow_daily.json")

    def test_retrain_stages_contract_is_stable(self):
        self.assertEqual(RETRAIN_STAGES, [
            "prepare_training",
            "run_training",
            "update_model_pointer",
            "archive_report",
        ])

    def test_overall_status_priority(self):
        self.assertEqual(summarize_overall_status(["success", "success"]), "success")
        self.assertEqual(summarize_overall_status(["success", "skipped"]), "skipped")
        self.assertEqual(summarize_overall_status(["success", "failed"]), "failed")


if __name__ == "__main__":
    unittest.main()
