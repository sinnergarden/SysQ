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

from qsys.ops import RETRAIN_STAGES, finalize_run, format_run_id, initialize_run, update_stage_status, write_latest_shadow_model
from qsys.ops.model_registry import (
    build_latest_shadow_model_payload,
    latest_shadow_model_is_usable,
    read_latest_shadow_model,
)
from qsys.ops.state import atomic_write_json, load_json
from qsys.ops.training import TrainingArtifacts, TrainingInvocationError, run_weekly_shadow_training
from qsys.research.mainline import MAINLINE_OBJECTS

DEFAULT_MAINLINE_OBJECT_NAME = "feature_173"


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _prepare_training_payload(run_id: str, trade_date: str, triggered_by: str, spec: Any) -> dict[str, object]:
    return {
        "stage": "prepare_training",
        "run_id": run_id,
        "trade_date": trade_date,
        "triggered_by": triggered_by,
        "mainline_object_name": spec.mainline_object_name,
        "bundle_id": spec.bundle_id,
        "model_name": spec.model_name,
    }


def _build_training_stage_payload(artifacts: TrainingArtifacts, latest_model_pointer: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "stage": "run_training",
        "status": "success",
        "mainline_object_name": artifacts.mainline_object_name,
        "bundle_id": artifacts.bundle_id,
        "model_name": artifacts.model_name,
        "model_path": artifacts.model_path,
        "train_run_id": artifacts.train_run_id,
        "trained_at": artifacts.trained_at,
        "artifact_pointers": {
            "training_report_path": artifacts.training_report_path,
            "training_summary_path": artifacts.training_summary_path,
            "config_snapshot_path": artifacts.config_snapshot_path,
            "decisions_path": artifacts.decisions_path,
            "model_path": artifacts.model_path,
            "latest_model_pointer_path": latest_model_pointer,
        },
    }
    return payload


def _build_pointer_stage_payload(payload: dict[str, str], pointer_path: str) -> dict[str, object]:
    return {
        "stage": "update_model_pointer",
        "status": "success",
        "model_name": payload["model_name"],
        "model_path": payload["model_path"],
        "train_run_id": payload["train_run_id"],
        "latest_model_pointer_path": pointer_path,
    }


def _build_archive_payload(manifest_path: Path, summary_path: Path, notes: list[str]) -> dict[str, object]:
    return {
        "stage": "archive_report",
        "status": "success",
        "manifest_path": str(manifest_path),
        "daily_summary_path": str(summary_path),
        "notes": notes,
    }


