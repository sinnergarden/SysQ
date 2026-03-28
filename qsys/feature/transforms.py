from __future__ import annotations

import numpy as np
import pandas as pd


def winsorize_series(series: pd.Series, lower: float = 0.01, upper: float = 0.99) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
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
        out[col] = out.groupby(date_col, group_keys=False)[col].apply(winsorize_series)
        out[f"{col}_z"] = out.groupby(date_col, group_keys=False)[col].apply(cs_zscore)
        out[f"{col}_rank"] = out.groupby(date_col, group_keys=False)[col].apply(cs_rank_pct)
    return out


def rolling_zscore(series: pd.Series, window: int) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    mean = s.rolling(window).mean()
    std = s.rolling(window).std(ddof=0).replace(0, np.nan)
    return (s - mean) / std
