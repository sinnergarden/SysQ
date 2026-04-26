import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qsys.ops.digest import (
    build_shadow_daily_digest,
    build_shadow_retrain_digest,
    build_shadow_run_digest,
)
from qsys.ops.telegram import send_shadow_run_telegram_notification


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
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


class TestShadowOpsDigest(unittest.TestCase):
    def _build_daily_run(self, tmpdir: str, *, with_predictions: bool = True, with_features: bool = True, order_rows: list[dict] | None = None, execution_summary: dict | None = None) -> tuple[Path, Path]:
        run_dir = Path(tmpdir) / "runs" / "2026-04-25" / "shadow_2026-04-25_100000"
        summary_path = _write_json(
            run_dir / "daily_summary.json",
            {
                "run_type": "shadow_daily",
                "run_id": "shadow_2026-04-25_100000",
                "requested_date": "2026-04-25",
                "trade_date": "2026-04-17",
                "decision_status": "shadow_rebalanced",
                "data_status": "success",
                "feature_status": "success",
                "overall_status": "success",
                "degradation_level": "none",
                "readiness_status": "ok",
                "mainline_object_name": "feature_173",
                "bundle_id": "bundle_feature_173",
                "prediction_count": 3,
                "min_prediction_count": 50,
                "prediction_coverage_status": "ok",
                "telegram_notification_status": "success",
            },
        )
        pointers = {
            "daily_summary_path": str(summary_path.relative_to(Path(tmpdir))),
            "data_status_path": f"runs/2026-04-25/shadow_2026-04-25_100000/01_data/data_status.json",
            "selected_model_path": f"runs/2026-04-25/shadow_2026-04-25_100000/03_model/selected_model.json",
            "inference_summary_path": f"runs/2026-04-25/shadow_2026-04-25_100000/04_inference/inference_summary.json",
            "predictions_path": f"runs/2026-04-25/shadow_2026-04-25_100000/04_inference/predictions.csv",
            "execution_summary_path": f"runs/2026-04-25/shadow_2026-04-25_100000/05_shadow/execution_summary.json",
            "feature_status_path": f"runs/2026-04-25/shadow_2026-04-25_100000/02_features/feature_status.json",
        }
        manifest_path = _write_json(
            run_dir / "manifest.json",
            {
                "run_type": "shadow_daily",
                "overall_status": "success",
                "stage_status": {"archive_report": {"artifact_pointers": pointers}},
            },
        )
        _write_json(run_dir / "01_data" / "data_status.json", {"last_qlib_date": "2026-04-17", "status": "success"})
        _write_json(run_dir / "03_model" / "selected_model.json", {"model_name": "qlib_lgbm_feature_173", "mainline_object_name": "feature_173", "bundle_id": "bundle_feature_173"})
        _write_json(run_dir / "04_inference" / "inference_summary.json", {"prediction_count": 3})
        _write_json(
            run_dir / "05_shadow" / "execution_summary.json",
            execution_summary or {"order_count": 3, "filled_count": 2, "rejected_count": 1, "turnover": 123456.78, "total_value_after": 1001234.56, "coverage_status": "ok"},
        )
        if with_features:
            _write_json(run_dir / "02_features" / "feature_status.json", {"field_count": 173, "usable_field_count": 173, "degradation_level": "none"})
        if with_predictions:
            _write_csv(
                run_dir / "04_inference" / "predictions.csv",
                [
                    {"instrument": "300394.SZ", "score": "0.0182"},
                    {"instrument": "600519.SH", "score": "0.0147"},
                    {"instrument": "000001.SZ", "score": "0.0095"},
                ],
                ["instrument", "score"],
            )
        _write_csv(
            run_dir / "05_shadow" / "order_intents.csv",
            order_rows
            if order_rows is not None
            else [
                {"instrument": "300394.SZ", "reason": "blocked_cash"},
                {"instrument": "600519.SH", "reason": "blocked_cash"},
                {"instrument": "000001.SZ", "reason": "rejected_lot"},
            ],
            ["instrument", "reason"],
        )
        return summary_path, manifest_path

    def _build_weekly_run(self, tmpdir: str) -> tuple[Path, Path]:
        run_dir = Path(tmpdir) / "runs" / "2026-04-25" / "shadow_retrain_2026-04-25_100000"
        summary_path = _write_json(
            run_dir / "daily_summary.json",
            {
                "run_type": "shadow_retrain_weekly",
                "run_id": "shadow_retrain_2026-04-25_100000",
                "trade_date": "2026-04-17",
                "date_resolution": {"requested_date": "2026-04-25", "resolved_trade_date": "2026-04-17"},
                "train_status": "success",
                "model_used": {"fallback": False, "model_name": "qlib_lgbm_feature_173"},
            },
        )
        manifest_path = _write_json(
            run_dir / "manifest.json",
            {
                "run_type": "shadow_retrain_weekly",
                "overall_status": "success",
                "mainline_object_name": "feature_173",
                "bundle_id": "bundle_feature_173",
                "model_name": "qlib_lgbm_feature_173",
                "stage_status": {
                    "archive_report": {"artifact_pointers": {"daily_summary_path": str(summary_path.relative_to(Path(tmpdir))) }},
                    "run_training": {"artifact_pointers": {"stage_output": "runs/2026-04-25/shadow_retrain_2026-04-25_100000/run_training.json"}},
                    "update_model_pointer": {"artifact_pointers": {"stage_output": "runs/2026-04-25/shadow_retrain_2026-04-25_100000/update_model_pointer.json"}},
                },
            },
        )
        _write_json(
            run_dir / "run_training.json",
            {
                "artifact_pointers": {"training_summary_path": str((Path(tmpdir) / "data" / "models" / "qlib_lgbm_feature_173" / "training_summary.json").resolve())},
            },
        )
        _write_json(run_dir / "update_model_pointer.json", {"status": "success"})
        model_dir = Path(tmpdir) / "data" / "models" / "qlib_lgbm_feature_173"
        _write_json(model_dir / "training_summary.json", {"rank_ic": 0.12, "mse": 0.34})
        _write_json(Path(tmpdir) / "models" / "latest_shadow_model.json", {"status": "success", "model_path": str(model_dir)})
        return summary_path, manifest_path

    def test_daily_digest_success_contains_business_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            summary_path, manifest_path = self._build_daily_run(tmpdir)
            digest = build_shadow_daily_digest(summary_path, manifest_path)
        self.assertIn("Qsys Daily Shadow", digest)
        self.assertIn("requested 2026-04-25 -> resolved 2026-04-17", digest)
        self.assertIn("mainline: feature_173", digest)
        self.assertIn("bundle: bundle_feature_173", digest)
        self.assertIn("173 total / 173 usable", digest)
        self.assertIn("readiness_status: ok", digest)
        self.assertIn("predictions: 3 / min_required: 50", digest)
        self.assertIn("300394.SZ score=0.0182", digest)
        self.assertIn("orders: 3, filled: 2, rejected: 1", digest)
        self.assertIn("turnover: 123456.78", digest)
        self.assertIn("total_value_after: 1001234.56", digest)
        self.assertIn("runs/2026-04-25/shadow_2026-04-25_100000/daily_summary.json", digest)

    def test_daily_digest_missing_artifacts_do_not_crash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            summary_path, manifest_path = self._build_daily_run(
                tmpdir,
                with_predictions=False,
                with_features=False,
                order_rows=[],
                execution_summary={"order_count": 0, "filled_count": 0, "rejected_count": 0, "turnover": 0, "total_value_after": None},
            )
            digest = build_shadow_daily_digest(summary_path, manifest_path)
        self.assertIn("fields: N/A total / N/A usable", digest)
        self.assertIn("- top picks:\n none", digest)
        self.assertIn("- no-trade reasons:\n none", digest)

    def test_daily_digest_reason_aggregation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            summary_path, manifest_path = self._build_daily_run(
                tmpdir,
                order_rows=[
                    {"instrument": "A", "reason": "blocked_cash"},
                    {"instrument": "B", "reason": "blocked_cash"},
                    {"instrument": "C", "reason": "blocked_limit"},
                ],
            )
            digest = build_shadow_daily_digest(summary_path, manifest_path)
        self.assertIn("blocked_cash x2", digest)
        self.assertIn("blocked_limit x1", digest)

    def test_daily_digest_uses_audit_for_no_trade_reason(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            summary_path, manifest_path = self._build_daily_run(tmpdir, order_rows=[])
            _write_csv(
                Path(tmpdir) / "runs" / "2026-04-25" / "shadow_2026-04-25_100000" / "05_shadow" / "rebalance_audit.csv",
                [{"trade_date": "2026-04-17", "instrument": "300394.SZ", "score": "0.0182", "target_weight": "0.2", "current_weight": "0.2", "target_value": "200000", "current_value": "200000", "diff_value": "0", "requested_qty": "0", "action": "hold", "reason": "already_at_target"}],
                ["trade_date", "instrument", "score", "target_weight", "current_weight", "target_value", "current_value", "diff_value", "requested_qty", "action", "reason"],
            )
            digest = build_shadow_daily_digest(summary_path, manifest_path)
        self.assertIn("already_at_target x1", digest)

    def test_weekly_digest_contains_core_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            summary_path, manifest_path = self._build_weekly_run(tmpdir)
            digest = build_shadow_retrain_digest(summary_path, manifest_path)
        self.assertIn("Qsys Weekly Retrain", digest)
        self.assertIn("requested 2026-04-25 -> resolved 2026-04-17", digest)
        self.assertIn("mainline: feature_173", digest)
        self.assertIn("bundle: bundle_feature_173", digest)
        self.assertIn("model: qlib_lgbm_feature_173", digest)
        self.assertIn("pointer: updated", digest)
        self.assertIn("fallback: false", digest)
        self.assertIn("RankIC=0.12", digest)

    def test_weekly_digest_missing_metrics_show_na(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            summary_path, manifest_path = self._build_weekly_run(tmpdir)
            training_summary = Path(tmpdir) / "data" / "models" / "qlib_lgbm_feature_173" / "training_summary.json"
            training_summary.unlink()
            digest = build_shadow_retrain_digest(summary_path, manifest_path)
        self.assertIn("metrics: IC=N/A, RankIC=N/A, loss=N/A", digest)

    def test_build_shadow_run_digest_dispatches(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            daily_summary, daily_manifest = self._build_daily_run(tmpdir)
            weekly_summary, weekly_manifest = self._build_weekly_run(tmpdir)
            self.assertIn("Qsys Daily Shadow", build_shadow_run_digest(daily_summary, daily_manifest))
            self.assertIn("Qsys Weekly Retrain", build_shadow_run_digest(weekly_summary, weekly_manifest))

    def test_telegram_notification_uses_digest_builder(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            summary_path, manifest_path = self._build_daily_run(tmpdir)
            with patch("qsys.ops.telegram.build_shadow_run_digest", return_value="Qsys Daily Shadow\ndecision_status: shadow_rebalanced"), patch(
                "qsys.ops.telegram._resolve_bot_token", return_value="123:ABC"
            ), patch("qsys.ops.telegram._resolve_chat_id", return_value="42"), patch(
                "qsys.ops.telegram.requests.post", return_value=_FakeResponse(200, '{"ok": true, "result": {}}')
            ) as post_mock:
                result = send_shadow_run_telegram_notification(summary_path, manifest_path)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["digest_status"], "success")
        payload = post_mock.call_args.kwargs["json"]
        self.assertNotIn("parse_mode", payload)
        self.assertIn("decision_status: shadow_rebalanced", payload["text"])

    def test_telegram_notification_falls_back_when_digest_builder_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            summary_path, manifest_path = self._build_daily_run(tmpdir)
            with patch("qsys.ops.telegram.build_shadow_run_digest", side_effect=RuntimeError("digest boom")), patch(
                "qsys.ops.telegram._resolve_bot_token", return_value="123:ABC"
            ), patch("qsys.ops.telegram._resolve_chat_id", return_value="42"), patch(
                "qsys.ops.telegram.requests.post", return_value=_FakeResponse(200, '{"ok": true, "result": {}}')
            ) as post_mock:
                result = send_shadow_run_telegram_notification(summary_path, manifest_path)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["digest_status"], "failed")
        self.assertIn("digest_error", result)
        payload = post_mock.call_args.kwargs["json"]
        self.assertIn("Qsys shadow_daily", payload["text"])
