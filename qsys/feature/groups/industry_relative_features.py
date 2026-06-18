"""Industry-relative rank features.

All features rank within (trade_date, industry) groups.
Group size < 5 -> NaN.
Uses $industry column from qlib meta (attached via ``attach_industry_info``).
Compatible with label_maturity_lag validation.

Rank percentile = rank within group / count -> 0-1.
Higher = better relative position in industry.
Valuation and holder-reduction features are negated before ranking so that
higher output consistently means "more desirable" (cheaper / less dispersion).

Usage
-----
Called from ``build_phase1_features`` via the feature flags path.
Computed after the industry context features block.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ── Shared helpers ──────────────────────────────────────────────────────


def _safe_div(num: pd.Series, den: pd.Series) -> pd.Series:
    return num / den.replace(0, np.nan)


def _clip_inf(s: pd.Series) -> pd.Series:
    return s.replace([np.inf, -np.inf], np.nan)


def _zscore(s: pd.Series) -> pd.Series:
    std = s.std(ddof=0)
    if pd.isna(std) or std < 1e-12:
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / std


def _rank_pct(s: pd.Series, min_group: int = 5) -> pd.Series:
    """Rank percentile within group: rank / count, yielding 0-1.

    Returns NaN when group size is below *min_group*.
    """
    if len(s) < min_group:
        return pd.Series(np.nan, index=s.index)
    return s.rank(pct=True)


# ── Industry-relative rank features ─────────────────────────────────────


def build_industry_relative_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build industry-relative rank features for fundamental / market data.

    Every feature cross-sectionally ranks a raw field within its
    (trade_date, industry) bucket, producing a 0-1 percentile score.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain **trade_date**, **industry**, and at least one of the
        source columns listed below.  Missing columns are silently skipped.

    Source columns consumed
    -----------------------
    roe, revenue_yoy, profit_yoy, ocf_margin           — fundamentals
    pe_ttm, pb_raw                                       — valuation (negated)
    holder_num_chg_qoq                                   — holder (negated)
    top10_holder_ratio_chg_qoq                           — holder
    margin_crowding_score                                — sentiment
    ret_60d, ret_120d                                    — momentum
    """
    out = df.copy()

    if "industry" not in out.columns or "trade_date" not in out.columns:
        return out

    _grp = ["trade_date", "industry"]

    # ── Fundamental relative features ──────────────────────────────────

    if "roe" in out.columns:
        out["industry_relative_roe"] = (
            out.groupby(_grp)["roe"].transform(_rank_pct)
        )

    if "revenue_yoy" in out.columns:
        out["industry_relative_revenue_yoy"] = (
            out.groupby(_grp)["revenue_yoy"].transform(_rank_pct)
        )

    if "profit_yoy" in out.columns:
        out["industry_relative_profit_yoy"] = (
            out.groupby(_grp)["profit_yoy"].transform(_rank_pct)
        )

    if "ocf_margin" in out.columns:
        out["industry_relative_ocf_margin"] = (
            out.groupby(_grp)["ocf_margin"].transform(_rank_pct)
        )

    # ── Valuation relative features (negated: lower = cheaper = higher rank) ──

    if "pe_ttm" in out.columns:
        out["industry_relative_pe_cheapness"] = (
            out.groupby(_grp)["pe_ttm"].transform(lambda s: _rank_pct(-s))
        )

    if "pb_raw" in out.columns:
        out["industry_relative_pb_cheapness"] = (
            out.groupby(_grp)["pb_raw"].transform(lambda s: _rank_pct(-s))
        )

    # ── Holder structure relative features ─────────────────────────────

    if "holder_num_chg_qoq" in out.columns:
        # Negated: more reduction in holder count -> higher rank
        out["industry_relative_holder_chg"] = (
            out.groupby(_grp)["holder_num_chg_qoq"].transform(
                lambda s: _rank_pct(-s)
            )
        )

    if "top10_holder_ratio_chg_qoq" in out.columns:
        out["industry_relative_top10_chg"] = (
            out.groupby(_grp)["top10_holder_ratio_chg_qoq"].transform(_rank_pct)
        )

    # ── Sentiment / crowding ───────────────────────────────────────────

    if "margin_crowding_score" in out.columns:
        out["industry_relative_margin_crowding"] = (
            out.groupby(_grp)["margin_crowding_score"].transform(_rank_pct)
        )

    # ── Price momentum relative (industry RPS) ─────────────────────────

    if "ret_60d" in out.columns:
        out["industry_relative_rps_60d"] = (
            out.groupby(_grp)["ret_60d"].transform(_rank_pct)
        )

    if "ret_120d" in out.columns:
        out["industry_relative_rps_120d"] = (
            out.groupby(_grp)["ret_120d"].transform(_rank_pct)
        )

    return out
