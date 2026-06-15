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
When ``enable_fundamental_context_features`` is True, v3-a features
are also computed if the relevant qlib fields are present.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qsys.feature.transforms import rolling_zscore


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
    _a = out.get("margin_balance_to_float_mv", None)
    _b = out.get("margin_balance_chg_60d", None)
    if _a is not None:
        # need trade_date for cs zscore
        if "trade_date" in out.columns:
            _za = out.groupby("trade_date")[_a].transform(
                lambda s: _zscore(s.fillna(0))
            )
            _zb = (
                out.groupby("trade_date")[_b].transform(lambda s: _zscore(s.fillna(0)))
                if _b is not None
                else 0
            )
            out["margin_crowding_score"] = _za + _zb

    # ── Composite: trend confirm ────────────────────────────────────
    _bc = out.get("margin_balance_chg_60d", None)
    _r60 = out.get("ret_60d", None)
    if _bc is not None and _r60 is not None and "trade_date" in out.columns:
        _zbc = out.groupby("trade_date")[_bc].transform(
            lambda s: _zscore(s.fillna(0))
        )
        _zr60 = out.groupby("trade_date")[_r60].transform(
            lambda s: _zscore(s.fillna(0))
        )
        out["margin_trend_confirm_score"] = _zbc * _zr60.clip(lower=0)

    # ── Composite: overheat ─────────────────────────────────────────
    _mc = out.get("margin_crowding_score", None)
    _r120 = out.get("ret_120d", None)
    if _mc is not None and _r120 is not None and "trade_date" in out.columns:
        _zmc = out.groupby("trade_date")[_mc].transform(
            lambda s: _zscore(s.fillna(0))
        )
        _zr120 = out.groupby("trade_date")[_r120].transform(
            lambda s: _zscore(s.fillna(0))
        )
        out["margin_overheat_risk_score"] = _zmc * _zr120.clip(lower=0)

    # Clean up intermediates
    for c in list(out.columns):
        if c.startswith("_"):
            out = out.drop(columns=[c])

    return out


# ── Shareholder concentration features ──────────────────────────────────


def build_shareholder_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build shareholder / concentration features.

    Source fields loaded via ``_load_holder_data`` and …_top10 and
    then merged PIT-style on ``ann_date``.
    """
    out = df.copy()

    # ── Holder num change ──────────────────────────────────────────
    if "holder_num" in out.columns and "ts_code" in out.columns:
        _prev = out.groupby("ts_code")["holder_num"].shift(1)
        out["holder_num_chg_qoq"] = _clip_inf(_safe_div(out["holder_num"], _prev) - 1)
        _prev2 = out.groupby("ts_code")["holder_num"].shift(2)
        out["holder_num_chg_2q"] = _clip_inf(_safe_div(out["holder_num"], _prev2) - 1)

    # ── Avg shares per holder ──────────────────────────────────────
    if {"holder_num", "total_share"}.issubset(out.columns):
        _avg = out["total_share"] / out["holder_num"].replace(0, np.nan)
        out["avg_shares_per_holder"] = _avg
        _prev_avg = _avg.groupby(out["ts_code"]).shift(1) if "ts_code" in out.columns else None
        if _prev_avg is not None:
            out["avg_shares_per_holder_chg_qoq"] = _clip_inf(
                _safe_div(_avg, _prev_avg) - 1
            )

    # ── Top10 holder ratio ─────────────────────────────────────────
    if "top10_holder_ratio" in out.columns and "ts_code" in out.columns:
        out["top10_holder_ratio_chg_qoq"] = _clip_inf(
            _safe_div(
                out["top10_holder_ratio"],
                out.groupby("ts_code")["top10_holder_ratio"].shift(1),
            ) - 1
        )

    # ── Datetime columns for stale-days ─────────────────────────────
    if "holder_ann_date" in out.columns and "trade_date" in out.columns:
        _ha = pd.to_datetime(out["holder_ann_date"], errors="coerce")
        _td = pd.to_datetime(out["trade_date"], errors="coerce")
        out["holder_num_stale_days"] = (_td - _ha).dt.days.clip(lower=0)
    if "top10_ann_date" in out.columns and "trade_date" in out.columns:
        _ta = pd.to_datetime(out["top10_ann_date"], errors="coerce")
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
    _hn = out.get("holder_num_chg_qoq", None)
    _r60 = out.get("ret_60d", None)
    if _hn is not None and _r60 is not None and "trade_date" in out.columns:
        _zhn = out.groupby("trade_date")[_hn].transform(
            lambda s: -_zscore(s.fillna(0))
        )
        _zr60 = out.groupby("trade_date")[_r60].transform(
            lambda s: _zscore(s.fillna(0))
        )
        out["holder_squeeze_score"] = _zhn * _zr60.clip(lower=0)

    # ── Composite: price confirm ───────────────────────────────────
    _hc = out.get("holder_concentration_score", None)
    _r120 = out.get("ret_120d", None)
    if _hc is not None and _r120 is not None and "trade_date" in out.columns:
        _zhc = out.groupby("trade_date")[_hc].transform(
            lambda s: _zscore(s.fillna(0))
        )
        _zr120 = out.groupby("trade_date")[_r120].transform(
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
