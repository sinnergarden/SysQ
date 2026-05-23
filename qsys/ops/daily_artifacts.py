"""Artifact I/O helpers for the daily alpha_v1 pipeline.

Extracted from scripts/run_alpha_v1_daily.py for Phase 1.5 boundary refactor.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from qsys.common.io import write_json


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
