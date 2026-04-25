#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qsys.config import cfg
from qsys.data.adapter import QlibAdapter
from qsys.data.health import inspect_qlib_data_health
from qsys.ops import (
    finalize_run,
    format_run_id,
    initialize_run,
    latest_shadow_model_is_usable,
    read_latest_shadow_model,
    update_stage_status,
)
from qsys.ops.inference import InferenceArtifacts, InferenceInvocationError, run_shadow_daily_inference, write_failed_inference_summary
from qsys.ops.state import atomic_write_json, load_json
from qsys.research.mainline import MAINLINE_OBJECTS, resolve_mainline_feature_config
from qsys.research.readiness import build_feature_coverage, build_readiness_summary

DEFAULT_MAINLINE_OBJECT_NAME = "feature_173"
DEFAULT_UNIVERSE = "csi300"


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _set_stage(
    context,
    *,
    stage_name: str,
    status: str,
    message: str,
    artifact_pointers: dict[str, Any] | None = None,
    started_at: str | None = None,
) -> None:
    now_text = datetime.now().replace(microsecond=0).isoformat()
    update_stage_status(
        context,
        stage_name=stage_name,
        status=status,
        started_at=started_at or now_text,
        ended_at=now_text,
        message=message,
        artifact_pointers=artifact_pointers or {},
    )


def _update_manifest_model_info(context, model_payload: dict[str, Any]) -> None:
    manifest = load_json(context.manifest_path)
    manifest["mainline_object_name"] = str(model_payload.get("mainline_object_name", manifest.get("mainline_object_name", "")))
    manifest["bundle_id"] = str(model_payload.get("bundle_id", manifest.get("bundle_id", "")))
    manifest["model_name"] = str(model_payload.get("model_name", manifest.get("model_name", "")))
    manifest["model_snapshot_path"] = str(model_payload.get("model_path", manifest.get("model_snapshot_path", "")))
    atomic_write_json(context.manifest_path, manifest)


def _build_data_status(*, trade_date: str, universe: str, mainline_object_name: str) -> dict[str, Any]:
    data_root = cfg.get_path("root")
    qlib_dir = cfg.get_path("qlib_bin")
    feature_fields = resolve_mainline_feature_config(mainline_object_name) or ["$close"]
    adapter = QlibAdapter()
    adapter.init_qlib()
    last_qlib_date = adapter.get_last_qlib_date()
    report = inspect_qlib_data_health(trade_date, feature_fields, universe=universe)
    return {
        "trade_date": trade_date,
        "status": "success" if report.ok else "failed",
        "mode": "freshness_check_only",
        "lightweight_check_only": True,
        "universe": universe,
        "mainline_object_name": mainline_object_name,
        "data_root": str(data_root),
        "qlib_dir": str(qlib_dir),
        "data_root_exists": bool(data_root.exists()),
        "qlib_dir_exists": bool(qlib_dir.exists()),
        "last_qlib_date": last_qlib_date.strftime("%Y-%m-%d") if last_qlib_date is not None else None,
        "health_report": report.to_dict(),
        "error": None,
    }


def _build_feature_status(*, trade_date: str, universe: str, mainline_object_name: str) -> dict[str, Any]:
    feature_config = resolve_mainline_feature_config(mainline_object_name)
    if not feature_config:
        return {
            "trade_date": trade_date,
            "status": "failed",
            "mode": "readiness_check_only",
            "lightweight_check_only": True,
            "mainline_object_name": mainline_object_name,
            "field_count": 0,
            "usable_field_count": 0,
            "degradation_level": "blocked",
            "notes": [f"No feature config found for {mainline_object_name}"],
            "error": f"No feature config found for {mainline_object_name}",
        }

    adapter = QlibAdapter()
    adapter.init_qlib()
    frame = adapter.get_features(universe, feature_config, start_time=trade_date, end_time=trade_date)
    coverage = build_feature_coverage(spec=MAINLINE_OBJECTS[mainline_object_name], frame=frame)
    summary = build_readiness_summary(spec=MAINLINE_OBJECTS[mainline_object_name], coverage=coverage)
    return {
        "trade_date": trade_date,
        "status": "success",
        "mode": "readiness_check_only",
        "lightweight_check_only": True,
        "mainline_object_name": mainline_object_name,
        **summary,
        "error": None,
    }


def _select_latest_model(base_dir: Path) -> tuple[dict[str, Any] | None, str | None]:
    payload = read_latest_shadow_model(base_dir)
    if not latest_shadow_model_is_usable(base_dir, payload):
        return None, "no usable latest model"
    return payload, None


