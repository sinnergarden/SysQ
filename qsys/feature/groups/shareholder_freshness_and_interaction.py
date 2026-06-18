"""Shareholder freshness and interaction features.

Freshness-decayed versions of holder concentration/squeeze scores,
plus cross-feature interactions with value, growth, and industry-relative signals.

Depends on v3a shareholder features (holder_concentration_score,
holder_squeeze_score, holder_num_stale_days, top10_holder_stale_days)
and fundamental context features (pe_repair_room_to_median,
pb_repair_room_to_median, revenue_yoy_accel, profit_yoy_accel,
industry_relative_rps_120d).

Usage
-----
Called from ``build_phase1_features`` via the feature flags path.
When ``enable_shareholder_freshness_features`` is True, these features are
computed after the v3a blocks and fundamental context.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ── Shared helpers ──────────────────────────────────────────────────────


def _zscore(s: pd.Series) -> pd.Series:
    """Cross-sectional zscore."""
    std = s.std(ddof=0)
    if pd.isna(std) or std < 1e-12:
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / std


def _clip_inf(s: pd.Series) -> pd.Series:
    return s.replace([np.inf, -np.inf], np.nan)


# ── Freshness decay + interaction features ─────────────────────────────


def build_shareholder_freshness_and_interaction_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build freshness-decayed and interaction features.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain ``trade_date``, ``ts_code``, and the source columns
        described for each feature below.  Missing columns are silently
        skipped.

    Source columns consumed
    -----------------------
    holder_concentration_score, holder_squeeze_score           — scores
    holder_num_stale_days, top10_holder_stale_days             — staleness
    pe_repair_room_to_median, pb_repair_room_to_median         — value proxy
    revenue_yoy_accel, profit_yoy_accel                        — growth proxy
    industry_relative_rps_120d                                  — industry rps
    valuation_repair_score, growth_accel_score (optional)      — direct proxy override
    """
    out = df.copy()

    # ═══════════════════════════════════════════════════════════════════
    # 1-2. Decay weights
    # ═══════════════════════════════════════════════════════════════════

    if "holder_num_stale_days" in out.columns:
        out["holder_decay_weight"] = np.exp(-out["holder_num_stale_days"] / 60.0)
    if "top10_holder_stale_days" in out.columns:
        out["top10_decay_weight"] = np.exp(-out["top10_holder_stale_days"] / 60.0)

    # ═══════════════════════════════════════════════════════════════════
    # 3-4. Freshness-weighted scores
    # ═══════════════════════════════════════════════════════════════════

    if "holder_concentration_score" in out.columns and "holder_decay_weight" in out.columns:
        out["holder_concentration_score_decay"] = (
            out["holder_concentration_score"] * out["holder_decay_weight"]
        )
    if "holder_squeeze_score" in out.columns and "holder_decay_weight" in out.columns:
        out["holder_squeeze_score_decay"] = (
            out["holder_squeeze_score"] * out["holder_decay_weight"]
        )

    # ═══════════════════════════════════════════════════════════════════
    # 5-6. Fresh holder signals (using primary holder stale days)
    # ═══════════════════════════════════════════════════════════════════

    if "holder_concentration_score" in out.columns and "holder_num_stale_days" in out.columns:
        stale = out["holder_num_stale_days"]
        conc = out["holder_concentration_score"]
        out["fresh_holder_signal_40d"] = conc * (stale <= 40).astype(float)
        out["fresh_holder_signal_80d"] = conc * (stale <= 80).astype(float)

    # ═══════════════════════════════════════════════════════════════════
    # Value repair proxy
    # ═══════════════════════════════════════════════════════════════════

    if "valuation_repair_score" in out.columns:
        out["_value_repair_proxy"] = out["valuation_repair_score"]
    else:
        _parts = []
        if "pe_repair_room_to_median" in out.columns:
            _parts.append(
                out.groupby("trade_date")["pe_repair_room_to_median"].transform(
                    lambda s: _zscore(s.fillna(0))
                )
            )
        if "pb_repair_room_to_median" in out.columns:
            _parts.append(
                out.groupby("trade_date")["pb_repair_room_to_median"].transform(
                    lambda s: _zscore(s.fillna(0))
                )
            )
        if _parts:
            out["_value_repair_proxy"] = sum(_parts) / len(_parts)

    # ═══════════════════════════════════════════════════════════════════
    # Growth acceleration proxy
    # ═══════════════════════════════════════════════════════════════════

    if "growth_accel_score" in out.columns:
        out["_growth_accel_proxy"] = out["growth_accel_score"]
    else:
        _parts = []
        if "revenue_yoy_accel" in out.columns:
            _parts.append(
                out.groupby("trade_date")["revenue_yoy_accel"].transform(
                    lambda s: _zscore(s.fillna(0))
                )
            )
        if "profit_yoy_accel" in out.columns:
            _parts.append(
                out.groupby("trade_date")["profit_yoy_accel"].transform(
                    lambda s: _zscore(s.fillna(0))
                )
            )
        if _parts:
            out["_growth_accel_proxy"] = sum(_parts) / len(_parts)

    # ═══════════════════════════════════════════════════════════════════
    # 7-9. Cross-feature interactions (zscore by trade_date)
    # ═══════════════════════════════════════════════════════════════════

    _conc_decay_key = "holder_concentration_score_decay"
    if _conc_decay_key in out.columns:
        _z_conc = out.groupby("trade_date")[_conc_decay_key].transform(
            lambda s: _zscore(s.fillna(0))
        )

        # 7. holder_concentration_x_value
        if "_value_repair_proxy" in out.columns:
            _z_val = out.groupby("trade_date")["_value_repair_proxy"].transform(
                lambda s: _zscore(s.fillna(0))
            )
            out["holder_concentration_x_value"] = _clip_inf(_z_conc * _z_val)

        # 8. holder_concentration_x_growth
        if "_growth_accel_proxy" in out.columns:
            _z_grw = out.groupby("trade_date")["_growth_accel_proxy"].transform(
                lambda s: _zscore(s.fillna(0))
            )
            out["holder_concentration_x_growth"] = _clip_inf(_z_conc * _z_grw)

        # 9. holder_concentration_x_industry_rps
        if "industry_relative_rps_120d" in out.columns:
            _z_rps = out.groupby("trade_date")["industry_relative_rps_120d"].transform(
                lambda s: _zscore(s.fillna(0))
            )
            out["holder_concentration_x_industry_rps"] = _clip_inf(_z_conc * _z_rps)

    # Clean up intermediate columns
    for c in list(out.columns):
        if c.startswith("_"):
            out = out.drop(columns=[c])

    return out
