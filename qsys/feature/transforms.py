from __future__ import annotations

import numpy as np
import pandas as pd


PIT_CROSS_SECTION_COLUMN = "_pit_member"


def cross_section_mask(frame: pd.DataFrame) -> pd.Series:
    """Rows eligible to influence a cross-sectional statistic.

    Feature materialization may retain continuous listed history for every
    instrument so rolling features remain well-defined across index exits and
    re-entries.  When a PIT membership mask is present, only current members
    may enter same-date ranks, winsorization, means or z-scores.
    """

    if PIT_CROSS_SECTION_COLUMN not in frame.columns:
        return pd.Series(True, index=frame.index, dtype=bool)
    return frame[PIT_CROSS_SECTION_COLUMN].fillna(False).astype(bool)


def cross_section_transform(
    frame: pd.DataFrame,
    column: str,
    by: str | list[str],
    function,
) -> pd.Series:
    """Group transform over eligible PIT rows, aligned to the full frame."""

    eligible = cross_section_mask(frame)
    result = pd.Series(np.nan, index=frame.index, dtype="float64")
    if not eligible.any():
        return result
    subset = frame.loc[eligible]
    transformed = subset.groupby(by, group_keys=False)[column].transform(function)
    result.loc[subset.index] = pd.to_numeric(transformed, errors="coerce")
    return result


def cross_section_rank(
    frame: pd.DataFrame,
    column: str,
    by: str | list[str],
    *,
    method: str = "average",
) -> pd.Series:
    return cross_section_transform(
        frame,
        column,
        by,
        lambda values: values.rank(pct=True, method=method),
    )


def winsorize_series(series: pd.Series, lower: float = 0.01, upper: float = 0.99) -> pd.Series:
    # Pandas 2.3 may silently downcast ``float32`` values to integers when
    # ``clip`` receives scalar bounds.  Small-valued features such as Amihud
    # illiquidity (~1e-11 with amount denominated in yuan) then collapse to
    # all zeros.  Promote to float64 before computing and applying bounds so
    # cross-sectional variation survives inference and research alike.
    s = pd.to_numeric(series, errors="coerce").astype("float64")
    valid = s.dropna()
    if valid.empty:
        return s
    lo = valid.quantile(lower)
    hi = valid.quantile(upper)
    return s.clip(lower=lo, upper=hi)


def cs_zscore(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    std = s.std(ddof=0)
    if pd.isna(std) or std == 0:
        return s * 0
    return (s - s.mean()) / std


def cs_rank_pct(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    return s.rank(pct=True, method="average")


def apply_cross_sectional_standardization(df: pd.DataFrame, columns: list[str], date_col: str = "trade_date") -> pd.DataFrame:
    out = df.copy()
    for col in columns:
        if col not in out.columns:
            continue
        out[col] = cross_section_transform(
            out, col, date_col, winsorize_series
        )
        out[f"{col}_z"] = cross_section_transform(
            out, col, date_col, cs_zscore
        )
        out[f"{col}_rank"] = cross_section_transform(
            out, col, date_col, cs_rank_pct
        )
    return out


def rolling_zscore(series: pd.Series, window: int) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    mean = s.rolling(window).mean()
    std = s.rolling(window).std(ddof=0).replace(0, np.nan)
    return (s - mean) / std
