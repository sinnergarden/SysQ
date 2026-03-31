from __future__ import annotations

import pandas as pd

from qsys.feature.definitions.common import merge_date_series


def _cross_section_spread(frame: pd.DataFrame, sort_col: str, value_col: str) -> float:
    valid = frame[[sort_col, value_col]].dropna()
    if valid.empty:
        return float("nan")
    rank = valid[sort_col].rank(pct=True, method="average")
    low = valid.loc[rank <= 0.3, value_col].mean()
    high = valid.loc[rank >= 0.7, value_col].mean()
    return low - high


def build_market_regime_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    ret_1d = out.groupby("ts_code", sort=False)["close"].pct_change(1)
    out["_ret_1d_tmp"] = ret_1d
    out["market_breadth"] = out.groupby("trade_date", sort=False)["_ret_1d_tmp"].transform(lambda s: (s > 0).mean())

    if "is_limit_up" in out.columns:
        out["limit_up_breadth"] = out.groupby("trade_date", sort=False)["is_limit_up"].transform("mean")
    else:
        out["limit_up_breadth"] = 0.0

    if "index_close" in out.columns and out["index_close"].notna().any():
        index_close = out.groupby("trade_date", sort=False)["index_close"].first().sort_index()
        index_ret = index_close.pct_change()
        out = merge_date_series(out, index_ret.rolling(5).std(), "index_volatility_5")
        out = merge_date_series(out, index_ret.rolling(10).std(), "index_volatility_10")
        out = merge_date_series(out, index_ret.rolling(20).std(), "index_volatility_20")
        out = merge_date_series(out, index_close / index_close.shift(20) - 1, "market_trend_strength")
    else:
        out["index_volatility_5"] = pd.NA
        out["index_volatility_10"] = pd.NA
        out["index_volatility_20"] = pd.NA
        out["market_trend_strength"] = pd.NA

    if "circ_mv" in out.columns:
        small_vs_large = out.groupby("trade_date", sort=False).apply(
            lambda frame: _cross_section_spread(frame, "circ_mv", "_ret_1d_tmp")
        )
        out = merge_date_series(out, small_vs_large, "small_vs_large_strength")
    else:
        out["small_vs_large_strength"] = pd.NA

    if "pb" in out.columns:
        growth_vs_value = out.groupby("trade_date", sort=False).apply(
            lambda frame: -_cross_section_spread(frame, "pb", "_ret_1d_tmp")
        )
        out = merge_date_series(out, growth_vs_value, "growth_vs_value_proxy")
    else:
        out["growth_vs_value_proxy"] = pd.NA

    return out.drop(columns=["_ret_1d_tmp"], errors="ignore")
