"""Industry momentum / theme proxy features.

All industry-level aggregations are first collapsed to a
``(trade_date, industry)`` daily panel, then rolling windows are
applied at the industry level (over time).  The result is merged
back to individual stocks.

No ``rolling(window)`` is ever called inside a ``groupby(["trade_date", "industry"])``
cross-sectional slice — all rolling is purely temporal.

PIT: all calculations use only historical windows (rolling/backward).
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

    out = out.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

    # ── Step 1: per-stock daily return (time-series within each stock) ──
    daily_ret = out.groupby("ts_code")["close"].pct_change()
    out["_daily_ret"] = daily_ret

    # ── Step 2: per-stock rolling values (time-series, not cross-sectional) ──
    # 120d high for new-high detection
    _high_120 = out.groupby("ts_code")["close"].transform(
        lambda s: s.rolling(120, min_periods=60).max()
    )
    out["_near_high"] = _safe_div(out["close"], _high_120).gt(0.95).astype(float)

    # volume ratio: current 20d avg / past 20d avg (per stock)
    _amt_ma = out.groupby("ts_code")["amount"].transform(
        lambda s: s.rolling(20, min_periods=5).mean()
    )
    _amt_ma_past = out.groupby("ts_code")["amount"].transform(
        lambda s: s.rolling(20, min_periods=5).mean().shift(20)
    )
    out["_vol_ratio"] = _clip_inf(_safe_div(_amt_ma, _amt_ma_past))

    # ── Step 3: construct (trade_date x industry) daily panel ─────────────
    ind_panel = out.groupby(["trade_date", "industry"]).agg(
        ind_ret=("_daily_ret", "mean"),
        ind_breadth=("_daily_ret", lambda s: (s > 0).mean()),
        ind_near_high=("_near_high", "mean"),
        ind_vol_ratio=("_vol_ratio", "mean"),
    ).reset_index()

    if "ret_60d" in out.columns:
        out["_r60"] = out["ret_60d"].fillna(0)
        top20 = out["_r60"].groupby(
            [out["trade_date"], out["industry"]]
        ).transform(lambda s: (s >= s.quantile(0.8)).astype(float))
        out["_top_ret"] = out["_r60"] * top20
        top_panel = out.groupby(["trade_date", "industry"])["_top_ret"].mean().reset_index()
        ind_panel = ind_panel.merge(top_panel, on=["trade_date", "industry"], how="left")

    # ── Step 4: industry-level temporal rolling (on ind_panel time series) ──
    ind_panel = ind_panel.sort_values(["industry", "trade_date"]).reset_index(drop=True)

    for window, label in [(20, "20d"), (60, "60d"), (120, "120d")]:
        ind_panel[f"industry_ret_{label}"] = ind_panel.groupby("industry")["ind_ret"].transform(
            lambda s: s.rolling(window, min_periods=max(2, window // 4)).mean()
        )
    for window, label in [(20, "20d"), (60, "60d")]:
        ind_panel[f"industry_breadth_{label}"] = ind_panel.groupby("industry")["ind_breadth"].transform(
            lambda s: s.rolling(window, min_periods=max(2, window // 4)).mean()
        )
    ind_panel["industry_new_high_ratio"] = ind_panel.groupby("industry")["ind_near_high"].transform(
        lambda s: s.rolling(20, min_periods=5).mean()
    )
    ind_panel["industry_volume_expansion"] = ind_panel.groupby("industry")["ind_vol_ratio"].transform(
        lambda s: s.rolling(20, min_periods=5).mean()
    )
    if "ret_60d" in out.columns and "_top_ret" in ind_panel.columns:
        ind_panel["industry_top_stock_momentum"] = ind_panel.groupby("industry")["_top_ret"].transform(
            lambda s: s.rolling(60, min_periods=10).mean()
        )

    # ── Step 5: merge industry panel back to stocks ──────────────────────
    result_cols = ["trade_date", "industry", "ind_ret"] + [
        c for c in ind_panel.columns if c.startswith("industry_")
    ]
    out = out.merge(ind_panel[result_cols], on=["trade_date", "industry"], how="left")

    # ── Step 6: stock-minus-industry return (cross-sectional per date) ──
    for h in [(60, "60d"), (20, "20d")]:
        col = f"ret_{h[0]}d"
        if col in out.columns:
            ind_mean = out[col].groupby([out["trade_date"], out["industry"]]).transform("mean")
            out[f"stock_minus_industry_ret_{h[1]}"] = _clip_inf(out[col] - ind_mean)

    # ── Step 7: stock-industry return rolling correlation (per stock) ──
    _follow = (daily_ret * out["ind_ret"]).groupby(out["ts_code"]).transform(
        lambda s: s.rolling(60, min_periods=20).mean()
    )
    _ret_var = (daily_ret ** 2).groupby(out["ts_code"]).transform(
        lambda s: s.rolling(60, min_periods=20).mean()
    ) ** 0.5
    _ind_var = (out["ind_ret"] ** 2).groupby(out["ts_code"]).transform(
        lambda s: s.rolling(60, min_periods=20).mean()
    ) ** 0.5
    out["stock_industry_ret_corr_60d"] = _clip_inf(
        _safe_div(_follow, _ret_var * _ind_var)
    )

    # Clean up intermediates
    for c in list(out.columns):
        if c.startswith("_"):
            out = out.drop(columns=[c], errors="ignore")

    return out
