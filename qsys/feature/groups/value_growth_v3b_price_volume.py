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
    """Build volume quality features — stability, contraction, quality."""
    out = df.copy()

    if not {"close", "amount", "ts_code", "trade_date"}.issubset(out.columns):
        return out

    _grp_amt = out.groupby("ts_code")["amount"]
    _grp_ret = out.groupby("ts_code")["close"].pct_change()

    # ── up_volume_down_volume_ratio_120d ─────────────────────────────
    # Volume on up days / volume on down days
    _up_vol = _grp_ret.gt(0).astype(float) * out["amount"]
    _down_vol = _grp_ret.lt(0).astype(float) * out["amount"]
    for window, label in [(60, "60d"), (120, "120d")]:
        _up_sum = _up_vol.groupby(out["ts_code"]).transform(
            lambda s: s.rolling(window, min_periods=20).sum()
        )
        _down_sum = _down_vol.groupby(out["ts_code"]).transform(
            lambda s: s.rolling(window, min_periods=20).sum()
        )
        out[f"up_volume_down_volume_ratio_{label}"] = _clip_inf(
            _safe_div(_up_sum, _down_sum)
        )

    # ── volume_contraction_after_rise_60d ────────────────────────────
    # After a positive return period, volume is contracting
    _ret_20 = _grp_ret.rolling(20, min_periods=10).mean()
    _amount_z = _grp_amt.transform(lambda s: _rolling_zscore(s, 60))
    # When ret_20 > 0 and amount zscore is falling → healthy consolidation
    _amt_trend = _grp_amt.transform(lambda s: s.rolling(20, min_periods=10).mean())
    _amt_trend_pct = _amt_trend.pct_change(10)
    out["volume_contraction_after_rise_60d"] = (
        (_ret_20.gt(0).astype(float)) *
        (-_amt_trend_pct.fillna(0).clip(lower=0))
    ).clip(lower=0)

    # ── quiet_accumulation_60d ───────────────────────────────────────
    # Price slowly rising + amount volatility decreasing
    _close_ma_20 = _grp_ret.rolling(20, min_periods=10).mean()
    _amount_vol = _grp_amt.transform(
        lambda s: s.rolling(20, min_periods=10).std(ddof=0)
    )
    _amount_vol_pct = _amount_vol.pct_change(10)
    out["quiet_accumulation_60d"] = (
        _close_ma_20.gt(0).astype(float) *
        (-_amount_vol_pct.fillna(0).clip(lower=0))
    ).clip(lower=0)

    # ── amount_stability_60d ─────────────────────────────────────────
    # Negative of amount coefficient of variation (higher = more stable)
    _amt_mean = _grp_amt.transform(lambda s: s.rolling(60, min_periods=20).mean())
    _amt_std = _grp_amt.transform(lambda s: s.rolling(60, min_periods=20).std(ddof=0))
    out["amount_stability_60d"] = _clip_inf(-_safe_div(_amt_std, _amt_mean))

    # ── breakout_volume_quality_120d ─────────────────────────────────
    # Near 120d high + volume moderately elevated (not extreme)
    _high_120 = out.groupby("ts_code")["close"].transform(
        lambda s: s.rolling(120, min_periods=60).max()
    )
    _near_high = _safe_div(out["close"], _high_120).gt(0.90).astype(float)
    _amt_zscore = _grp_amt.transform(lambda s: _rolling_zscore(s, 120))
    # Volume zscore between 0.5 and 1.5 (moderate) near high
    _moderate_vol = (_amt_zscore.gt(0.5) & _amt_zscore.lt(1.5)).astype(float)
    out["breakout_volume_quality_120d"] = _near_high * _moderate_vol

    return out


# ── Interaction features (v3a × v3b) ────────────────────────────────────


def build_v3a_v3b_interaction_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build interaction features combining v3a and v3b signals.

    All rely on columns from v3a (margin / shareholder / concentration)
    and v3b (trend quality / volume quality) groups.
    """
    out = df.copy()

    # holder_concentration * max(zscore(trend_consistency_120d), 0)
    _hc = out.get("holder_concentration_score")
    _tc = out.get("trend_consistency_120d")
    if _hc is not None and _tc is not None and "trade_date" in out.columns:
        _ztc = out.groupby("trade_date")[_tc].transform(lambda s: _zscore(s.fillna(0)))
        out["holder_concentration_trend_confirm"] = _hc * _ztc.clip(lower=0)

    # holder_concentration * max(zscore(low_vol_uptrend_120d), 0)
    _lv = out.get("low_vol_uptrend_120d")
    if _hc is not None and _lv is not None and "trade_date" in out.columns:
        _zlv = out.groupby("trade_date")[_lv].transform(lambda s: _zscore(s.fillna(0)))
        out["holder_concentration_low_vol_uptrend"] = _hc * _zlv.clip(lower=0)

    # holder_concentration * max(zscore(volume_contraction_after_rise_60d), 0)
    _vc = out.get("volume_contraction_after_rise_60d")
    if _hc is not None and _vc is not None and "trade_date" in out.columns:
        _zvc = out.groupby("trade_date")[_vc].transform(lambda s: _zscore(s.fillna(0)))
        out["holder_concentration_volume_contract"] = _hc * _zvc.clip(lower=0)

    # margin_trend_confirm_score * max(zscore(holder_concentration_score), 0)
    _mt = out.get("margin_trend_confirm_score")
    if _mt is not None and _hc is not None and "trade_date" in out.columns:
        _zhc = out.groupby("trade_date")[_hc].transform(lambda s: _zscore(s.fillna(0)))
        out["margin_holder_trend_confirm"] = _mt * _zhc.clip(lower=0)

    # margin_trend_confirm_score * max(zscore(pullback_recovery_speed_60d), 0)
    _pr = out.get("pullback_recovery_speed_60d")
    if _mt is not None and _pr is not None and "trade_date" in out.columns:
        _zpr = out.groupby("trade_date")[_pr].transform(lambda s: _zscore(s.fillna(0)))
        out["margin_pullback_recovery_confirm"] = _mt * _zpr.clip(lower=0)

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
