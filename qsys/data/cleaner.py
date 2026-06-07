"""Data cleaning utilities for canonical feather SOT.

These functions are shared across the data pipeline — collector, storage,
and adapter — so that cleaning logic lives in one place and is not
duplicated in private methods.

The canonical SOT must be free of merge artifacts (``_x``/``_y`` suffixes),
duplicate columns, and non-canonical noise columns before downstream
consumers (adapter, qlib, model training) touch it.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

# ── Known merge-suffix pairs ─────────────────────────────────────────────
# Order: (canonical_name, [suffix candidates]) — first-found wins.
MERGE_SUFFIX_COALESCE: dict[str, list[str]] = {
    "close": ["close_x", "close_y"],
    "open": ["open_x", "open_y"],
    "high": ["high_x", "high_y"],
    "low": ["low_x", "low_y"],
    "vol": ["vol_x", "vol_y"],
    "amount": ["amount_x", "amount_y"],
    "high_limit": ["up_limit", "high_limit_x", "high_limit_y"],
    "low_limit": ["down_limit", "low_limit_x", "low_limit_y"],
    "volume": ["volume_x", "volume_y"],
    "factor": ["adj_factor", "factor_x", "factor_y"],
}

_COALESCE_CANDIDATES = {cand for cols in MERGE_SUFFIX_COALESCE.values() for cand in cols}


def coalesce_merge_suffix_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Coalesce merge-suffixed columns (``close_x``/``close_y`` → ``close``).

    For each canonical name in ``MERGE_SUFFIX_COALESCE``:
    1. Build a single Series from the target column (if exists) and each
       candidate suffix column via ``combine_first``.
    2. Drop the suffix columns.
    3. Ensure the target column is in the result.

    If the target column is missing from *df*, it is created as NaN and
    then filled from the suffix columns.

    Returns a DataFrame with a predictable set of columns — no merge
    artifacts, no unexpected suffix columns.
    """
    if df is None or df.empty:
        return df

    out = df.copy()

    for target, candidates in MERGE_SUFFIX_COALESCE.items():
        # Build combined series: start with existing target, then overlay candidates
        present_candidates = [c for c in candidates if c in out.columns]

        if target in out.columns:
            series = pd.to_numeric(out[target], errors="coerce").copy()
        else:
            series = pd.Series(index=out.index, dtype="float64")

        for cand in present_candidates:
            candidate_series = pd.to_numeric(out[cand], errors="coerce")
            series = series.combine_first(candidate_series)

        # Remove suffix columns from the output
        cols_to_drop = [c for c in present_candidates if c in out.columns]
        if cols_to_drop:
            out = out.drop(columns=cols_to_drop)

        # Insert the coalesced column (preserving original position if possible)
        out[target] = pd.to_numeric(series, errors="coerce")

    # Drop any remaining _x/_y/__src columns not in our known list
    stray = [
        c for c in out.columns
        if re.search(r"_(x|y|__src)$", c) and c not in _COALESCE_CANDIDATES
    ]
    if stray:
        out = out.drop(columns=stray)

    return out


def has_dirty_columns(df: pd.DataFrame) -> bool:
    """Return True if *df* contains any ``_x``/``_y``/``__src`` suffix columns.""" ""
    return any(re.search(r"_(x|y|__src)$", c) for c in df.columns)


# ── Column normalization ─────────────────────────────────────────────────

_COLUMN_RENAME_MAP: dict[str, str] = {
    "adj_factor": "factor",
    "up_limit": "high_limit",
    "down_limit": "low_limit",
    "vol": "volume",
}


def normalize_daily_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename common alias columns to canonical names.

    Renames (in-place style):
    - ``adj_factor`` → ``factor``
    - ``up_limit`` → ``high_limit``
    - ``down_limit`` → ``low_limit``
    - ``vol`` → ``volume`` (when ``volume`` does not already exist)

    Returns a new DataFrame with renamed columns.
    """
    if df is None or df.empty:
        return df

    rename = {}
    for old, new in _COLUMN_RENAME_MAP.items():
        if old in df.columns and (new not in df.columns or old == new):
            rename[old] = new

    if rename:
        return df.rename(columns=rename)
    return df


# ── Non-canonical column dropping ────────────────────────────────────────

# Columns that are not part of the canonical feather schema
# These are temporary/merge-only columns that should not persist in canonical data.
_NON_CANONICAL_COLUMNS: set[str] = {
    # Merge artifacts from daily_basic merging with daily
    "close_x", "close_y",
    "open_x", "open_y",
    "high_x", "high_y",
    "low_x", "low_y",
    "vol_x", "vol_y",
    "amount_x", "amount_y",
    # Fields that are temporally valid only during the merge step
    "ann_date",
    "end_date",
}

_KEEP_COLUMNS_BASELINE: set[str] = {
    "ts_code", "trade_date",
    "open", "high", "low", "close", "pre_close", "change", "pct_chg",
    "vol", "amount",
    "turnover_rate", "turnover_rate_f", "volume_ratio",
    "pe", "pe_ttm", "pb", "ps", "ps_ttm",
    "dv_ratio", "dv_ttm",
    "total_share", "float_share", "free_share",
    "total_mv", "circ_mv",
    "adj_factor",
    "up_limit", "down_limit",
    "paused",
    # moneyflow
    "buy_sm_amount", "buy_md_amount", "buy_lg_amount", "buy_elg_amount",
    "sell_sm_amount", "sell_md_amount", "sell_lg_amount", "sell_elg_amount",
    "net_mf_amount", "big_inflow", "net_inflow",
    # PIT financials
    "net_income", "revenue", "oper_cost",
    "total_assets", "equity", "total_cur_assets", "total_cur_liab",
    "roe", "roe_ttm", "roe_waa", "grossprofit_margin",
    "debt_to_assets", "current_ratio",
    "op_cashflow",
    "q_dt_profit", "dt_netprofit_yoy", "q_gr_yoy", "profit_to_gr", "net_profit_margin",
    # margin
    "margin_balance", "margin_buy_amount", "margin_repay_amount",
    "margin_total_balance", "lend_volume", "lend_sell_volume", "lend_repay_volume",
    # dragon-tiger (batch 1)
    "exalter", "buy", "sell", "net_buy", "name", "reason",
    "buyer_sum", "seller_sum", "net_amount",
}


def drop_non_canonical_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Drop columns that are not part of the canonical schema.

    This is conservative — it only drops known-non-canonical columns
    (merge artifacts, stale ``ann_date``/``end_date`` that are only
    meaningful during the merge step), not unknown columns.
    """
    if df is None or df.empty:
        return df

    to_drop = [c for c in df.columns if c in _NON_CANONICAL_COLUMNS and c not in _KEEP_COLUMNS_BASELINE]
    if to_drop:
        return df.drop(columns=to_drop)
    return df
