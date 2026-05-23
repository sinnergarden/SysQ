"""COMMITTING / COMMITTED marker management.

Extracted from scripts/run_alpha_v1_daily.py for Phase 1.5 boundary refactor.
"""

from __future__ import annotations

from pathlib import Path


def committing_marker(run_root: Path) -> Path:
    """Path to the COMMITTING marker file."""
    return run_root / "execution" / "COMMITTING"


def committed_marker(run_root: Path) -> Path:
    """Path to the COMMITTED marker file."""
    return run_root / "execution" / "COMMITTED"


def is_execution_committed(run_root: Path) -> bool:
    """Check whether execution has been committed for this run."""
    return committed_marker(run_root).exists()


def cleanup_committing(run_root: Path) -> None:
    """Remove COMMITTING marker on failure so retry is possible."""
    p = committing_marker(run_root)
    if p.exists():
        p.unlink()
        print(f"  🧹 COMMITTING marker cleaned up")
