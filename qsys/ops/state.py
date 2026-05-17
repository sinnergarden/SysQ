from __future__ import annotations

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


def summarize_overall_status(stage_statuses: list[str]) -> str:
    if not stage_statuses:
        return "pending"
    for status in stage_statuses:
        validate_status(status)
    return max(stage_statuses, key=lambda item: STATUS_PRIORITY[item])


def write_latest_pointer(path: str | Path, payload: dict[str, Any]) -> Path:
    return atomic_write_json(path, payload)


# Re-export canonical I/O for backward compat
from qsys.utils.json_io import atomic_write_json, load_json  # noqa: F401, E402
