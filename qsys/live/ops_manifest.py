from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def build_manifest_path(report_dir: str | Path, execution_date: str) -> Path:
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    return report_dir / f"daily_ops_manifest_{execution_date}.json"


def load_manifest(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle) or {}


def _merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def update_manifest(
    *,
    report_dir: str | Path,
    execution_date: str,
    signal_date: str | None,
    stage: str,
    status: str,
    report_path: str | None = None,
    artifacts: dict[str, str] | None = None,
    data_status: dict[str, Any] | None = None,
    model_info: dict[str, Any] | None = None,
    blockers: list[str] | None = None,
    notes: list[str] | None = None,
    summary: dict[str, Any] | None = None,
) -> str:
    manifest_path = build_manifest_path(report_dir, execution_date)
    existing = load_manifest(manifest_path)

    stages = dict(existing.get("stages") or {})
    stages[stage] = {
        "status": status,
        "report_path": report_path,
        "updated_at": datetime.now().isoformat(),
        "summary": summary or {},
    }

    merged = _merge_dict(
        existing,
        {
            "execution_date": execution_date,
            "signal_date": signal_date,
            "updated_at": datetime.now().isoformat(),
            "stages": stages,
            "artifacts": dict(artifacts or {}),
            "data_status": dict(data_status or {}),
            "model_info": dict(model_info or {}),
        },
    )

    all_blockers = list(existing.get("blockers") or [])
    for item in blockers or []:
        if item not in all_blockers:
            all_blockers.append(item)
    merged["blockers"] = all_blockers

    all_notes = list(existing.get("notes") or [])
    for item in notes or []:
        if item not in all_notes:
            all_notes.append(item)
    merged["notes"] = all_notes

    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(merged, handle, indent=2, ensure_ascii=False)
    return str(manifest_path)
