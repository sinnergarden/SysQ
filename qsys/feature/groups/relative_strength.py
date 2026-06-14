from __future__ import annotations

import numpy as np
import pandas as pd


def _rolling_return(series: pd.Series, window: int) -> pd.Series:
    return series.pct_change(window)


def build_relative_strength_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close_grp = out.groupby("ts_code")["close"]
    volume_col = "volume" if "volume" in out.columns else "vol"
    vol_grp = out.groupby("ts_code")[volume_col]
    amount_grp = out.groupby("ts_code")["amount"]

    out["ret_1d"] = close_grp.pct_change(1)
    out["ret_3d"] = close_grp.pct_change(3)
    out["ret_5d"] = close_grp.pct_change(5)
    out["vol_mean_3d"] = vol_grp.transform(lambda s: s.rolling(3).mean())
    out["vol_mean_5d"] = vol_grp.transform(lambda s: s.rolling(5).mean())
    out["amount_mean_3d"] = amount_grp.transform(lambda s: s.rolling(3).mean())
    out["amount_mean_5d"] = amount_grp.transform(lambda s: s.rolling(5).mean())

    for col in ["ret_1d", "ret_3d", "ret_5d", "vol_mean_3d", "vol_mean_5d", "amount_mean_3d", "amount_mean_5d"]:
        out[f"{col}_rank"] = out.groupby("trade_date")[col].rank(pct=True, method="average")

    if "index_close" in out.columns:
        idx_ret_3 = out.groupby("trade_date")["index_close"].transform("first").pct_change(3)
        idx_ret_5 = out.groupby("trade_date")["index_close"].transform("first").pct_change(5)
        out["stock_minus_index_ret_3d"] = out["ret_3d"] - idx_ret_3
        out["stock_minus_index_ret_5d"] = out["ret_5d"] - idx_ret_5
    else:
        out["stock_minus_index_ret_3d"] = pd.NA
        out["stock_minus_index_ret_5d"] = pd.NA

    if "industry_ret_3d" in out.columns:
        out["stock_minus_industry_ret_3d"] = out["ret_3d"] - out["industry_ret_3d"]
    if "industry_ret_5d" in out.columns:
        out["stock_minus_industry_ret_5d"] = out["ret_5d"] - out["industry_ret_5d"]

    # ── Value-growth: market confirmation (medium/long-horizon) ──
    out["ret_20d"] = close_grp.pct_change(20)
    out["ret_60d"] = close_grp.pct_change(60)
    out["ret_120d"] = close_grp.pct_change(120)

    out["volume_ratio_20d"] = out[volume_col] / vol_grp.transform(lambda s: s.rolling(20).mean()).replace(0, pd.NA)
    out["volume_ratio_60d"] = out[volume_col] / vol_grp.transform(lambda s: s.rolling(60).mean()).replace(0, pd.NA)

    out["distance_to_120d_high"] = out["close"] / close_grp.transform(lambda s: s.rolling(120).max()) - 1
    out["distance_to_250d_high"] = out["close"] / close_grp.transform(lambda s: s.rolling(250).max()) - 1

    # ═══════════════════════════════════════════════════════════════
    # v2: continuation_trend_quality
    # ═══════════════════════════════════════════════════════════════
    daily_ret = close_grp.pct_change(fill_method=None)

    # Up-day ratio
    _up = (daily_ret > 0).astype(float)
    out["up_day_ratio_60d"] = _up.groupby(out["ts_code"]).transform(lambda s: s.rolling(60, min_periods=20).mean())
    out["up_day_ratio_120d"] = _up.groupby(out["ts_code"]).transform(lambda s: s.rolling(120, min_periods=40).mean())

    # Trend smoothness: net ret / total absolute movement
    _abs_ret = daily_ret.abs()
    _abs_sum_60 = _abs_ret.groupby(out["ts_code"]).transform(lambda s: s.rolling(60, min_periods=20).sum())
    _abs_sum_120 = _abs_ret.groupby(out["ts_code"]).transform(lambda s: s.rolling(120, min_periods=40).sum())
    out["trend_smoothness_60d"] = out["ret_60d"] / _abs_sum_60.replace(0, np.nan)
    out["trend_smoothness_120d"] = out["ret_120d"] / _abs_sum_120.replace(0, np.nan)

    # Max pullback from rolling high
    _rmax_120 = close_grp.transform(lambda s: s.rolling(120, min_periods=40).max())
    _drawdown = _rmax_120 / out["close"] - 1
    out["max_pullback_120d"] = _drawdown.groupby(out["ts_code"]).transform(lambda s: s.rolling(120, min_periods=40).min())

    # Volatility-adjusted return
    _vol_60 = daily_ret.groupby(out["ts_code"]).transform(lambda s: s.rolling(60, min_periods=20).std())
    _vol_120 = daily_ret.groupby(out["ts_code"]).transform(lambda s: s.rolling(120, min_periods=40).std())
    out["volatility_adjusted_return_60d"] = out["ret_60d"] / _vol_60.replace(0, np.nan)
    out["volatility_adjusted_return_120d"] = out["ret_120d"] / _vol_120.replace(0, np.nan)

    # RPS (Relative Price Strength): cross-sectional percentile rank of returns
    # This replaces RSI for relative-strength measurement (CANSLIM convention)
    out["rps_60d"] = out.groupby("trade_date")["ret_60d"].rank(pct=True)
    out["rps_120d"] = out.groupby("trade_date")["ret_120d"].rank(pct=True)
    out["rps_20d"] = out.groupby("trade_date")["ret_20d"].rank(pct=True)
    out["rps_20d_minus_rps_60d"] = out["rps_20d"] - out["rps_60d"]

    # Industry-relative RPS
    if "industry" in out.columns and out["industry"].notna().any():
        out["rps_industry_60d"] = out.groupby(["trade_date", "industry"])["ret_60d"].rank(pct=True)
        out["rps_industry_120d"] = out.groupby(["trade_date", "industry"])["ret_120d"].rank(pct=True)
    else:
        out["rps_industry_60d"] = pd.NA
        out["rps_industry_120d"] = pd.NA

    # Price percentile in 252d window
    out["price_percentile_252d"] = close_grp.transform(
        lambda s: s.rolling(252, min_periods=60).rank(pct=True)
    )

    # Distance from 252d low (for repair setup)
    _close_min_252 = close_grp.transform(lambda s: s.rolling(252, min_periods=60).min())
    out["distance_to_252d_low"] = out["close"] / _close_min_252 - 1

    # ═══════════════════════════════════════════════════════════════
    # v2: volume_participation_quality
    # ═══════════════════════════════════════════════════════════════
    _up_vol = (out[volume_col] * _up).groupby(out["ts_code"]).transform(lambda s: s.rolling(60, min_periods=20).sum())
    _up_count = _up.groupby(out["ts_code"]).transform(lambda s: s.rolling(60, min_periods=20).sum())
    _down_vol = (out[volume_col] * (_up == 0).astype(float)).groupby(out["ts_code"]).transform(lambda s: s.rolling(60, min_periods=20).sum())
    _down_count = (_up == 0).astype(float).groupby(out["ts_code"]).transform(lambda s: s.rolling(60, min_periods=20).sum())
    out["volume_up_down_ratio_60d"] = (_up_vol / _up_count) / (_down_vol / _down_count.replace(0, pd.NA))

    # Positive volume ratio (fraction of days with above-mean volume)
    _vol_mean_60 = vol_grp.transform(lambda s: s.rolling(60, min_periods=20).mean())
    _vol_above = (out[volume_col] > _vol_mean_60).astype(float)
    out["positive_volume_ratio_60d"] = _vol_above.groupby(out["ts_code"]).transform(
        lambda s: s.rolling(60, min_periods=20).mean()
    )

    # Amount ratio
    out["amount_ratio_20d"] = out["amount"] / amount_grp.transform(
        lambda s: s.rolling(20, min_periods=10).mean()
    ).replace(0, pd.NA)
    out["amount_ratio_60d"] = out["amount"] / amount_grp.transform(
        lambda s: s.rolling(60, min_periods=20).mean()
    ).replace(0, pd.NA)

    # Volume spike (z-score over 20d)
    _vol_m_20 = vol_grp.transform(lambda s: s.rolling(20, min_periods=10).mean())
    _vol_s_20 = vol_grp.transform(lambda s: s.rolling(20, min_periods=10).std()).replace(0, np.nan)
    out["volume_spike_20d"] = (out[volume_col] - _vol_m_20) / _vol_s_20

    # Volume stability (1/CV over 60d)
    _vol_m_60 = vol_grp.transform(lambda s: s.rolling(60, min_periods=20).mean()).replace(0, np.nan)
    _vol_s_60 = vol_grp.transform(lambda s: s.rolling(60, min_periods=20).std()).replace(0, np.nan)
    out["volume_stability_60d"] = 1 / (_vol_s_60 / _vol_m_60)

    # ═══════════════════════════════════════════════════════════════
    # Clean up intermediates
    for c in list(out.columns):
        if c.startswith("_"):
            out = out.drop(columns=[c])

    return out