def run_shadow_daily(base_dir: str | Path, *, run_id: str | None = None, triggered_by: str = "manual") -> dict[str, object]:
    now = datetime.now()
    resolved_run_id = run_id or format_run_id("daily", now)
    base_dir = Path(base_dir)
    latest_model_pointer = str(base_dir / "models" / "latest_shadow_model.json")
    mainline_object_name = DEFAULT_MAINLINE_OBJECT_NAME

    context = initialize_run(
        base_dir,
        run_type="daily",
        run_id=resolved_run_id,
        mainline_object_name=mainline_object_name,
        bundle_id=MAINLINE_OBJECTS[mainline_object_name].bundle_id,
        model_name="",
        model_snapshot_path="",
        latest_model_pointer=latest_model_pointer,
        data_snapshot={
            "triggered_by": triggered_by,
            "check_mode": "lightweight_only",
            "heavy_data_update_called": False,
        },
        fallback_summary={"used": False},
        notes=[
            "Daily shadow runner consumes latest shadow model and only runs lightweight checks plus inference.",
            "maybe_retrain and shadow_rebalance stay skipped by design in PR3A.",
        ],
    )

    notes: list[str] = []
    selected_model_payload: dict[str, Any] | None = None
    inference_artifacts: InferenceArtifacts | None = None
    data_status_payload: dict[str, Any] | None = None
    feature_status_payload: dict[str, Any] | None = None
    selected_model_path: Path | None = None
    inference_summary_path: Path | None = None
    predictions_path: Path | None = None

    data_dir = context.run_dir / "01_data"
    feature_dir = context.run_dir / "02_features"
    model_dir = context.run_dir / "03_model"
    inference_dir = context.run_dir / "04_inference"

    data_started = datetime.now().replace(microsecond=0).isoformat()
    update_stage_status(context, stage_name="data_sync", status="running", started_at=data_started, message="Running lightweight data freshness check.")
    try:
        data_status_payload = _build_data_status(trade_date=context.trade_date, universe=DEFAULT_UNIVERSE, mainline_object_name=mainline_object_name)
        data_status_path = _write_json(data_dir / "data_status.json", data_status_payload)
        data_status = "success" if data_status_payload["status"] == "success" else "failed"
        _set_stage(
            context,
            stage_name="data_sync",
            status=data_status,
            started_at=data_started,
            message="Lightweight data freshness check completed." if data_status == "success" else "Lightweight data freshness check failed.",
            artifact_pointers={"data_status_path": str(data_status_path)},
        )
        if data_status != "success":
            notes.extend(data_status_payload.get("health_report", {}).get("blocking_issues", []))
    except Exception as exc:
        data_status_payload = {
            "trade_date": context.trade_date,
            "status": "failed",
            "mode": "freshness_check_only",
            "lightweight_check_only": True,
            "mainline_object_name": mainline_object_name,
            "error": str(exc),
        }
        data_status_path = _write_json(data_dir / "data_status.json", data_status_payload)
        _set_stage(
            context,
            stage_name="data_sync",
            status="failed",
            started_at=data_started,
            message=f"Lightweight data freshness check failed: {exc}",
            artifact_pointers={"data_status_path": str(data_status_path)},
        )
        notes.append(f"data_sync failed: {exc}")

    feature_started = datetime.now().replace(microsecond=0).isoformat()
    update_stage_status(context, stage_name="feature_refresh", status="running", started_at=feature_started, message="Running lightweight feature readiness check.")
    try:
        feature_status_payload = _build_feature_status(trade_date=context.trade_date, universe=DEFAULT_UNIVERSE, mainline_object_name=mainline_object_name)
        feature_status_path = _write_json(feature_dir / "feature_status.json", feature_status_payload)
        feature_status = "success" if feature_status_payload["status"] == "success" else "failed"
        _set_stage(
            context,
            stage_name="feature_refresh",
            status=feature_status,
            started_at=feature_started,
            message="Lightweight feature readiness check completed." if feature_status == "success" else "Lightweight feature readiness check failed.",
            artifact_pointers={"feature_status_path": str(feature_status_path)},
        )
        if feature_status_payload.get("notes"):
            notes.extend([str(item) for item in feature_status_payload["notes"][:5]])
    except Exception as exc:
        feature_status_payload = {
            "trade_date": context.trade_date,
            "status": "failed",
            "mode": "readiness_check_only",
            "lightweight_check_only": True,
            "mainline_object_name": mainline_object_name,
            "error": str(exc),
        }
        feature_status_path = _write_json(feature_dir / "feature_status.json", feature_status_payload)
        _set_stage(
            context,
            stage_name="feature_refresh",
            status="failed",
            started_at=feature_started,
            message=f"Lightweight feature readiness check failed: {exc}",
            artifact_pointers={"feature_status_path": str(feature_status_path)},
        )
        notes.append(f"feature_refresh failed: {exc}")

    maybe_retrain_path = _write_json(
        context.run_dir / "maybe_retrain.json",
        {
            "stage": "maybe_retrain",
            "status": "skipped",
            "reason": "daily_runner_does_not_train_in_pr3a",
            "triggered_by": triggered_by,
        },
    )
    _set_stage(
        context,
        stage_name="maybe_retrain",
        status="skipped",
        message="PR3A keeps daily retrain disabled by design.",
        artifact_pointers={"stage_output": str(maybe_retrain_path)},
    )

    select_started = datetime.now().replace(microsecond=0).isoformat()
    update_stage_status(context, stage_name="select_model", status="running", started_at=select_started, message="Selecting latest usable shadow model.")
    selected_model_payload, model_error = _select_latest_model(base_dir)
    if selected_model_payload is None:
        selected_model_payload = {
            "model_name": "",
            "model_path": "",
            "mainline_object_name": "",
            "bundle_id": "",
            "train_run_id": "",
            "status": "failed",
            "error": model_error or "no usable latest model",
        }
        selected_model_path = _write_json(model_dir / "selected_model.json", selected_model_payload)
        _set_stage(
            context,
            stage_name="select_model",
            status="failed",
            started_at=select_started,
            message=model_error or "no usable latest model",
            artifact_pointers={"selected_model_path": str(selected_model_path)},
        )
        inference_summary_path = write_failed_inference_summary(
            trade_date=context.trade_date,
            model_payload=selected_model_payload,
            output_dir=inference_dir,
            error=model_error or "no usable latest model",
        )
        _set_stage(
            context,
            stage_name="inference",
            status="skipped",
            message="Inference skipped because there is no usable latest model.",
            artifact_pointers={"inference_summary_path": str(inference_summary_path)},
        )
        shadow_rebalance_path = _write_json(
            context.run_dir / "shadow_rebalance.json",
            {
                "stage": "shadow_rebalance",
                "status": "skipped",
                "reason": "inference_not_available",
                "message": model_error or "no usable latest model",
            },
        )
        _set_stage(
            context,
            stage_name="shadow_rebalance",
            status="skipped",
            message="Shadow rebalance skipped because inference did not run.",
            artifact_pointers={"stage_output": str(shadow_rebalance_path)},
        )
        notes.append(model_error or "no usable latest model")
    else:
        _update_manifest_model_info(context, selected_model_payload)
        selected_model_path = _write_json(model_dir / "selected_model.json", selected_model_payload)
        _set_stage(
            context,
            stage_name="select_model",
            status="success",
            started_at=select_started,
            message="Latest usable shadow model selected.",
            artifact_pointers={
                "selected_model_path": str(selected_model_path),
                "model_path": str(selected_model_payload["model_path"]),
            },
        )
        inference_started = datetime.now().replace(microsecond=0).isoformat()
        update_stage_status(context, stage_name="inference", status="running", started_at=inference_started, message="Running daily inference with latest shadow model.")
        try:
            inference_artifacts = run_shadow_daily_inference(
                trade_date=context.trade_date,
                model_payload=selected_model_payload,
                output_dir=inference_dir,
                universe=DEFAULT_UNIVERSE,
            )
            predictions_path = Path(inference_artifacts.predictions_path)
            inference_summary_path = Path(inference_artifacts.inference_summary_path)
            _set_stage(
                context,
                stage_name="inference",
                status="success",
                started_at=inference_started,
                message="Daily inference completed successfully.",
                artifact_pointers={
                    "predictions_path": str(predictions_path),
                    "inference_summary_path": str(inference_summary_path),
                },
            )
            shadow_rebalance_path = _write_json(
                context.run_dir / "shadow_rebalance.json",
                {
                    "stage": "shadow_rebalance",
                    "status": "skipped",
                    "reason": "pr3a_inference_only",
                    "message": "Shadow rebalance remains intentionally skipped in PR3A.",
                },
            )
            _set_stage(
                context,
                stage_name="shadow_rebalance",
                status="skipped",
                message="Shadow rebalance intentionally skipped in PR3A.",
                artifact_pointers={"stage_output": str(shadow_rebalance_path)},
            )
            notes.append(f"Inference produced {inference_artifacts.prediction_count} predictions.")
        except (InferenceInvocationError, Exception) as exc:
            inference_summary_path = write_failed_inference_summary(
                trade_date=context.trade_date,
                model_payload=selected_model_payload,
                output_dir=inference_dir,
                error=str(exc),
            )
            _set_stage(
                context,
                stage_name="inference",
                status="failed",
                started_at=inference_started,
                message=f"Daily inference failed: {exc}",
                artifact_pointers={"inference_summary_path": str(inference_summary_path)},
            )
            shadow_rebalance_path = _write_json(
                context.run_dir / "shadow_rebalance.json",
                {
                    "stage": "shadow_rebalance",
                    "status": "skipped",
                    "reason": "inference_failed",
                    "message": str(exc),
                },
            )
            _set_stage(
                context,
                stage_name="shadow_rebalance",
                status="skipped",
                message="Shadow rebalance skipped because inference failed.",
                artifact_pointers={"stage_output": str(shadow_rebalance_path)},
            )
            notes.append(f"inference failed: {exc}")

    archive_payload = {
        "stage": "archive_report",
        "status": "success",
        "data_status_path": str(data_dir / "data_status.json"),
        "feature_status_path": str(feature_dir / "feature_status.json"),
        "selected_model_path": str(selected_model_path) if selected_model_path else "",
        "predictions_path": str(predictions_path) if predictions_path else None,
        "inference_summary_path": str(inference_summary_path) if inference_summary_path else None,
        "daily_summary_path": str(context.summary_path),
    }
    archive_path = _write_json(context.run_dir / "archive_report.json", archive_payload)
    _set_stage(
        context,
        stage_name="archive_report",
        status="success",
        message="Daily run artifacts archived.",
        artifact_pointers={
            "stage_output": str(archive_path),
            "data_status_path": archive_payload["data_status_path"],
            "feature_status_path": archive_payload["feature_status_path"],
            "selected_model_path": archive_payload["selected_model_path"],
            "predictions_path": archive_payload["predictions_path"],
            "inference_summary_path": archive_payload["inference_summary_path"],
            "daily_summary_path": archive_payload["daily_summary_path"],
        },
    )

    manifest = load_json(context.manifest_path)
    model_used = {
        "model_name": selected_model_payload.get("model_name", "") if selected_model_payload else "",
        "model_path": selected_model_payload.get("model_path", "") if selected_model_payload else "",
        "mainline_object_name": selected_model_payload.get("mainline_object_name", "") if selected_model_payload else "",
        "bundle_id": selected_model_payload.get("bundle_id", "") if selected_model_payload else "",
        "train_run_id": selected_model_payload.get("train_run_id", "") if selected_model_payload else "",
        "latest_model_pointer": latest_model_pointer,
    }
    inference_error = None
    if inference_summary_path and Path(inference_summary_path).exists():
        inference_summary_payload = load_json(inference_summary_path)
        inference_error = inference_summary_payload.get("error")
    summary = finalize_run(
        context,
        daily_summary={
            "trade_date": manifest["trade_date"],
            "run_id": context.run_id,
            "run_type": "shadow_daily",
            "data_status": manifest["stage_status"]["data_sync"]["status"],
            "feature_status": manifest["stage_status"]["feature_refresh"]["status"],
            "train_status": manifest["stage_status"]["maybe_retrain"]["status"],
            "model_used": model_used,
            "inference_status": manifest["stage_status"]["inference"]["status"],
            "rebalance_status": manifest["stage_status"]["shadow_rebalance"]["status"],
            "shadow_order_count": 0,
            "degradation_level": feature_status_payload.get("degradation_level", "unknown") if feature_status_payload else "unknown",
            "decision_status": manifest["stage_status"]["archive_report"]["status"],
            "error": inference_error or selected_model_payload.get("error") if selected_model_payload else None,
            "notes": notes or ["Daily shadow runner completed."],
        },
        notes=notes or ["Daily shadow runner completed."],
        fallback_summary={"used": False},
    )
    return {
        "run_id": context.run_id,
        "run_dir": str(context.run_dir),
        "manifest_path": str(context.manifest_path),
        "summary_path": str(context.summary_path),
        "overall_status": load_json(context.manifest_path)["overall_status"],
        "daily_summary": summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the shadow daily ops skeleton")
    parser.add_argument("--base-dir", default=str(PROJECT_ROOT), help="Base directory for runs/ and models/")
    parser.add_argument("--run-id", default=None, help="Optional run_id override")
    parser.add_argument("--triggered-by", default="manual", help="Trigger source label")
    args = parser.parse_args()

    result = run_shadow_daily(args.base_dir, run_id=args.run_id, triggered_by=args.triggered_by)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
