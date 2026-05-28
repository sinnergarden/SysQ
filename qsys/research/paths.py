"""Research root path resolution and artifact directory conventions."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

_RESERVED_RUN_NAMES = frozenset({
    "shared", "archive", "_archive", "_shared", ".shared",
})

_RESEARCH_ROOT: Path | None = None


def set_research_root(path: str | Path) -> None:
    """Set the global research root directory (overrides default)."""
    global _RESEARCH_ROOT
    _RESEARCH_ROOT = Path(path).resolve()


def get_research_root(project_root: Path | None = None) -> Path:
    """Return the research artifacts root directory.

    Default: ``<project_root>/research/artifacts/``.
    """
    if _RESEARCH_ROOT is not None:
        return _RESEARCH_ROOT
    base = project_root or Path.cwd()
    return base / "research" / "artifacts"


def _validate_run_name(name: str) -> None:
    if not name or len(name) > 128:
        raise ValueError(f"run_name must be 1-128 chars, got {len(name)}")
    if not name.replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"run_name must be alphanumeric with - and _ only: {name!r}")


def resolve_run_dir(
    run_name: str,
    *,
    project_root: Path | None = None,
    exists_ok: bool = False,
) -> Path:
    """Resolve and create a research run directory.

    Parameters
    ----------
    run_name:
        Human-readable identifier for the run (e.g. ``alpha_v1_20260501``).
        Must be alphanumeric with ``-`` and ``_`` only, 1-128 characters.
    project_root:
        Project root.  Uses ``get_research_root`` when ``None``.
    exists_ok:
        If ``False`` (default), raise ``FileExistsError`` when the directory
        already exists.

    Returns
    -------
    Path
        ``<research_root>/<run_name>/``, created on disk.
    """
    _validate_run_name(run_name)
    if run_name.startswith("_") or run_name in _RESERVED_RUN_NAMES:
        raise ValueError(f"run_name {run_name!r} is reserved")

    run_dir = get_research_root(project_root) / run_name

    if run_dir.exists():
        if exists_ok:
            return run_dir
        raise FileExistsError(f"Run directory already exists: {run_dir}")

    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def resolve_artifact_path(
    run_dir: Path,
    kind: Literal["manifest", "signals", "labels", "predictions"],
) -> Path:
    """Return the subdirectory path for a given artifact kind within a run.

    Conventions::

        <run_dir>/          run_dir (from resolve_run_dir)
            manifest.json   top-level manifest
            signals/        signal parquet files
            labels/         label parquet files
            predictions/    prediction parquet files
    """
    mapping: dict[str, str] = {
        "manifest": "",
        "signals": "signals",
        "labels": "labels",
        "predictions": "predictions",
    }
    sub = mapping.get(kind, kind)
    if not sub:
        return run_dir
    return run_dir / sub


def make_run_id() -> str:
    """Generate a unique run ID with timestamp prefix.

    Format: ``<YYYYMMDD>_<16-char-hex>``.
    """
    ts = datetime.now().strftime("%Y%m%d")
    uid = uuid.uuid4().hex[:16]
    return f"{ts}_{uid}"
