"""v3-b feature groups: delayed-safe price-volume quality features.

All features use only historical windows (no future data).
Compatible with label_maturity_lag validation.

Usage
-----
Called from ``build_phase1_features`` via the feature flags path.
When ``enable_v3b_price_volume_features`` is True, these features are
computed after the v3a margin/shareholder block.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ── Shared helpers ──────────────────────────────────────────────────────


def _safe_div(num: pd.Series, den: pd.Series) -> pd.Series:
    return num / den.replace(0, np.nan)


def _zscore(s: pd.Series) -> pd.Series:
    std = s.std(ddof=0)
    if pd.isna(std) or std < 1e-12:
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / std


def _clip_inf(s: pd.Series) -> pd.Series:
    return s.replace([np.inf, -np.inf], np.nan)


def _rolling_zscore(series: pd.Series, window: int = 60) -> pd.Series:
    """Rolling zscore to detect extremes (not for cross-section)."""
    rmean = series.rolling(window, min_periods=20).mean()
    rstd = series.rolling(window, min_periods=20).std(ddof=0).replace(0, np.nan)
    return (series - rmean) / rstd


# ── Long-term trend quality features ────────────────────────────────────


def build_trend_quality_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build trend quality features — consistency, vol-adjusted, drawdown."""
    out = df.copy()
    close = out.get("close")
    ts_code = out.get("ts_code")
    trade_date = out.get("trade_date")

    if close is None or ts_code is None:
        return out

    _grp = out.groupby("ts_code")["close"]

    # ── trend_consistency_120d ───────────────────────────────────────
    # Fraction of positive daily returns over 120d
    ret_1d = _grp.pct_change()
    for window, label in [(60, "60d"), (120, "120d")]:
        up_days = ret_1d.gt(0).groupby(out["ts_code"]).transform(
            lambda s: s.rolling(window, min_periods=40).mean()
        )
        out[f"trend_consistency_{label}"] = up_days

    # ── low_vol_uptrend_120d ─────────────────────────────────────────
    # ret_120d / realized_vol_120d — higher = smooth uptrend
    for window, label in [(60, "60d"), (120, "120d")]:
        _ret = _grp.pct_change(window)
        _vol = ret_1d.groupby(out["ts_code"]).transform(
            lambda s: s.rolling(window, min_periods=40).std(ddof=0)
        )
        out[f"low_vol_uptrend_{label}"] = _clip_inf(_safe_div(_ret, _vol))

    # ── return_drawdown_ratio_120d ───────────────────────────────────
    # ret_120d / abs(max_drawdown_120d)
    for window, label in [(60, "60d"), (120, "120d")]:
        _ret = _grp.pct_change(window)
        # max drawdown over window: rolling max close vs current close
        _rolling_max = _grp.transform(lambda s: s.rolling(window, min_periods=40).max())
        _drawdown = _safe_div(_rolling_max - out["close"], _rolling_max)
        _max_dd = _drawdown.groupby(out["ts_code"]).transform(
            lambda s: s.rolling(window, min_periods=40).max()
        )
        out[f"return_drawdown_ratio_{label}"] = _clip_inf(
            _safe_div(_ret, _max_dd.replace(0, np.nan))
        )

    # ── pullback_recovery_speed_60d ──────────────────────────────────
    # Current close relative to 60d low, adjusted by close level
    _low_60 = _grp.transform(lambda s: s.rolling(60, min_periods=20).min())
    _high_60 = _grp.transform(lambda s: s.rolling(60, min_periods=20).max())
    # Recovery: how far from 60d low, as fraction of 60d range
    out["pullback_recovery_speed_60d"] = _clip_inf(
        _safe_div(out["close"] - _low_60, _high_60 - _low_60)
    )

    # ── new_high_persistence_120d ────────────────────────────────────
    # Fraction of days where close is within 5% of 120d high
    _high_120 = _grp.transform(lambda s: s.rolling(120, min_periods=60).max())
    _near_high = (out["close"] / _high_120).gt(0.95).astype(float)
    out["new_high_persistence_120d"] = _near_high.groupby(out["ts_code"]).transform(
        lambda s: s.rolling(120, min_periods=60).mean()
    )

    return out


# ── Volume quality features ─────────────────────────────────────────────


