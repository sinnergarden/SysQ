from __future__ import annotations

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
    return out
