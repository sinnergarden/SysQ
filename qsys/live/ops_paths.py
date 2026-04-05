from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

DEFAULT_DAILY_ROOT = Path("daily")
DEFAULT_EXPERIMENTS_ROOT = Path("experiments")
DEFAULT_ACCOUNT_DB = Path("data/meta/real_account.db")
DEFAULT_DERIVED_ROOT = Path("data/derived")

_PRE_OPEN_SUBDIRS = {
    "plans": "plans",
    "order_intents": "order_intents",
    "signals": "signals",
    "reports": "reports",
    "manifests": "manifests",
}

_POST_CLOSE_SUBDIRS = {
    "reconciliation": "reconciliation",
    "snapshots": "snapshots",
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
    plans_dir: Path | None = None
    order_intents_dir: Path | None = None
    signals_dir: Path | None = None
    reconciliation_dir: Path | None = None
    snapshots_dir: Path | None = None


def build_daily_root(*, daily_root: str | Path = DEFAULT_DAILY_ROOT) -> Path:
    path = Path(daily_root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_day_root(execution_date: str, *, daily_root: str | Path = DEFAULT_DAILY_ROOT) -> Path:
    root = build_daily_root(daily_root=daily_root) / execution_date
    root.mkdir(parents=True, exist_ok=True)
    return root


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
        return DailyStagePaths(
            execution_date=execution_date,
            stage=stage,
            root=stage_root,
            reports_dir=dirs["reports"],
            manifests_dir=dirs["manifests"],
            plans_dir=dirs["plans"],
            order_intents_dir=dirs["order_intents"],
            signals_dir=dirs["signals"],
        )

    dirs = {name: stage_root / value for name, value in _POST_CLOSE_SUBDIRS.items()}
    return DailyStagePaths(
        execution_date=execution_date,
        stage=stage,
        root=stage_root,
        reports_dir=dirs["reports"],
        manifests_dir=dirs["manifests"],
        reconciliation_dir=dirs["reconciliation"],
        snapshots_dir=dirs["snapshots"],
    )


def resolve_account_db_path(*, project_root: str | Path) -> Path:
    canonical = Path(project_root) / DEFAULT_ACCOUNT_DB
    canonical.parent.mkdir(parents=True, exist_ok=True)
    return canonical


def resolve_experiments_root(*, project_root: str | Path) -> Path:
    canonical = Path(project_root) / DEFAULT_EXPERIMENTS_ROOT
    canonical.mkdir(parents=True, exist_ok=True)
    return canonical


def resolve_derived_root(*, project_root: str | Path) -> Path:
    canonical = Path(project_root) / DEFAULT_DERIVED_ROOT
    canonical.mkdir(parents=True, exist_ok=True)
    return canonical


def ensure_stage_subdir(base_dir: str | Path, subdir_name: str) -> Path:
    base_dir = Path(base_dir)
    if base_dir.name == subdir_name:
        return base_dir
    if base_dir.name in {"pre_open", "post_close"}:
        return base_dir / subdir_name
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


def find_plan_path_for_execution_date(
    *,
    execution_date: str,
    account_name: str,
    plan_dir: str | Path | None = None,
    daily_root: str | Path = DEFAULT_DAILY_ROOT,
) -> Path | None:
    candidates: list[Path] = []
    if plan_dir is not None:
        candidates.append(Path(plan_dir))
    pre_open_paths = build_stage_paths(execution_date, stage="pre_open", daily_root=daily_root)
    if pre_open_paths.plans_dir is not None:
        candidates.append(pre_open_paths.plans_dir)

    direct_match = f"plan_{execution_date}_{account_name}.csv"
    for directory in candidates:
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
    return files[:limit] if limit is not None else files
