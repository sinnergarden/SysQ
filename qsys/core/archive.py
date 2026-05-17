from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.config import cfg
from qsys.core.contracts import RunManifest
from qsys.utils.logger import log


# ── Run archive root ──────────────────────────────────────────────────

_RUNS_ROOT: Path | None = None  # lazy-init from cfg


def _runs_root() -> Path:
    global _RUNS_ROOT
    if _RUNS_ROOT is None:
        root = Path(str(cfg.get_path("root"))) / "runs"
        root.mkdir(parents=True, exist_ok=True)
        _RUNS_ROOT = root
    return _RUNS_ROOT


def resolve_run_dir(run_id: str) -> Path:
    return _runs_root() / run_id


def next_run_seq(trade_date: str, mode: str, account_id: str) -> int:
    """Return the next available seq number for a run with the given prefix.

    Scans existing archives under *runs_root()/_{trade_date}_{mode}_{account_id}_NNN*
    and returns one past the highest seq.  Returns 1 when no prior archive exists.
    """
    prefix = f"{trade_date.replace('-', '')}_{mode}_{account_id}_"
    root = _runs_root()
    max_seq = 0
    if root.exists():
        for child in root.iterdir():
            if child.is_dir() and child.name.startswith(prefix):
                try:
                    seq_part = child.name.rsplit("_", 1)[-1]
                    max_seq = max(max_seq, int(seq_part))
                except (ValueError, IndexError):
                    pass
    return max_seq + 1


# ── Directory initialisation ──────────────────────────────────────────

_RUN_DIR_LAYOUT = [
    "inputs",
    "outputs",
]


def init_run_dir(run_id: str) -> Path:
    """Create the run archive directory skeleton and return its path."""
    run_dir = resolve_run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    for sub in _RUN_DIR_LAYOUT:
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
    log.info("Initialised run archive: %s", run_dir)
    return run_dir


# ── Manifest ──────────────────────────────────────────────────────────


def write_manifest(run_dir: Path, manifest: RunManifest) -> Path:
    """Write manifest.json into *run_dir* and return its path."""
    path = run_dir / "manifest.json"
    path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    log.info("Wrote manifest: %s", path)
    return path


# ── Input / output helpers ────────────────────────────────────────────

_INPUT_WRITERS: dict[str, str] = {
    "signal_basket": ".csv",
}
_OUTPUT_WRITERS: dict[str, str] = {
    "plan": ".csv",
    "order_intents": ".json",
    "execution_results": ".json",
    "reconciliation_report": ".json",
}


def _save_csv(path: Path, data: Any) -> None:
    if isinstance(data, pd.DataFrame):
        data.to_csv(path, index=False)
    elif isinstance(data, list) and data and isinstance(data[0], dict):
        pd.DataFrame(data).to_csv(path, index=False)
    else:
        path.write_text(str(data), encoding="utf-8")


def _save_json(path: Path, data: Any) -> None:
    if isinstance(data, (dict, list)):
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    else:
        path.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")


def save_input(run_dir: Path, name: str, data: Any) -> Path:
    """Save an input artifact (signal_basket, account_snapshot_before, ...)."""
    sub = run_dir / "inputs"
    sub.mkdir(parents=True, exist_ok=True)
    ext = _INPUT_WRITERS.get(name, ".json")
    path = sub / f"{name}{ext}"

    if ext == ".csv":
        _save_csv(path, data)
    else:
        _save_json(path, data)

    log.debug("Saved input: %s", path)
    return path


def save_output(run_dir: Path, name: str, data: Any) -> Path:
    """Save an output artifact (plan, order_intents, execution_results, ...)."""
    sub = run_dir / "outputs"
    sub.mkdir(parents=True, exist_ok=True)
    ext = _OUTPUT_WRITERS.get(name, ".json")
    path = sub / f"{name}{ext}"

    if ext == ".csv":
        _save_csv(path, data)
    else:
        _save_json(path, data)

    log.debug("Saved output: %s", path)
    return path


# ── Summary ───────────────────────────────────────────────────────────


def write_summary(run_dir: Path, summary: dict[str, Any]) -> Path:
    """Write (or overwrite) summary.json in the run root."""
    path = run_dir / "summary.json"
    path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    log.info("Wrote summary: %s", path)
    return path


def read_summary(run_dir: Path) -> dict[str, Any] | None:
    """Read summary.json from a run archive, or return None."""
    path = run_dir / "summary.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Failed to read summary at %s: %s", path, exc)
        return None


# ── Discovery ─────────────────────────────────────────────────────────


def discover_runs(
    trade_date: str,
    mode: str | None = None,
    account_id: str | None = None,
) -> list[Path]:
    """Return run archive directories matching the given criteria.

    Parameters
    ----------
    trade_date : str
        The execution date (YYYY-MM-DD) to filter by.
    mode : str, optional
        Filter by run mode (e.g. "paper").
    account_id : str, optional
        Filter by account ID (e.g. "shadow").

    Results are sorted so that the most recent seq appears last
    (callers can take ``[-1]`` for the latest run of that date).
    """
    root = _runs_root()
    if not root.exists():
        return []
    prefix = trade_date.replace("-", "")
    if mode:
        prefix += f"_{mode}"
    if account_id:
        prefix += f"_{account_id}"
    prefix += "_"
    candidates: list[Path] = []
    for child in root.iterdir():
        if child.is_dir() and child.name.startswith(prefix):
            candidates.append(child)
    candidates.sort(key=lambda p: p.name)
    return candidates