def build_volume_quality_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build volume quality features — stability, contraction, quality.

    BUGFIX: all ``rolling(…)`` and ``pct_change(…)`` calls are wrapped
    in ``groupby("ts_code")`` to prevent cross-stock contamination.
    """
    out = df.copy()

    if not {"close", "amount", "ts_code", "trade_date"}.issubset(out.columns):
        return out

    _inst_key = "ts_code"

    # Per-stock daily close returns
    _ret_1d = out.groupby(_inst_key)["close"].pct_change()

    # Per-stock change helper
    def _grp_pct_change(series: pd.Series, periods: int = 1):
        return series.groupby(out[_inst_key]).transform(
            lambda s: s.pct_change(periods)
        )

    # ── up_volume_down_volume_ratio_120d ─────────────────────────────
    _up_vol = _ret_1d.gt(0).astype(float) * out["amount"]
    _down_vol = _ret_1d.lt(0).astype(float) * out["amount"]
    for window, label in [(60, "60d"), (120, "120d")]:
        _up_sum = _up_vol.groupby(out[_inst_key]).transform(
            lambda s: s.rolling(window, min_periods=20).sum()
        )
        _down_sum = _down_vol.groupby(out[_inst_key]).transform(
            lambda s: s.rolling(window, min_periods=20).sum()
        )
        out[f"up_volume_down_volume_ratio_{label}"] = _clip_inf(
            _safe_div(_up_sum, _down_sum)
        )

    # ── volume_contraction_after_rise_60d ────────────────────────────
    _ret_20 = _ret_1d.groupby(out[_inst_key]).transform(
        lambda s: s.rolling(20, min_periods=10).mean()
    )
    _amt_trend = out.groupby(_inst_key)["amount"].transform(
        lambda s: s.rolling(20, min_periods=10).mean()
    )
    _amt_trend_pct = _grp_pct_change(_amt_trend, 10)
    out["volume_contraction_after_rise_60d"] = (
        (_ret_20.gt(0).astype(float)) *
        (-_amt_trend_pct.fillna(0).clip(lower=0))
    ).clip(lower=0)

    # ── quiet_accumulation_60d ───────────────────────────────────────
    _close_ma_20 = _ret_1d.groupby(out[_inst_key]).transform(
        lambda s: s.rolling(20, min_periods=10).mean()
    )
    _amount_vol = out.groupby(_inst_key)["amount"].transform(
        lambda s: s.rolling(20, min_periods=10).std(ddof=0)
    )
    _amount_vol_pct = _grp_pct_change(_amount_vol, 10)
    out["quiet_accumulation_60d"] = (
        _close_ma_20.gt(0).astype(float) *
        (-_amount_vol_pct.fillna(0).clip(lower=0))
    ).clip(lower=0)

    # ── amount_stability_60d ─────────────────────────────────────────
    _amt_mean = out.groupby(_inst_key)["amount"].transform(
        lambda s: s.rolling(60, min_periods=20).mean()
    )
    _amt_std = out.groupby(_inst_key)["amount"].transform(
        lambda s: s.rolling(60, min_periods=20).std(ddof=0)
    )
    out["amount_stability_60d"] = _clip_inf(-_safe_div(_amt_std, _amt_mean))

    # ── breakout_volume_quality_120d ─────────────────────────────────
    _high_120 = out.groupby(_inst_key)["close"].transform(
        lambda s: s.rolling(120, min_periods=60).max()
    )
    _near_high = _safe_div(out["close"], _high_120).gt(0.90).astype(float)
    _amt_zscore = out.groupby(_inst_key)["amount"].transform(
        lambda s: _rolling_zscore(s, 120)
    )
    _moderate_vol = (_amt_zscore.gt(0.5) & _amt_zscore.lt(1.5)).astype(float)
    out["breakout_volume_quality_120d"] = _near_high * _moderate_vol

    return out


# ── Interaction features (v3a × v3b) ────────────────────────────────────


def build_v3a_v3b_interaction_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build interaction features combining v3a and v3b signals.

    BUGFIX: use string column names for groupby, not Series from out.get().
    """
    out = df.copy()

    required = ["holder_concentration_score", "margin_trend_confirm_score",
                 "trend_consistency_120d", "low_vol_uptrend_120d",
                 "volume_contraction_after_rise_60d", "pullback_recovery_speed_60d",
                 "trade_date"]
    if not all(c in out.columns for c in required):
        return out

    hc = out["holder_concentration_score"]

    # holder_concentration * max(zscore(trend_consistency_120d), 0)
    out["holder_concentration_trend_confirm"] = (
        hc * out.groupby("trade_date")["trend_consistency_120d"].transform(
            lambda s: _zscore(s.fillna(0))
        ).clip(lower=0)
    )

    # holder_concentration * max(zscore(low_vol_uptrend_120d), 0)
    out["holder_concentration_low_vol_uptrend"] = (
        hc * out.groupby("trade_date")["low_vol_uptrend_120d"].transform(
            lambda s: _zscore(s.fillna(0))
        ).clip(lower=0)
    )

    # holder_concentration * max(zscore(volume_contraction_after_rise_60d), 0)
    out["holder_concentration_volume_contract"] = (
        hc * out.groupby("trade_date")["volume_contraction_after_rise_60d"].transform(
            lambda s: _zscore(s.fillna(0))
        ).clip(lower=0)
    )

    # margin_trend_confirm_score * max(zscore(holder_concentration_score), 0)
    mt = out["margin_trend_confirm_score"]
    out["margin_holder_trend_confirm"] = (
        mt * out.groupby("trade_date")["holder_concentration_score"].transform(
            lambda s: _zscore(s.fillna(0))
        ).clip(lower=0)
    )

    # margin_trend_confirm_score * max(zscore(pullback_recovery_speed_60d), 0)
    out["margin_pullback_recovery_confirm"] = (
        mt * out.groupby("trade_date")["pullback_recovery_speed_60d"].transform(
            lambda s: _zscore(s.fillna(0))
        ).clip(lower=0)
    )

    return out


# ── Combined builder ────────────────────────────────────────────────────


def build_v3b_price_volume_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build all v3-b price-volume quality features."""
    out = build_trend_quality_features(df)
    out = build_volume_quality_features(out)
    return out


def build_v3b_price_volume_with_interactions(df: pd.DataFrame) -> pd.DataFrame:
    """Build v3-b features + v3a×v3b interaction features."""
    out = build_v3b_price_volume_features(df)
    out = build_v3a_v3b_interaction_features(out)
    return out
