from __future__ import annotations

from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def resolve_data_root() -> Path:
    try:
        from qsys.config import cfg

        root = cfg.get_path("root")
        if root is not None:
            return Path(str(root))
    except Exception:
        pass
    return PROJECT_ROOT / "data"


def ensure_datetime_column(df: pd.DataFrame, column: str = "trade_date") -> pd.DataFrame:
    out = df.copy()
    if column in out.columns:
        out[column] = pd.to_datetime(out[column])
    return out


def ensure_panel_sorted(df: pd.DataFrame) -> pd.DataFrame:
    out = ensure_datetime_column(df)
    sort_cols = [col for col in ["trade_date", "ts_code"] if col in out.columns]
    if sort_cols:
        out = out.sort_values(sort_cols).reset_index(drop=True)
    return out


def coalesce_numeric_columns(df: pd.DataFrame, target: str, candidates: list[str]) -> pd.DataFrame:
    out = df.copy()
    series = pd.to_numeric(out[target], errors="coerce") if target in out.columns else None
    for candidate in candidates:
        if candidate in out.columns:
            candidate_series = pd.to_numeric(out[candidate], errors="coerce")
            series = candidate_series if series is None else series.combine_first(candidate_series)
    if series is None:
        series = pd.Series(index=out.index, dtype=float)
    out[target] = series
    return out


def prepare_research_panel(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for target, candidates in {
        "close": ["close_x", "close_y"],
        "open": ["open_x", "open_y"],
        "high": ["high_x", "high_y"],
        "low": ["low_x", "low_y"],
        "high_limit": ["up_limit"],
        "low_limit": ["down_limit"],
        "volume": ["vol"],
    }.items():
        out = coalesce_numeric_columns(out, target, candidates)
    return ensure_panel_sorted(out)


def merge_date_series(df: pd.DataFrame, series: pd.Series, column_name: str) -> pd.DataFrame:
    out = df.copy()
    renamed = series.rename(column_name)
    return out.merge(renamed, left_on="trade_date", right_index=True, how="left")
