"""Industry momentum / theme proxy features.

All industry aggregations are first collapsed to a (industry, trade_date)
daily panel, then rolling windows are applied at the industry level.
The result is merged back to individual stocks.

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

    # Ensure sort order for rolling
    out = out.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

    _grp = ["trade_date", "industry"]

    # Daily return per stock
    ret_d = out.groupby("ts_code")["close"].pct_change()

    # ── Build industry daily panel ────────────────────────────────────
    ind_panel = out.groupby(["trade_date", "industry"]).agg(
        ind_ret=("close", lambda s: s.pct_change(1).mean()),
        ind_breadth=("close", lambda s: (s.pct_change(1) > 0).mean()),
        ind_near_high_ratio=("close", lambda s: _safe_div(s, s.rolling(120, min_periods=60).max()).gt(0.95).mean()),
        ind_volume_ratio=("amount", lambda s: _safe_div(
            s.rolling(20, min_periods=10).mean(),
            s.rolling(20, min_periods=10).mean().shift(20)
        ).mean()),
    ).reset_index()
    ind_panel = ind_panel.sort_values(["industry", "trade_date"]).reset_index(drop=True)

    # Rolling on industry panel
    for window, label in [(20, "20d"), (60, "60d"), (120, "120d")]:
        ind_panel[f"industry_ret_{label}"] = ind_panel.groupby("industry")["ind_ret"].transform(
            lambda s: s.rolling(window, min_periods=window // 2).mean()
        )

    for window, label in [(20, "20d"), (60, "60d")]:
        ind_panel[f"industry_breadth_{label}"] = ind_panel.groupby("industry")["ind_breadth"].transform(
            lambda s: s.rolling(window, min_periods=window // 2).mean()
        )

    ind_panel["industry_new_high_ratio"] = ind_panel.groupby("industry")["ind_near_high_ratio"].transform(
        lambda s: s.rolling(20, min_periods=10).mean()
    )

    ind_panel["industry_volume_expansion"] = ind_panel.groupby("industry")["ind_volume_ratio"].transform(
        lambda s: s.rolling(20, min_periods=10).mean()
    )

    # Top-stock momentum: for each industry×trade_date, mean of top 20% by ret_60d
    if "ret_60d" in out.columns:
        _r60 = out["ret_60d"].fillna(0)
        _top_mask = _r60.groupby(_grp).transform(lambda s: s >= s.quantile(0.8)).astype(float)
        top_df = out[["trade_date", "industry"]].copy()
        top_df["top_ret"] = _r60 * _top_mask
        top_panel = top_df.groupby(["trade_date", "industry"])["top_ret"].mean().reset_index()
        top_panel = top_panel.sort_values(["industry", "trade_date"]).reset_index(drop=True)
        top_panel["industry_top_stock_momentum"] = top_panel.groupby("industry")["top_ret"].transform(
            lambda s: s.rolling(60, min_periods=10).mean()
        )
        ind_panel = ind_panel.merge(top_panel[["trade_date", "industry", "industry_top_stock_momentum"]],
                                     on=["trade_date", "industry"], how="left")

    # ── Merge industry panel back to stock level ──────────────────────
    merge_cols = ["trade_date", "industry"] + [c for c in ind_panel.columns if c.startswith("industry_")] + ["ind_ret"]
    out = out.merge(ind_panel[merge_cols], on=["trade_date", "industry"], how="left")

    # ── Stock minus industry return (per-date截面 industry mean) ──────
    if "ret_60d" in out.columns:
        ind_r60 = out["ret_60d"].groupby(_grp).transform("mean")
        out["stock_minus_industry_ret_60d"] = _clip_inf(out["ret_60d"] - ind_r60)
    if "ret_20d" in out.columns:
        ind_r20 = out["ret_20d"].groupby(_grp).transform("mean")
        out["stock_minus_industry_ret_20d"] = _clip_inf(out["ret_20d"] - ind_r20)

    # ── Stock-industry return correlation (rolling 60d) ───────────────
    _follow = (ret_d * out["ind_ret"]).groupby(out["ts_code"]).transform(
        lambda s: s.rolling(60, min_periods=20).mean()
    )
    _ret_var = (ret_d ** 2).groupby(out["ts_code"]).transform(
        lambda s: s.rolling(60, min_periods=20).mean()
    ) ** 0.5
    _ind_var = (out["ind_ret"] ** 2).groupby(out["ts_code"]).transform(
        lambda s: s.rolling(60, min_periods=20).mean()
    ) ** 0.5
    _corr = _safe_div(_follow, _ret_var * _ind_var)
    out["stock_industry_ret_corr_60d"] = _clip_inf(_corr)

    # Clean up intermediates (keep ind_ret for correlation, drop the rest)
    for c in list(out.columns):
        if c.startswith("_") or c in ("ind_breadth", "ind_near_high_ratio", "ind_volume_ratio", "top_ret"):
            out = out.drop(columns=[c], errors="ignore")

    return out