def run_shadow_retrain_weekly(
    base_dir: str | Path,
    *,
    run_id: str | None = None,
    triggered_by: str = "manual",
    mainline_object_name: str = DEFAULT_MAINLINE_OBJECT_NAME,
) -> dict[str, object]:
    now = datetime.now()
    resolved_run_id = run_id or format_run_id("weekly_retrain", now)
    base_dir = Path(base_dir)
    latest_model_pointer = str(base_dir / "models" / "latest_shadow_model.json")
    spec = MAINLINE_OBJECTS[mainline_object_name]
    previous_model_payload = read_latest_shadow_model(base_dir)
    previous_model_usable = latest_shadow_model_is_usable(base_dir, previous_model_payload)

    context = initialize_run(
        base_dir,
        run_type="weekly_retrain",
        run_id=resolved_run_id,
        mainline_object_name=spec.mainline_object_name,
        bundle_id=spec.bundle_id,
        model_name=spec.model_name,
        model_snapshot_path="",
        latest_model_pointer=latest_model_pointer,
        data_snapshot={
            "triggered_by": triggered_by,
            "mainline_object_name": spec.mainline_object_name,
            "bundle_id": spec.bundle_id,
        },
        fallback_summary={"used": False},
        notes=[f"Weekly retrain targets mainline object {spec.mainline_object_name}."],
    )

    notes: list[str] = []
    fallback_summary: dict[str, Any] = {"used": False}
    training_artifacts: TrainingArtifacts | None = None
    active_model_payload = previous_model_payload if previous_model_usable else None

    prepare_payload = _prepare_training_payload(context.run_id, context.trade_date, triggered_by, spec)
    prepare_path = _write_json(context.run_dir / "prepare_training.json", prepare_payload)
    started_at = datetime.now().replace(microsecond=0).isoformat()
    update_stage_status(
        context,
        stage_name="prepare_training",
        status="success",
        started_at=started_at,
        ended_at=started_at,
        message="Weekly retrain configuration prepared.",
        artifact_pointers={"stage_output": str(prepare_path)},
    )

    training_stage_started = datetime.now().replace(microsecond=0).isoformat()
    update_stage_status(
        context,
        stage_name="run_training",
        status="running",
        started_at=training_stage_started,
        message="Shadow weekly retrain started.",
    )

    try:
        training_artifacts = run_weekly_shadow_training(
            base_dir,
            mainline_object_name=spec.mainline_object_name,
            train_run_id=context.run_id,
        )
        training_payload = _build_training_stage_payload(training_artifacts, latest_model_pointer)
        training_stage_path = _write_json(context.run_dir / "run_training.json", training_payload)
        update_stage_status(
            context,
            stage_name="run_training",
            status="success",
            ended_at=datetime.now().replace(microsecond=0).isoformat(),
            message="Shadow weekly retrain completed successfully.",
            artifact_pointers={
                "stage_output": str(training_stage_path),
                "training_report_path": training_artifacts.training_report_path,
                "training_summary_path": training_artifacts.training_summary_path,
                "config_snapshot_path": training_artifacts.config_snapshot_path,
                "decisions_path": training_artifacts.decisions_path,
                "model_path": training_artifacts.model_path,
                "latest_model_pointer_path": latest_model_pointer,
            },
        )

        pointer_payload = build_latest_shadow_model_payload(
            model_name=training_artifacts.model_name,
            model_path=training_artifacts.model_path,
            mainline_object_name=training_artifacts.mainline_object_name,
            bundle_id=training_artifacts.bundle_id,
            train_run_id=context.run_id,
            trained_at=training_artifacts.trained_at,
            status="success",
        )
        pointer_path = write_latest_shadow_model(base_dir, pointer_payload)
        pointer_stage_path = _write_json(context.run_dir / "update_model_pointer.json", _build_pointer_stage_payload(pointer_payload, str(pointer_path)))
        update_stage_status(
            context,
            stage_name="update_model_pointer",
            status="success",
            started_at=datetime.now().replace(microsecond=0).isoformat(),
            ended_at=datetime.now().replace(microsecond=0).isoformat(),
            message="Latest shadow model pointer updated.",
            artifact_pointers={
                "stage_output": str(pointer_stage_path),
                "latest_model_pointer_path": str(pointer_path),
                "model_path": pointer_payload["model_path"],
            },
        )
        active_model_payload = pointer_payload
        notes.append(f"Updated latest shadow model pointer to {pointer_payload['model_name']}.")
    except TrainingInvocationError as exc:
        failure_payload = {
            "stage": "run_training",
            "status": "failed",
            "error": str(exc),
            "command": exc.command,
            "returncode": exc.returncode,
            "stdout_tail": exc.stdout_tail,
            "stderr_tail": exc.stderr_tail,
            "mainline_object_name": spec.mainline_object_name,
            "bundle_id": spec.bundle_id,
        }
        training_stage_path = _write_json(context.run_dir / "run_training.json", failure_payload)
        if previous_model_usable:
            fallback_summary = {
                "used": True,
                "reason": "training_failed_retained_previous_model",
                "retained_previous_model": previous_model_payload,
            }
            update_stage_status(
                context,
                stage_name="run_training",
                status="fallback",
                ended_at=datetime.now().replace(microsecond=0).isoformat(),
                message=f"Training failed; retained previous model. {exc}",
                artifact_pointers={
                    "stage_output": str(training_stage_path),
                    "latest_model_pointer_path": latest_model_pointer,
                    "retained_model_path": previous_model_payload["model_path"],
                    "command": exc.command,
                    "returncode": exc.returncode,
                },
            )
            pointer_stage_path = _write_json(
                context.run_dir / "update_model_pointer.json",
                {
                    "stage": "update_model_pointer",
                    "status": "fallback",
                    "message": "Retained previous model pointer after training failure.",
                    "latest_model_pointer_path": latest_model_pointer,
                    "retained_previous_model": previous_model_payload,
                },
            )
            update_stage_status(
                context,
                stage_name="update_model_pointer",
                status="fallback",
                started_at=datetime.now().replace(microsecond=0).isoformat(),
                ended_at=datetime.now().replace(microsecond=0).isoformat(),
                message="Retained previous model pointer after training failure.",
                artifact_pointers={
                    "stage_output": str(pointer_stage_path),
                    "latest_model_pointer_path": latest_model_pointer,
                    "retained_model_path": previous_model_payload["model_path"],
                },
            )
            active_model_payload = previous_model_payload
            notes.append(f"Training failed; retained previous model {previous_model_payload['model_name']}.")
        else:
            fallback_summary = {
                "used": False,
                "reason": "training_failed_without_previous_model",
            }
            update_stage_status(
                context,
                stage_name="run_training",
                status="failed",
                ended_at=datetime.now().replace(microsecond=0).isoformat(),
                message=f"Training failed and no previous model is available. {exc}",
                artifact_pointers={
                    "stage_output": str(training_stage_path),
                    "command": exc.command,
                    "returncode": exc.returncode,
                },
            )
            pointer_stage_path = _write_json(
                context.run_dir / "update_model_pointer.json",
                {
                    "stage": "update_model_pointer",
                    "status": "failed",
                    "message": "No latest shadow model pointer update because training failed.",
                    "latest_model_pointer_path": latest_model_pointer,
                },
            )
            update_stage_status(
                context,
                stage_name="update_model_pointer",
                status="failed",
                started_at=datetime.now().replace(microsecond=0).isoformat(),
                ended_at=datetime.now().replace(microsecond=0).isoformat(),
                message="Training failed and there is no previous model to retain.",
                artifact_pointers={"stage_output": str(pointer_stage_path)},
            )
            notes.append("Training failed and no previous shadow model was available to retain.")

    archive_status = "success"
    archive_message = "Weekly retrain manifest and summary archived."
    if fallback_summary.get("used"):
        archive_status = "fallback"
        archive_message = "Weekly retrain archived with fallback to retained previous model."
    elif active_model_payload is None:
        archive_status = "failed"
        archive_message = "Weekly retrain archived after hard training failure."

    archive_stage_path = _write_json(
        context.run_dir / "archive_report.json",
        _build_archive_payload(context.manifest_path, context.summary_path, notes),
    )
    update_stage_status(
        context,
        stage_name="archive_report",
        status=archive_status,
        started_at=datetime.now().replace(microsecond=0).isoformat(),
        ended_at=datetime.now().replace(microsecond=0).isoformat(),
        message=archive_message,
        artifact_pointers={
            "stage_output": str(archive_stage_path),
            "manifest_path": str(context.manifest_path),
            "daily_summary_path": str(context.summary_path),
        },
    )

    manifest = load_json(context.manifest_path)
    if active_model_payload:
        manifest["model_name"] = active_model_payload.get("model_name", manifest["model_name"])
        manifest["model_snapshot_path"] = active_model_payload.get("model_path", manifest["model_snapshot_path"])
    atomic_write_json(context.manifest_path, manifest)
    manifest = load_json(context.manifest_path)

    model_used = {
        "fallback": bool(fallback_summary.get("used")),
        "latest_model_pointer": manifest["latest_model_pointer"],
    }
    if active_model_payload:
        model_used.update({
            "model_name": active_model_payload.get("model_name"),
            "model_path": active_model_payload.get("model_path"),
            "train_run_id": active_model_payload.get("train_run_id"),
        })

    decision_status = manifest["stage_status"]["update_model_pointer"]["status"]
    summary_notes = list(notes)
    if fallback_summary.get("used"):
        summary_notes.append("Retained previous model after failed retrain.")
    elif active_model_payload is None:
        summary_notes.append("No usable model is available because retraining failed.")

    finalize_run(
        context,
        daily_summary={
            "trade_date": manifest["trade_date"],
            "run_id": context.run_id,
            "run_type": "shadow_retrain_weekly",
            "data_status": manifest["stage_status"]["prepare_training"]["status"],
            "feature_status": manifest["stage_status"]["prepare_training"]["status"],
            "train_status": manifest["stage_status"]["run_training"]["status"],
            "model_used": model_used,
            "inference_status": "skipped",
            "rebalance_status": "skipped",
            "shadow_order_count": 0,
            "degradation_level": "none" if not fallback_summary.get("used") else "fallback",
            "decision_status": decision_status,
            "notes": summary_notes,
        },
        notes=summary_notes,
        fallback_summary=fallback_summary,
    )

    manifest = load_json(context.manifest_path)
    return {
        "run_id": context.run_id,
        "run_dir": str(context.run_dir),
        "manifest_path": str(context.manifest_path),
        "summary_path": str(context.summary_path),
        "overall_status": manifest["overall_status"],
        "triggered_by": triggered_by,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the shadow weekly retrain workflow")
    parser.add_argument("--base-dir", default=str(PROJECT_ROOT), help="Project root for writing shadow ops artifacts")
    parser.add_argument("--run-id", help="Optional stable run_id for reruns")
    parser.add_argument("--triggered-by", default="manual", help="Who triggered this run")
    args = parser.parse_args()
    result = run_shadow_retrain_weekly(args.base_dir, run_id=args.run_id, triggered_by=args.triggered_by)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
