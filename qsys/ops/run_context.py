"""DailyRunContext and path-resolution helpers for the daily pipeline.

This module is part of the Protected Core / Runtime boundary layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class DailyRunContext:
    """Immutable context bundle for a single daily pipeline invocation.

    Carries all parameters extracted from CLI arguments and environment
    so that runner methods and downstream helpers do not need argparse
    or shell-variable access.
    """

    trade_date: str
    mode: str  # "preopen" | "postclose" | "train"
    run_root: Path  # actual artifact directory (resolved by resolve_run_root)
    project_root: Path
    strategy_id: str
    account_id: str

    # Optional overrides
    data_date: str | None = None  # reference_date for inference prices
    ledger_db_path: str | None = None
    output_dir: Path | None = None  # --output-dir override

    # Flags
    debug_run: bool = False
    force_rerun: bool = False
    notify_only: bool = False
    no_notify: bool = False

    # Audit trail
    reason: str | None = None

    @property
    def output_dir_resolved(self) -> Path:
        """Alias for run_root — the resolved artifact directory."""
        return self.run_root


def resolve_run_root(
    project_root: Path,
    strategy_id: str,
    trade_date: str,
    *,
    debug_run: bool = False,
    output_dir: Path | None = None,
) -> Path:
    """Resolve run_root for a daily pipeline invocation.

    Production
        ``project_root/experiments/<strategy_id>_daily/<trade_date>``

    Debug with --output-dir
        The given *output_dir*.

    Debug without --output-dir
        ``project_root/experiments/debug/<strategy_id>/<trade_date>_<timestamp>``
    """
    if output_dir is not None:
        return output_dir
    if debug_run:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return project_root / "experiments" / "debug" / strategy_id / f"{trade_date}_{ts}"
    return project_root / "experiments" / f"{strategy_id}_daily" / trade_date
