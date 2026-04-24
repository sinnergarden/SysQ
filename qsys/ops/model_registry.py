from __future__ import annotations

from pathlib import Path

from .state import write_latest_pointer


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


def write_latest_shadow_model(base_dir: str | Path, payload: dict[str, str]) -> Path:
    base_dir = Path(base_dir)
    models_dir = base_dir / "models"
    return write_latest_pointer(models_dir / "latest_shadow_model.json", payload)
