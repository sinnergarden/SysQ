from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

ALLOWED_STATUSES = {"pending", "running", "success", "failed", "skipped", "fallback"}

STATUS_PRIORITY = {
    "failed": 5,
    "fallback": 4,
    "running": 3,
    "pending": 2,
    "success": 1,
    "skipped": 0,
}


def validate_status(status: str) -> str:
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"Unsupported status: {status}")
    return status


def ensure_directory(path: str | Path) -> Path:
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def atomic_write_json(path: str | Path, payload: dict[str, Any]) -> Path:
    path = Path(path)
    ensure_directory(path.parent)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temp_name = handle.name
    os.replace(temp_name, path)
    return path


def load_json(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def summarize_overall_status(stage_statuses: list[str]) -> str:
    if not stage_statuses:
        return "pending"
    for status in stage_statuses:
        validate_status(status)
    return max(stage_statuses, key=lambda item: STATUS_PRIORITY[item])


def write_latest_pointer(path: str | Path, payload: dict[str, Any]) -> Path:
    return atomic_write_json(path, payload)
