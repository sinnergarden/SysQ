"""Minimal-pull DataFrame merge helpers extracted from TushareCollector.

These are pure functions with no dependency on ``self`` or instance state.
"""

from __future__ import annotations

import pandas as pd


def merge_trade_frames(left: pd.DataFrame, right: pd.DataFrame, *, keys: list[str]) -> pd.DataFrame:
    """Left-merge two DataFrames, coalescing overlapping columns with ``combine_first``.

    Overlapping columns in *right* (those not in *keys*) that also exist in *left*
    are merged with a ``__src`` suffix pattern, then combined with
    ``left[col].combine_first(right[col])`` so the left value wins when non-null.
    """
    if left is None or left.empty:
        return right.copy() if right is not None else pd.DataFrame()
    if right is None or right.empty:
        return left

    overlapping = [col for col in right.columns if col in left.columns and col not in keys]
    merged = pd.merge(left, right, on=keys, how="left", suffixes=("", "__src"))
    for col in overlapping:
        src_col = f"{col}__src"
        if src_col not in merged.columns:
            continue
        merged[col] = merged[col].combine_first(merged[src_col])
        merged = merged.drop(columns=[src_col])
    return merged


def prepare_financial_frame(df: pd.DataFrame, value_cols) -> pd.DataFrame:
    """Validate, clean, and sort a financial DataFrame for PIT merge.

    Drops rows missing ``ann_date`` (strict PIT: without announcement date
    the data is unusable).  Sorts by ``[ts_code, ann_date, end_date]``.
    Returns only ``[ts_code, ann_date, end_date] + value_cols``.
    """
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    if "ann_date" not in df.columns:
        return pd.DataFrame()

    if "end_date" not in df.columns:
        df["end_date"] = None

    # Clean date fields
    df["ann_date"] = df["ann_date"].replace("", None)
    df["end_date"] = df["end_date"].replace("", None)

    # Strict PIT: drop rows without announcement date
    df = df[df["ann_date"].notna()]

    df["_ann_dt"] = pd.to_datetime(df["ann_date"], errors="coerce")
    df["_end_dt"] = pd.to_datetime(df["end_date"], errors="coerce")

    df = df.sort_values(["ts_code", "_ann_dt", "_end_dt"])

    cols = ["ts_code", "ann_date", "end_date"] + list(value_cols)
    cols = [c for c in cols if c in df.columns]
    return df[cols]
