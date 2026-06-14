"""Attempt management for the daily shadow pipeline (UC-8).

Provides pure helpers for attempt ID generation, active-attempt tracking,
and promotion-snapshot persistence.  No database backing — uses flat JSON
and YAML files under the existing run_root.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from qsys.common.io import read_json, write_json


def build_attempt_id(
    stage: str,
    run_mode: str,
    trade_date: str,
    strategy_id: str,
    seq: int,
) -> str:
    """Build a unique attempt ID string."""
    return f"{stage}_{run_mode}_{trade_date}_{strategy_id}_{seq:04d}"


def next_attempt_seq(run_root: Path) -> int:
    """Determine the next attempt sequence number for *run_root*.

    Reads the existing ``daily_manifest.json`` and returns
    ``max(existing_seq, 0) + 1``.  Returns 1 when the manifest
    does not exist or has no ``attempt_seq`` field.
    """
    path = run_root / "daily_manifest.json"
    data = read_json(path, {})
    if not isinstance(data, dict):
        return 1
    seq = data.get("attempt_seq", 0)
    if not isinstance(seq, int) or seq < 0:
        return 1
    return seq + 1


def active_attempt_path(run_root: Path) -> Path:
    """Path to the active-attempt pointer JSON."""
    return run_root / "active_attempt.json"


def read_active_attempt(run_root: Path) -> dict[str, Any] | None:
    """Read the active-attempt pointer, or return ``None``."""
    return read_json(active_attempt_path(run_root), None)


def write_active_attempt(run_root: Path, payload: dict[str, Any]) -> Path:
    """Write the active-attempt pointer JSON.  Returns the path.

    *payload* is expected to contain at minimum ``attempt_id``,
    ``attempt_seq``, ``stage``, ``run_mode``.
    """
    path = active_attempt_path(run_root)
    return write_json(path, payload)


def snapshot_promotion_pointer(
    run_root: Path,
    pointer_payload: dict[str, Any],
) -> Path:
    """Freeze a promotion pointer dict as ``promotion_snapshot.yaml``.

    *pointer_payload* is typically the return value of
    ``resolve_shadow_promotion()``, augmented with ``snapshot_taken_at``.

    Returns the path to the written YAML.
    """
    from datetime import datetime, timezone

    snapshot = dict(pointer_payload)
    snapshot["snapshot_taken_at"] = datetime.now(timezone.utc).isoformat()

    path = run_root / "promotion_snapshot.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(snapshot, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def resolve_promotion_snapshot(run_root: Path) -> dict[str, Any] | None:
    """Read the frozen promotion snapshot if it exists."""
    path = run_root / "promotion_snapshot.yaml"
    if not path.exists():
        return None
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data


def make_active_attempt_payload(
    attempt_id: str,
    attempt_seq: int,
    stage: str,
    run_mode: str,
    trade_date: str,
    strategy_id: str,
    *,
    supersedes_attempt_id: str | None = None,
    rerun_reason: str | None = None,
) -> dict[str, Any]:
    """Build a standard active-attempt payload dict."""
    return {
        "attempt_id": attempt_id,
        "attempt_seq": attempt_seq,
        "stage": stage,
        "run_mode": run_mode,
        "trade_date": trade_date,
        "strategy_id": strategy_id,
        "supersedes_attempt_id": supersedes_attempt_id,
        "rerun_reason": rerun_reason,
    }
