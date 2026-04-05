from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

DEFAULT_DAILY_ROOT = Path("daily")
DEFAULT_EXPERIMENTS_ROOT = Path("experiments")
DEFAULT_ACCOUNT_DB = Path("data/meta/real_account.db")
LEGACY_ACCOUNT_DB = Path("data/real_account.db")
LEGACY_EXPERIMENTS_ROOT = Path("data/experiments")
LEGACY_DAILY_ARCHIVE_ROOT = Path("daily/ops")
LEGACY_REPORT_ROOT = Path("data/reports")
LEGACY_SIGNAL_ROOT = Path("data")

_PRE_OPEN_SUBDIRS = {
    "plans": "plans",
    "templates": "templates",
    "order_intents": "order_intents",
    "signals": "signals",
    "diagnostics": "diagnostics",
    "reports": "reports",
    "manifests": "manifests",
}

_POST_CLOSE_SUBDIRS = {
    "reconciliation": "reconciliation",
    "snapshots": "snapshots",
    "diagnostics": "diagnostics",
    "reports": "reports",
    "manifests": "manifests",
}


@dataclass(frozen=True)
class DailyStagePaths:
    execution_date: str
    stage: str
    root: Path
    reports_dir: Path
    manifests_dir: Path
    diagnostics_dir: Path
    plans_dir: Path | None = None
    templates_dir: Path | None = None
    order_intents_dir: Path | None = None
    signals_dir: Path | None = None
    reconciliation_dir: Path | None = None
    snapshots_dir: Path | None = None


@dataclass(frozen=True)
class LegacyMove:
    source: Path
    destination: Path
    status: str


