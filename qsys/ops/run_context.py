"""DailyRunContext and path-resolution helpers for the daily pipeline.

This module is part of the Protected Core / Runtime boundary layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    run_root: Path
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

    # Derived helpers — populated by the runner, not the CLI parser
    _output_resolved: bool = field(default=False, repr=False, compare=False)

    @property
    def output_dir_resolved(self) -> Path:
        """Return effective output directory, resolving defaults on first call."""
        if self.output_dir is not None:
            return self.output_dir
        return self.run_root / "experiments" / "alpha_v1_daily" / self.trade_date


def resolve_run_root(project_root: Path, debug_run: bool, output_dir: Path | None) -> Path:
    """Resolve the effective run root.

    *debug* → *output_dir* if given else a temp directory under *project_root*.
    *production* → *project_root* always.
    """
    if not debug_run:
        return project_root
    if output_dir is not None:
        return output_dir
    import tempfile

    tmp = tempfile.mkdtemp(prefix="alpha_v1_debug_", dir=str(project_root))
    return Path(tmp)
