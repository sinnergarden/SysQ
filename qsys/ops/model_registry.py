from __future__ import annotations

from pathlib import Path
from typing import Any

from .state import load_json, write_latest_pointer

REQUIRED_LATEST_SHADOW_MODEL_FIELDS = {
    "model_name",
    "model_path",
    "mainline_object_name",
    "bundle_id",
    "train_run_id",
    "trained_at",
    "status",
}


def build_latest_shadow_model_payload(
    *,
    model_name: str,
    model_path: str,
    mainline_object_name: str,
    bundle_id: str,
    train_run_id: str,
    trained_at: str,
    status: str,
) -> dict[str, str]:
    return {
        "model_name": model_name,
        "model_path": model_path,
        "mainline_object_name": mainline_object_name,
        "bundle_id": bundle_id,
        "train_run_id": train_run_id,
        "trained_at": trained_at,
        "status": status,
    }


def latest_shadow_model_path(base_dir: str | Path) -> Path:
    return Path(base_dir) / "models" / "latest_shadow_model.json"


def write_latest_shadow_model(base_dir: str | Path, payload: dict[str, str]) -> Path:
    return write_latest_pointer(latest_shadow_model_path(base_dir), payload)


def read_latest_shadow_model(base_dir: str | Path) -> dict[str, Any]:
    payload = load_json(latest_shadow_model_path(base_dir))
    if not payload:
        return {}
    missing = sorted(REQUIRED_LATEST_SHADOW_MODEL_FIELDS.difference(payload))
    if missing:
        return {}
    return payload


def latest_shadow_model_is_usable(base_dir: str | Path, payload: dict[str, Any] | None = None) -> bool:
    payload = payload or read_latest_shadow_model(base_dir)
    if not payload:
        return False
    model_path = payload.get("model_path")
    if not model_path:
        return False
    return Path(model_path).exists()