def build_daily_root(*, daily_root: str | Path = DEFAULT_DAILY_ROOT) -> Path:
    path = Path(daily_root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_day_root(execution_date: str, *, daily_root: str | Path = DEFAULT_DAILY_ROOT) -> Path:
    root = build_daily_root(daily_root=daily_root) / execution_date
    root.mkdir(parents=True, exist_ok=True)
    return root


def build_daily_summary_dir(execution_date: str, *, daily_root: str | Path = DEFAULT_DAILY_ROOT) -> Path:
    summary_dir = build_day_root(execution_date, daily_root=daily_root) / "summary"
    summary_dir.mkdir(parents=True, exist_ok=True)
    return summary_dir


def build_stage_paths(
    execution_date: str,
    *,
    stage: str,
    daily_root: str | Path = DEFAULT_DAILY_ROOT,
) -> DailyStagePaths:
    if stage not in {"pre_open", "post_close"}:
        raise ValueError(f"Unsupported stage: {stage}")

    stage_root = build_day_root(execution_date, daily_root=daily_root) / stage
    stage_root.mkdir(parents=True, exist_ok=True)

    if stage == "pre_open":
        dirs = {name: stage_root / value for name, value in _PRE_OPEN_SUBDIRS.items()}
        for path in dirs.values():
            path.mkdir(parents=True, exist_ok=True)
        return DailyStagePaths(
            execution_date=execution_date,
            stage=stage,
            root=stage_root,
            reports_dir=dirs["reports"],
            manifests_dir=dirs["manifests"],
            diagnostics_dir=dirs["diagnostics"],
            plans_dir=dirs["plans"],
            templates_dir=dirs["templates"],
            order_intents_dir=dirs["order_intents"],
            signals_dir=dirs["signals"],
        )

    dirs = {name: stage_root / value for name, value in _POST_CLOSE_SUBDIRS.items()}
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return DailyStagePaths(
        execution_date=execution_date,
        stage=stage,
        root=stage_root,
        reports_dir=dirs["reports"],
        manifests_dir=dirs["manifests"],
        diagnostics_dir=dirs["diagnostics"],
        reconciliation_dir=dirs["reconciliation"],
        snapshots_dir=dirs["snapshots"],
    )


def resolve_account_db_path(*, project_root: str | Path) -> Path:
    project_root = Path(project_root)
    canonical = project_root / DEFAULT_ACCOUNT_DB
    legacy = project_root / LEGACY_ACCOUNT_DB
    if legacy.exists() and not canonical.exists():
        legacy.parent.mkdir(parents=True, exist_ok=True)
        return legacy
    canonical.parent.mkdir(parents=True, exist_ok=True)
    return canonical


def resolve_experiments_root(*, project_root: str | Path) -> Path:
    project_root = Path(project_root)
    canonical = project_root / DEFAULT_EXPERIMENTS_ROOT
    legacy = project_root / LEGACY_EXPERIMENTS_ROOT
    if legacy.exists() and not canonical.exists():
        return legacy
    canonical.mkdir(parents=True, exist_ok=True)
    return canonical


def ensure_stage_subdir(base_dir: str | Path, subdir_name: str) -> Path:
    base_dir = Path(base_dir)
    if base_dir.name == subdir_name:
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir
    if base_dir.name in {"pre_open", "post_close"}:
        target = base_dir / subdir_name
        target.mkdir(parents=True, exist_ok=True)
        return target
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def _read_plan_execution_date(path: Path) -> str | None:
    try:
        frame = pd.read_csv(path, nrows=1)
    except Exception:
        return None
    if frame.empty:
        return None
    if "execution_date" in frame.columns:
        value = frame.iloc[0]["execution_date"]
        if pd.notna(value):
            return pd.Timestamp(value).strftime("%Y-%m-%d")
    return None


def _read_signal_basket_execution_date(path: Path) -> str | None:
    try:
        frame = pd.read_csv(path, nrows=1)
    except Exception:
        return None
    if frame.empty:
        return None
    if "execution_date" in frame.columns:
        value = frame.iloc[0]["execution_date"]
        if pd.notna(value):
            return pd.Timestamp(value).strftime("%Y-%m-%d")
    if "signal_date" in frame.columns:
        value = frame.iloc[0]["signal_date"]
        if pd.notna(value):
            return pd.Timestamp(value).strftime("%Y-%m-%d")
    return None


def _load_json(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle) or {}


def _candidate_plan_dirs(
    *,
    execution_date: str,
    plan_dir: str | Path | None,
    daily_root: str | Path = DEFAULT_DAILY_ROOT,
    legacy_root: str | Path = LEGACY_SIGNAL_ROOT,
) -> list[Path]:
    candidates: list[Path] = []
    if plan_dir is not None:
        candidates.append(Path(plan_dir))
    candidates.append(build_stage_paths(execution_date, stage="pre_open", daily_root=daily_root).plans_dir or Path())
    candidates.append(Path(legacy_root))
    ordered: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        ordered.append(path)
    return ordered


def find_plan_path_for_execution_date(
    *,
    execution_date: str,
    account_name: str,
    plan_dir: str | Path | None = None,
    daily_root: str | Path = DEFAULT_DAILY_ROOT,
    legacy_root: str | Path = LEGACY_SIGNAL_ROOT,
) -> Path | None:
    direct_match = f"plan_{execution_date}_{account_name}.csv"
    for directory in _candidate_plan_dirs(
        execution_date=execution_date,
        plan_dir=plan_dir,
        daily_root=daily_root,
        legacy_root=legacy_root,
    ):
        if not directory.exists() or not directory.is_dir():
            continue

        direct_path = directory / direct_match
        if direct_path.exists():
            return direct_path

        for candidate in sorted(directory.glob(f"plan_*_{account_name}.csv"), reverse=True):
            candidate_execution_date = _read_plan_execution_date(candidate)
            if candidate_execution_date == execution_date:
                return candidate
    return None


def list_signal_basket_candidates(signal_root: str | Path, *, limit: int | None = None) -> list[Path]:
    signal_root = Path(signal_root)
    if not signal_root.exists():
        return []

    files = sorted(signal_root.rglob("signal_basket_*.csv"), reverse=True)
    legacy_files = sorted(signal_root.glob("signal_basket_*.csv"), reverse=True)
    combined: list[Path] = []
    seen: set[Path] = set()
    for path in [*files, *legacy_files]:
        if path in seen:
            continue
        seen.add(path)
        combined.append(path)
    return combined[:limit] if limit is not None else combined


def _safe_move(source: Path, destination: Path) -> LegacyMove:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if source.resolve() == destination.resolve():
            return LegacyMove(source=source, destination=destination, status="unchanged")
        return LegacyMove(source=source, destination=destination, status="skipped_exists")
    shutil.move(str(source), str(destination))
    return LegacyMove(source=source, destination=destination, status="moved")


def _migrate_root_snapshot_index(*, project_root: Path, daily_root: Path) -> list[LegacyMove]:
    legacy_root = project_root / LEGACY_DAILY_ARCHIVE_ROOT
    if not legacy_root.exists():
        return []

    moves: list[LegacyMove] = []
    for source in sorted(legacy_root.glob("*/snapshot_index.json")):
        execution_date = source.parent.name
        destination = daily_root / execution_date / "snapshot_index.json"
        moves.append(_safe_move(source, destination))
    return moves


def migrate_legacy_daily_artifacts(
    *,
    project_root: str | Path,
    data_root: str | Path = "data",
    daily_root: str | Path = DEFAULT_DAILY_ROOT,
) -> list[LegacyMove]:
    project_root = Path(project_root)
    data_root = project_root / Path(data_root)
    daily_root = project_root / Path(daily_root)
    daily_root.mkdir(parents=True, exist_ok=True)

    moves: list[LegacyMove] = []

    for source in sorted(data_root.glob("plan_*_*.csv")):
        execution_date = _read_plan_execution_date(source)
        if execution_date is None:
            continue
        destination = build_stage_paths(execution_date, stage="pre_open", daily_root=daily_root).plans_dir / source.name
        moves.append(_safe_move(source, destination))

    for source in sorted(data_root.glob("real_sync_template_*_*.csv")):
        execution_date = _read_plan_execution_date(source)
        if execution_date is None:
            continue
        destination = build_stage_paths(execution_date, stage="pre_open", daily_root=daily_root).templates_dir / source.name
        moves.append(_safe_move(source, destination))

    for source in sorted(data_root.glob("order_intents_*_*.json")):
        stem = source.stem
        parts = stem.split("_")
        if len(parts) < 4:
            continue
        execution_date = parts[2]
        destination = build_stage_paths(execution_date, stage="pre_open", daily_root=daily_root).order_intents_dir / source.name
        moves.append(_safe_move(source, destination))

    for source in sorted(data_root.glob("signal_basket_*.csv")):
        execution_date = _read_signal_basket_execution_date(source)
        if execution_date is None:
            continue
        destination = build_stage_paths(execution_date, stage="pre_open", daily_root=daily_root).signals_dir / source.name
        moves.append(_safe_move(source, destination))

    report_root = data_root / "reports"
    if report_root.exists():
        for source in sorted(report_root.glob("daily_ops_*.json")):
            payload = _load_json(source)
            execution_date = str(payload.get("execution_date") or "").strip()
            workflow = str(payload.get("workflow") or "")
            if not execution_date:
                continue
            stage = "post_close" if workflow == "daily_ops_post_close" else "pre_open"
            destination = build_stage_paths(execution_date, stage=stage, daily_root=daily_root).reports_dir / source.name
            moves.append(_safe_move(source, destination))

        for source in sorted(report_root.glob("daily_ops_manifest_*.json")):
            payload = _load_json(source)
            execution_date = str(payload.get("execution_date") or source.stem.rsplit("_", 1)[-1])
            stage = "post_close" if "post_close" in (payload.get("stages") or {}) and "pre_open" not in (payload.get("stages") or {}) else "pre_open"
            destination = build_stage_paths(execution_date, stage=stage, daily_root=daily_root).manifests_dir / source.name
            moves.append(_safe_move(source, destination))

    moves.extend(_migrate_root_snapshot_index(project_root=project_root, daily_root=daily_root))
    return moves


def describe_moves(moves: Iterable[LegacyMove]) -> dict[str, int]:
    summary = {"moved": 0, "unchanged": 0, "skipped_exists": 0}
    for move in moves:
        summary.setdefault(move.status, 0)
        summary[move.status] += 1
    return summary
