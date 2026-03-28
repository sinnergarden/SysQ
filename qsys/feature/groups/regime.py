from __future__ import annotations

import pandas as pd


def build_regime_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    by_date = out.groupby("trade_date")

    ret_1d = out.groupby("ts_code")["close"].pct_change(1)
    out["_ret_1d_tmp"] = ret_1d
    out["market_breadth"] = by_date["_ret_1d_tmp"].transform(lambda s: (s > 0).mean())
    out["limit_up_breadth"] = by_date["is_limit_up"].transform("mean") if "is_limit_up" in out.columns else 0.0

    if "index_close" in out.columns:
        idx_close = by_date["index_close"].transform("first")
        idx_ret = idx_close.groupby(out["trade_date"]).transform("first")
        out["index_volatility_5"] = idx_close.pct_change().rolling(5).std()
        out["index_volatility_10"] = idx_close.pct_change().rolling(10).std()
        out["index_volatility_20"] = idx_close.pct_change().rolling(20).std()
        out["market_trend_strength"] = idx_close / idx_close.shift(20) - 1
    else:
        out["index_volatility_5"] = pd.NA
        out["index_volatility_10"] = pd.NA
        out["index_volatility_20"] = pd.NA
        out["market_trend_strength"] = pd.NA

    if "circ_mv" in out.columns:
        size_rank = by_date["circ_mv"].rank(pct=True)
        small = ret_1d.where(size_rank <= 0.3)
        large = ret_1d.where(size_rank >= 0.7)
        out["small_vs_large_strength"] = by_date.apply(lambda g: (g.loc[g['circ_mv'].rank(pct=True) <= 0.3, '_ret_1d_tmp'].mean() - g.loc[g['circ_mv'].rank(pct=True) >= 0.7, '_ret_1d_tmp'].mean())).reset_index(level=0, drop=True)
    else:
        out["small_vs_large_strength"] = pd.NA

    if "pb" in out.columns:
        value_rank = by_date["pb"].rank(pct=True)
        growth_proxy = ret_1d.where(value_rank >= 0.7)
        value_proxy = ret_1d.where(value_rank <= 0.3)
        out["growth_vs_value_proxy"] = by_date.apply(lambda g: (g.loc[g['pb'].rank(pct=True) >= 0.7, '_ret_1d_tmp'].mean() - g.loc[g['pb'].rank(pct=True) <= 0.3, '_ret_1d_tmp'].mean())).reset_index(level=0, drop=True)
    else:
        out["growth_vs_value_proxy"] = pd.NA

    out = out.drop(columns=["_ret_1d_tmp"], errors="ignore")
    return out
