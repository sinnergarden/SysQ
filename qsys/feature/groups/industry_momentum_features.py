"""Industry momentum / theme proxy features.

All features computed per trade_date from existing price panel + industry mapping.
No external data needed.

PIT: all calculations use only historical window (rolling/backward).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _safe_div(num, den):
    return num / den.replace(0, np.nan)


def _clip_inf(s):
    return s.replace([np.inf, -np.inf], np.nan)


def build_industry_momentum_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build industry/sector momentum proxy features.

    Requires columns: trade_date, ts_code, close, amount, industry.
    Also reads ret_20d, ret_60d, ret_120d if available (computed upstream).
    """
    out = df.copy()
    required = {"trade_date", "ts_code", "close", "amount"}
    if not required.issubset(out.columns):
        return out
    if "industry" not in out.columns:
        return out

    _inst_key = "ts_code"
    _dt_key = "trade_date"
    _ind_key = "industry"

    # Daily return
    ret_d = out.groupby(_inst_key)["close"].pct_change()

    # Industry average return
    ind_ret = ret_d.groupby([out[_dt_key], out[_ind_key]]).transform("mean")

    # ── Industry momentum (rolling windows on industry-avg daily returns) ──
    for window, label in [(20, "20d"), (60, "60d"), (120, "120d")]:
        ind_ret_roll = ind_ret.groupby(out[_inst_key]).transform(
            lambda s: s.rolling(window, min_periods=window // 2).mean()
        )
        out[f"industry_ret_{label}"] = _clip_inf(ind_ret_roll)

    # ── Industry breadth: fraction of stocks with positive daily return ──
    for window, label in [(20, "20d"), (60, "60d")]:
        _pos = ret_d.gt(0).astype(float)
        breadth = _pos.groupby([out[_dt_key], out[_ind_key]]).transform(
            lambda s: s.rolling(window, min_periods=window // 2).mean()
        )
        out[f"industry_breadth_{label}"] = breadth

    # ── Industry new-high ratio ──────────────────────────────────────────
    _high_120 = out.groupby(_inst_key)["close"].transform(
        lambda s: s.rolling(120, min_periods=60).max()
    )
    _near_high = _safe_div(out["close"], _high_120).gt(0.95).astype(float)
    out["industry_new_high_ratio"] = _near_high.groupby([out[_dt_key], out[_ind_key]]).transform(
        lambda s: s.rolling(20, min_periods=10).mean()
    )

    # ── Industry top-stock momentum ──────────────────────────────────────
    # Mean return of top 20% stocks in each industry (by ret_60d)
    if "ret_60d" in out.columns:
        _r60 = out["ret_60d"].fillna(0)
        _top_mask = _r60.groupby(out[_ind_key]).transform(
            lambda s: s >= s.quantile(0.8)
        )
        _top_ret = (_r60 * _top_mask.astype(float)).groupby([out[_dt_key], out[_ind_key]]).transform(
            lambda s: s.replace(0, np.nan).rolling(60, min_periods=10).mean()
        )
        out["industry_top_stock_momentum"] = _clip_inf(_top_ret)

    # ── Industry volume expansion ────────────────────────────────────────
    _amt_ma = out.groupby(_inst_key)["amount"].transform(
        lambda s: s.rolling(20, min_periods=10).mean()
    )
    _amt_ma_prev = out.groupby(_inst_key)["amount"].transform(
        lambda s: s.rolling(20, min_periods=10).mean().shift(20)
    )
    _vol_ratio = _safe_div(_amt_ma, _amt_ma_prev)
    out["industry_volume_expansion"] = _vol_ratio.groupby([out[_dt_key], out[_ind_key]]).transform(
        lambda s: s.rolling(20, min_periods=10).mean()
    )

    # ── Stock minus industry return ──────────────────────────────────────
    if "ret_60d" in out.columns:
        ind_r60 = out["ret_60d"].groupby([out[_dt_key], out[_ind_key]]).transform("mean")
        out["stock_minus_industry_ret_60d"] = _clip_inf(out["ret_60d"] - ind_r60)
    if "ret_20d" in out.columns:
        ind_r20 = out["ret_20d"].groupby([out[_dt_key], out[_ind_key]]).transform("mean")
        out["stock_minus_industry_ret_20d"] = _clip_inf(out["ret_20d"] - ind_r20)

    # ── Industry leader-follow score ────────────────────────────────────
    # Correlation of stock return with industry top-5 return (rolling 60d)
    if "ret_60d" in out.columns:
        _top5_mask = _r60.groupby([out[_dt_key], out[_ind_key]]).transform(
            lambda s: s >= s.quantile(0.9)
        )
        _ind_top5_ret = (_r60 * _top5_mask.astype(float)).groupby(out[_ind_key]).transform(
            lambda s: s.replace(0, np.nan).rolling(60, min_periods=20).mean()
        )
        # How closely does this stock follow industry leaders?
        _follow = (ret_d * ind_ret).groupby(out[_inst_key]).transform(
            lambda s: s.rolling(60, min_periods=20).mean()
        )
        _vol_prod = (ret_d ** 2).groupby(out[_inst_key]).transform(
            lambda s: s.rolling(60, min_periods=20).mean()
        ) ** 0.5
        _ind_vol = (ind_ret ** 2).groupby(out[_inst_key]).transform(
            lambda s: s.rolling(60, min_periods=20).mean()
        ) ** 0.5
        _corr = _safe_div(_follow, _vol_prod * _ind_vol)
        out["industry_leader_follow_score"] = _clip_inf(_corr)

    # Clean intermediates
    for c in list(out.columns):
        if c.startswith("_"):
            out = out.drop(columns=[c])

    return out
