"""Artifact I/O helpers for the daily alpha_v1 pipeline.

Extracted from scripts/run_alpha_v1_daily.py for Phase 1.5 boundary refactor.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from qsys.common.io import write_json
from qsys.research.manifest import with_standard_metadata


def save_run_meta(
    run_root: Path,
    trade_date: str,
    mode: str,
    data_date: str | None = None,
    debug_run: bool = False,
    reason: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Write run_meta.json with run metadata."""
    meta: dict[str, Any] = {
        "trade_date": trade_date,
        "mode": mode,
        "reference_date": data_date,
        "debug_run": debug_run,
        "reason": reason,
        "ts": datetime.now().isoformat(),
    }
    if extra:
        meta.update(extra)
    run_root.mkdir(parents=True, exist_ok=True)
    write_json(run_root / "run_meta.json", meta)


def write_daily_manifest(
    run_root: Path,
    *,
    trade_date: str,
    stage: str,
    run_mode: str = "shadow",
    strategy_id: str,
    account_id: str,
    candidate_id: str | None = None,
    signal_run_id: str | None = None,
    strategy_config_id: str | None = None,
    backtest_id: str | None = None,
    promotion_pointer_path: str | None = None,
    triggered_by: str = "manual",
    debug_run: bool = False,
    stage_status: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Write UC-8/UC-9 daily run manifest with identity lineage.

    The manifest serves as the audit record for a daily shadow or production
    run.  It records the promotion pointer, candidate, signal, and strategy
    config that produced the run, satisfying the UC-8/UC-9 guardrails for
    ID chain auditability.

    Parameters
    ----------
    stage: preopen | postclose | train
    run_mode: shadow | production
    """
    manifest: dict[str, Any] = {
        "artifact_type": "daily_run",
        "trade_date": trade_date,
        "stage": stage,
        "run_mode": run_mode,
        "strategy_id": strategy_id,
        "account_id": account_id,
    }
    if candidate_id:
        manifest["candidate_id"] = candidate_id
    if signal_run_id:
        manifest["signal_run_id"] = signal_run_id
    if strategy_config_id:
        manifest["strategy_config_id"] = strategy_config_id
    if backtest_id:
        manifest["backtest_id"] = backtest_id
    if promotion_pointer_path:
        manifest["promotion_pointer_path"] = promotion_pointer_path
    manifest["triggered_by"] = triggered_by
    if stage_status:
        manifest["stage_status"] = stage_status
    if extra:
        manifest.update(extra)
    manifest["debug_run"] = debug_run

    manifest = with_standard_metadata(manifest)
    run_root.mkdir(parents=True, exist_ok=True)
    write_json(run_root / "daily_manifest.json", manifest)


def archive_execution(run_root: Path) -> None:
    """Archive existing execution/ dir before force-rerun."""
    exec_dir = run_root / "execution"
    if not exec_dir.exists():
        return
    archive_dir = run_root / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.move(str(exec_dir), str(archive_dir / f"execution_{ts}"))
    print(f"  📦 已有执行产物已存档: archive/execution_{ts}")
