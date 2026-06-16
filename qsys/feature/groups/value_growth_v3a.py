"""v3-a feature groups: margin financing + shareholder concentration.

PIT note
--------
Margin fields (from qlib bin) are daily snapshots — no PIT concern.
Shareholder fields require ``ann_date``-based ``merge_asof`` (see
``_load_holder_data``).  Using ``end_date`` instead of ``ann_date``
introduces lookahead — handled by the loader.

Usage
-----
Called from ``build_phase1_features`` via the feature flags path.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ── Shared helpers ──────────────────────────────────────────────────────


def _safe_div(num: pd.Series, den: pd.Series) -> pd.Series:
    """Divide, replacing 0-denominator with NaN."""
    return num / den.replace(0, np.nan)


def _zscore(s: pd.Series) -> pd.Series:
    """Cross-sectional zscore (per-trade-date)."""
    std = s.std(ddof=0)
    if pd.isna(std) or std < 1e-12:
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / std


def _clip_inf(s: pd.Series) -> pd.Series:
    return s.replace([np.inf, -np.inf], np.nan)


# ── Margin financing features ───────────────────────────────────────────


def build_margin_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build margin / securities-lending features.

    All source fields are daily snapshots from qlib bin.
    NaN means the stock is not margin-eligible.
    """
    out = df.copy()

    # ── Eligibility flag ────────────────────────────────────────────
    if "margin_balance" in out.columns:
        out["margin_eligible"] = out["margin_balance"].notna().astype(float)

    # ── Balance to float MV ─────────────────────────────────────────
    _mv_col = None
    for c in ("circ_mv", "total_mv"):
        if c in out.columns:
            _mv_col = c
            break
    if "margin_balance" in out.columns and _mv_col:
        out["margin_balance_to_float_mv"] = _clip_inf(
            _safe_div(out["margin_balance"], out[_mv_col])
        )

    # ── Balance change (20d / 60d) ──────────────────────────────────
    if "margin_balance" in out.columns and "ts_code" in out.columns:
        _grp = out.groupby("ts_code")["margin_balance"]
        for n, label in [(20, "20d"), (60, "60d")]:
            prev = _grp.shift(n)
            out[f"margin_balance_chg_{label}"] = _clip_inf(
                _safe_div(out["margin_balance"], prev) - 1
            )

    # ── Buy intensity: margin buy / total amount ────────────────────
    if {"margin_buy_amount", "amount"}.issubset(out.columns):
        _buy = out.groupby("ts_code")["margin_buy_amount"].transform(
            lambda s: s.rolling(20, min_periods=5).sum()
        )
        _amt = out.groupby("ts_code")["amount"].transform(
            lambda s: s.rolling(20, min_periods=5).sum()
        )
        out["margin_buy_intensity_20d"] = _clip_inf(_safe_div(_buy, _amt))

    # ── Repay / buy ratio ──────────────────────────────────────────
    if {"margin_repay_amount", "margin_buy_amount"}.issubset(out.columns):
        _repay = out.groupby("ts_code")["margin_repay_amount"].transform(
            lambda s: s.rolling(20, min_periods=5).sum()
        )
        _buy_ = out.groupby("ts_code")["margin_buy_amount"].transform(
            lambda s: s.rolling(20, min_periods=5).sum()
        )
        out["margin_repay_to_buy_20d"] = _clip_inf(_safe_div(_repay, _buy_))

    # ── Composite: crowding ─────────────────────────────────────────
    # BUGFIX: use string column names, not Series from out.get()
    if {"margin_balance_to_float_mv", "margin_balance_chg_60d", "trade_date"}.issubset(out.columns):
        _za = out.groupby("trade_date")["margin_balance_to_float_mv"].transform(
            lambda s: _zscore(s.fillna(0))
        )
        _zb = out.groupby("trade_date")["margin_balance_chg_60d"].transform(
            lambda s: _zscore(s.fillna(0))
        )
        out["margin_crowding_score"] = _za + _zb

    # ── Composite: trend confirm ────────────────────────────────────
    if {"margin_balance_chg_60d", "ret_60d", "trade_date"}.issubset(out.columns):
        _zbc = out.groupby("trade_date")["margin_balance_chg_60d"].transform(
            lambda s: _zscore(s.fillna(0))
        )
        _zr60 = out.groupby("trade_date")["ret_60d"].transform(
            lambda s: _zscore(s.fillna(0))
        )
        out["margin_trend_confirm_score"] = _zbc * _zr60.clip(lower=0)

    # ── Composite: overheat ─────────────────────────────────────────
    if {"margin_crowding_score", "ret_120d", "trade_date"}.issubset(out.columns):
        _zmc = out.groupby("trade_date")["margin_crowding_score"].transform(
            lambda s: _zscore(s.fillna(0))
        )
        _zr120 = out.groupby("trade_date")["ret_120d"].transform(
            lambda s: _zscore(s.fillna(0))
        )
        out["margin_overheat_risk_score"] = _zmc * _zr120.clip(lower=0)

    # Clean up intermediates
    for c in list(out.columns):
        if c.startswith("_"):
            out = out.drop(columns=[c])

    return out


