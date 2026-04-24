from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .state import atomic_write_json, ensure_directory, load_json, summarize_overall_status, validate_status, write_latest_pointer

DAILY_STAGES = [
    "data_sync",
    "feature_refresh",
    "maybe_retrain",
    "select_model",
    "inference",
    "shadow_rebalance",
    "archive_report",
]

RETRAIN_STAGES = [
    "prepare_training",
    "run_training",
    "update_model_pointer",
    "archive_report",
]

RUN_TYPES = {
    "daily": {
        "run_type_value": "shadow_daily",
        "stage_names": DAILY_STAGES,
        "run_prefix": "shadow",
        "latest_pointer_name": "latest_shadow_daily.json",
        "summary_file_name": "daily_summary.json",
    },
    "weekly_retrain": {
        "run_type_value": "shadow_retrain_weekly",
        "stage_names": RETRAIN_STAGES,
        "run_prefix": "shadow_retrain",
        "latest_pointer_name": "latest_shadow_retrain.json",
        "summary_file_name": "daily_summary.json",
    },
}


@dataclass(frozen=True)
class ShadowRunContext:
    run_type: str
    run_type_value: str
    run_id: str
    trade_date: str
    run_dir: Path
    manifest_path: Path
    summary_path: Path
    latest_pointer_path: Path
    stage_names: list[str]


def utc_now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def format_run_id(run_type: str, dt: datetime | None = None) -> str:
    dt = dt or datetime.now()
    if run_type not in RUN_TYPES:
        raise ValueError(f"Unsupported run_type: {run_type}")
    prefix = RUN_TYPES[run_type]["run_prefix"]
    return f"{prefix}_{dt.strftime('%Y-%m-%d_%H%M%S')}"


def build_run_context(base_dir: str | Path, *, run_type: str, run_id: str) -> ShadowRunContext:
    if run_type not in RUN_TYPES:
        raise ValueError(f"Unsupported run_type: {run_type}")
    trade_date = _extract_run_date(run_id, run_type=run_type)
    run_dir = Path(base_dir) / "runs" / trade_date / run_id
    latest_pointer_path = Path(base_dir) / "runs" / RUN_TYPES[run_type]["latest_pointer_name"]
    return ShadowRunContext(
        run_type=run_type,
        run_type_value=RUN_TYPES[run_type]["run_type_value"],
        run_id=run_id,
        trade_date=trade_date,
        run_dir=run_dir,
        manifest_path=run_dir / "manifest.json",
        summary_path=run_dir / RUN_TYPES[run_type]["summary_file_name"],
        latest_pointer_path=latest_pointer_path,
        stage_names=list(RUN_TYPES[run_type]["stage_names"]),
    )


def initialize_run(
    base_dir: str | Path,
    *,
    run_type: str,
    run_id: str,
    trade_date: str | None = None,
    mainline_object_name: str = "stub_mainline",
    bundle_id: str = "stub_bundle",
    model_name: str = "stub_model",
    model_snapshot_path: str = "",
    latest_model_pointer: str = "models/latest_shadow_model.json",
    data_snapshot: dict[str, Any] | None = None,
    fallback_summary: dict[str, Any] | None = None,
    notes: list[str] | None = None,
) -> ShadowRunContext:
    context = build_run_context(base_dir, run_type=run_type, run_id=run_id)
    ensure_directory(context.run_dir)
    started_at = utc_now_text()
    manifest_payload = {
        "run_id": context.run_id,
        "run_type": context.run_type_value,
        "trade_date": trade_date or context.trade_date,
        "mainline_object_name": mainline_object_name,
        "bundle_id": bundle_id,
        "model_name": model_name,
        "model_snapshot_path": model_snapshot_path,
        "latest_model_pointer": latest_model_pointer,
        "stage_status": _build_empty_stages(context.stage_names),
        "overall_status": "pending",
        "data_snapshot": dict(data_snapshot or {}),
        "fallback_summary": dict(fallback_summary or {}),
        "started_at": started_at,
        "ended_at": None,
        "notes": list(notes or []),
    }
    atomic_write_json(context.manifest_path, manifest_payload)
    return context


def update_stage_status(
    context: ShadowRunContext,
    *,
    stage_name: str,
    status: str,
    started_at: str | None = None,
    ended_at: str | None = None,
    message: str | None = None,
    artifact_pointers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validate_status(status)
    manifest_payload = load_json(context.manifest_path)
    _assert_stage_name(stage_name, context.stage_names)
    stage_payload = dict(manifest_payload["stage_status"][stage_name])
    if started_at is not None:
        stage_payload["started_at"] = started_at
    if ended_at is not None:
        stage_payload["ended_at"] = ended_at
    stage_payload["status"] = status
    stage_payload["message"] = message or stage_payload.get("message") or ""
    stage_payload["artifact_pointers"] = dict(artifact_pointers or stage_payload.get("artifact_pointers") or {})
    manifest_payload["stage_status"][stage_name] = stage_payload
    manifest_payload["overall_status"] = summarize_overall_status(
        [item["status"] for item in manifest_payload["stage_status"].values()]
    )
    atomic_write_json(context.manifest_path, manifest_payload)
    return manifest_payload


def finalize_run(
    context: ShadowRunContext,
    *,
    daily_summary: dict[str, Any],
    ended_at: str | None = None,
    notes: list[str] | None = None,
    fallback_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest_payload = load_json(context.manifest_path)
    stage_statuses = [manifest_payload["stage_status"][name]["status"] for name in context.stage_names]
    overall_status = summarize_overall_status(stage_statuses)
    manifest_payload["ended_at"] = ended_at or utc_now_text()
    manifest_payload["overall_status"] = overall_status
    manifest_payload["notes"] = list(notes or manifest_payload.get("notes") or [])
    manifest_payload["fallback_summary"] = dict(fallback_summary or manifest_payload.get("fallback_summary") or {})
    atomic_write_json(context.manifest_path, manifest_payload)

    summary_payload = dict(daily_summary)
    summary_payload["run_id"] = context.run_id
    summary_payload["run_type"] = context.run_type_value
    summary_payload["trade_date"] = manifest_payload["trade_date"]
    atomic_write_json(context.summary_path, summary_payload)

    latest_payload = {
        "run_id": context.run_id,
        "trade_date": manifest_payload["trade_date"],
        "overall_status": overall_status,
        "manifest_path": str(context.manifest_path),
        "updated_at": manifest_payload["ended_at"],
    }
    if context.run_type == "daily":
        latest_payload["daily_summary_path"] = str(context.summary_path)
    write_latest_pointer(context.latest_pointer_path, latest_payload)
    return summary_payload


def _build_empty_stages(stage_names: list[str]) -> dict[str, dict[str, Any]]:
    return {
        stage_name: {
            "status": "pending",
            "started_at": None,
            "ended_at": None,
            "message": "",
            "artifact_pointers": {},
        }
        for stage_name in stage_names
    }


def _extract_run_date(run_id: str, *, run_type: str) -> str:
    prefix = RUN_TYPES[run_type]["run_prefix"] + "_"
    if not run_id.startswith(prefix):
        raise ValueError(f"run_id does not match {run_type}: {run_id}")
    remainder = run_id[len(prefix):]
    parts = remainder.split("_")
    if len(parts) != 2:
        raise ValueError(f"run_id format is invalid: {run_id}")
    return parts[0]


def _assert_stage_name(stage_name: str, stage_names: list[str]) -> None:
    if stage_name not in stage_names:
        raise ValueError(f"Unsupported stage_name: {stage_name}")