# ── Shareholder data loader ──────────────────────────────────────────────


def load_shareholder_data(df: pd.DataFrame, holder_path: str = "data/canonical/holder_num.parquet") -> pd.DataFrame:
    """Load shareholder data from parquet and PIT-merge via ``ann_date`` merge_asof.

    ``df`` must have ``trade_date`` (parsed) and ``instrument`` / ``ts_code`` columns.
    Returns *df* with ``holder_num``, ``top10_holder_ratio``, ``holder_real_ann_date``,
    ``top10_real_ann_date``, ``holder_num_prev_ann``, ``holder_num_prev2_ann``,
    ``top10_holder_ratio_prev_ann``, ``total_share`` appended.

    Changes (qoq/2q) are computed at the announcement level before merging,
    then propagated to daily frequency via merge_asof.
    """
    out = df.copy()
    td_key = "trade_date" if "trade_date" in out.columns else None
    if td_key is None:
        return out

    out["_dt"] = pd.to_datetime(out[td_key])
    out["_inst"] = out.get("instrument", out.get("ts_code", "")).str.upper()
    # _row_id tracks original row order so we can restore it after merge_asof
    out["_row_id"] = np.arange(len(out))

    # ── helper: merge_asof one right-side table, preserving row order ──
    def _merge_pit(left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
        """Merge ``right`` into ``left`` by ``_inst`` / ``_dt`` (backward).

        Both sides must have ``_dt``, ``_inst``.
        ``right`` should already have been prepared with the columns needed
        (including a ``_real_ann_dt`` if it should be tracked).  The merge
        result is joined back to ``left`` by ``_row_id`` to restore order.
        """
        left_sorted = left.sort_values("_dt")[["_row_id", "_dt", "_inst"]].reset_index(drop=True)
        # right must be globally sorted by _dt for merge_asof
        right_sorted = right.sort_values("_dt").reset_index(drop=True)
        merged = pd.merge_asof(
            left_sorted,
            right_sorted,
            on="_dt", by="_inst", direction="backward",
        )
        # Restore original order via _row_id
        merged = merged.sort_values("_row_id").reset_index(drop=True)
        merged = merged.drop(columns=["_dt", "_inst", "_row_id"])
        return merged

    # ── helper: sort in-place + compute announcement-level shifts ─────
    def _add_prev_cols(ann_df: pd.DataFrame, col: str, prev_col: str, prev2_col: str) -> pd.DataFrame:
        """Sort ``ann_df`` by (inst, _dt) in-place and add shift-1/shift-2 columns."""
        ann_df = ann_df.sort_values(["inst", "_dt"]).reset_index(drop=True)
        ann_df[prev_col] = ann_df.groupby("inst")[col].shift(1)
        ann_df[prev2_col] = ann_df.groupby("inst")[col].shift(2)
        return ann_df

    # ── holder_num + prev_ann values ──────────────────────────────────
    try:
        hdf = pd.read_parquet(holder_path)
        hdf["_dt"] = pd.to_datetime(hdf["ann_date"])
        hdf["_real_ann_dt"] = hdf["_dt"]  # keep real ann_date before rename
        hdf["inst"] = hdf["inst"].str.upper()
        hdf = _add_prev_cols(hdf, "holder_num", "holder_num_prev_ann", "holder_num_prev2_ann")
        hdf["_inst"] = hdf["inst"]

        right = hdf[["_inst", "_dt", "_real_ann_dt", "holder_num",
                      "holder_num_prev_ann", "holder_num_prev2_ann"]]
        result = _merge_pit(out[["_row_id", "_dt", "_inst"]], right)
        out["holder_num"] = result["holder_num"]
        out["holder_num_prev_ann"] = result["holder_num_prev_ann"]
        out["holder_num_prev2_ann"] = result["holder_num_prev2_ann"]
        out["holder_real_ann_date"] = result["_real_ann_dt"].dt.strftime("%Y-%m-%d")
    except Exception:
        out["holder_num"] = np.nan
        out["holder_num_prev_ann"] = np.nan
        out["holder_num_prev2_ann"] = np.nan
        out["holder_real_ann_date"] = np.nan

    # total_share as proxy if available
    if "total_share" not in out.columns:
        out["total_share"] = np.nan

    # ── top10_holder_ratio + prev_ann ─────────────────────────────────
    top10_path = holder_path.replace("holder_num", "top10_holder_ratio")
    try:
        tdf = pd.read_parquet(top10_path)
        tdf["_dt"] = pd.to_datetime(tdf["ann_date"])
        tdf["_real_ann_dt"] = tdf["_dt"]
        tdf["inst"] = tdf["inst"].str.upper()
        tdf = _add_prev_cols(tdf, "top10_ratio", "top10_holder_ratio_prev_ann", "__unused")
        tdf["_inst"] = tdf["inst"]

        right = tdf[["_inst", "_dt", "_real_ann_dt", "top10_ratio",
                      "top10_holder_ratio_prev_ann"]]
        result = _merge_pit(out[["_row_id", "_dt", "_inst"]], right)
        out["top10_holder_ratio"] = result["top10_ratio"]
        out["top10_holder_ratio_prev_ann"] = result["top10_holder_ratio_prev_ann"]
        out["top10_real_ann_date"] = result["_real_ann_dt"].dt.strftime("%Y-%m-%d")
    except Exception:
        out["top10_holder_ratio"] = np.nan
        out["top10_holder_ratio_prev_ann"] = np.nan
        out["top10_real_ann_date"] = np.nan

    out = out.drop(columns=["_dt", "_inst", "_row_id"], errors="ignore")
    return out


# ── Shareholder concentration features ──────────────────────────────────


def build_shareholder_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build shareholder / concentration features.

    Uses announcement-level previous values (holder_num_prev_ann,
    holder_num_prev2_ann, top10_holder_ratio_prev_ann) that were
    pre-computed by ``load_shareholder_data``.  This ensures qoq changes
    reflect real announcement-to-announcement transitions rather than
    day-over-day noise.
    """
    out = df.copy()

    # ── Holder num change (announcement-level qoq/2q) ──────────────
    if "holder_num" in out.columns and "holder_num_prev_ann" in out.columns:
        out["holder_num_chg_qoq"] = _clip_inf(
            _safe_div(out["holder_num"], out["holder_num_prev_ann"]) - 1
        )
    if "holder_num" in out.columns and "holder_num_prev2_ann" in out.columns:
        out["holder_num_chg_2q"] = _clip_inf(
            _safe_div(out["holder_num"], out["holder_num_prev2_ann"]) - 1
        )

    # ── Avg shares per holder (announcement-level qoq) ─────────────
    if {"holder_num", "total_share", "holder_num_prev_ann"}.issubset(out.columns):
        _avg = out["total_share"] / out["holder_num"].replace(0, np.nan)
        _avg_prev = out["total_share"] / out["holder_num_prev_ann"].replace(0, np.nan)
        out["avg_shares_per_holder"] = _avg
        out["avg_shares_per_holder_chg_qoq"] = _clip_inf(_safe_div(_avg, _avg_prev) - 1)

    # ── Top10 holder ratio (announcement-level qoq) ────────────────
    if "top10_holder_ratio" in out.columns and "top10_holder_ratio_prev_ann" in out.columns:
        out["top10_holder_ratio_chg_qoq"] = _clip_inf(
            _safe_div(out["top10_holder_ratio"], out["top10_holder_ratio_prev_ann"]) - 1
        )

    # ── Datetime columns for stale-days ─────────────────────────────
    if "holder_real_ann_date" in out.columns and "trade_date" in out.columns:
        _ha = pd.to_datetime(out["holder_real_ann_date"], errors="coerce")
        _td = pd.to_datetime(out["trade_date"], errors="coerce")
        out["holder_num_stale_days"] = (_td - _ha).dt.days.clip(lower=0)
    if "top10_real_ann_date" in out.columns and "trade_date" in out.columns:
        _ta = pd.to_datetime(out["top10_real_ann_date"], errors="coerce")
        _td = pd.to_datetime(out["trade_date"], errors="coerce")
        out["top10_holder_stale_days"] = (_td - _ta).dt.days.clip(lower=0)

    # ── Composite: concentration score ─────────────────────────────
    _parts = []
    if "holder_num_chg_qoq" in out.columns:
        _parts.append(out.groupby("trade_date")["holder_num_chg_qoq"].transform(
            lambda s: -_zscore(s.fillna(0))
        ))
    if "avg_shares_per_holder_chg_qoq" in out.columns:
        _parts.append(out.groupby("trade_date")["avg_shares_per_holder_chg_qoq"].transform(
            lambda s: _zscore(s.fillna(0))
        ))
    if "top10_holder_ratio_chg_qoq" in out.columns:
        _parts.append(out.groupby("trade_date")["top10_holder_ratio_chg_qoq"].transform(
            lambda s: _zscore(s.fillna(0))
        ))
    if _parts and len(_parts) >= 2:
        out["holder_concentration_score"] = sum(_parts) / len(_parts)

    # ── Composite: squeeze score ───────────────────────────────────
    # BUGFIX: use string column names
    if {"holder_num_chg_qoq", "ret_60d", "trade_date"}.issubset(out.columns):
        _zhn = out.groupby("trade_date")["holder_num_chg_qoq"].transform(
            lambda s: -_zscore(s.fillna(0))
        )
        _zr60 = out.groupby("trade_date")["ret_60d"].transform(
            lambda s: _zscore(s.fillna(0))
        )
        out["holder_squeeze_score"] = _zhn * _zr60.clip(lower=0)

    # ── Composite: price confirm ───────────────────────────────────
    # BUGFIX: use string column names
    if {"holder_concentration_score", "ret_120d", "trade_date"}.issubset(out.columns):
        _zhc = out.groupby("trade_date")["holder_concentration_score"].transform(
            lambda s: _zscore(s.fillna(0))
        )
        _zr120 = out.groupby("trade_date")["ret_120d"].transform(
            lambda s: _zscore(s.fillna(0))
        )
        out["holder_price_confirm_score"] = _zhc * _zr120.clip(lower=0)

    for c in list(out.columns):
        if c.startswith("_"):
            out = out.drop(columns=[c])

    return out


# ── Combined builder ────────────────────────────────────────────────────


def build_v3a_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build all v3-a features (margin + shareholder)."""
    out = build_margin_features(df)
    out = build_shareholder_features(out)
    return out
