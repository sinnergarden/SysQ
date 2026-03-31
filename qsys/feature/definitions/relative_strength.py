from __future__ import annotations

import pandas as pd

from qsys.feature.definitions.common import merge_date_series


def build_relative_strength_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close_grp = out.groupby("ts_code", sort=False)["close"]
    volume_col = "volume" if "volume" in out.columns else "vol"
    volume_grp = out.groupby("ts_code", sort=False)[volume_col]
    amount_grp = out.groupby("ts_code", sort=False)["amount"]

    out["ret_1d"] = close_grp.pct_change(1)
    out["ret_3d"] = close_grp.pct_change(3)
    out["ret_5d"] = close_grp.pct_change(5)
    out["vol_mean_3d"] = volume_grp.transform(lambda s: s.rolling(3).mean())
    out["vol_mean_5d"] = volume_grp.transform(lambda s: s.rolling(5).mean())
    out["amount_mean_3d"] = amount_grp.transform(lambda s: s.rolling(3).mean())
    out["amount_mean_5d"] = amount_grp.transform(lambda s: s.rolling(5).mean())

    for column in [
        "ret_1d",
        "ret_3d",
        "ret_5d",
        "vol_mean_3d",
        "vol_mean_5d",
        "amount_mean_3d",
        "amount_mean_5d",
    ]:
        out[f"{column}_rank"] = out.groupby("trade_date", sort=False)[column].rank(pct=True, method="average")

    if "index_close" in out.columns and out["index_close"].notna().any():
        index_close = out.groupby("trade_date", sort=False)["index_close"].first().sort_index()
        out = merge_date_series(out, index_close.pct_change(3), "index_ret_3d")
        out = merge_date_series(out, index_close.pct_change(5), "index_ret_5d")
        out["stock_minus_index_ret_3d"] = out["ret_3d"] - out["index_ret_3d"]
        out["stock_minus_index_ret_5d"] = out["ret_5d"] - out["index_ret_5d"]
    else:
        out["stock_minus_index_ret_3d"] = pd.NA
        out["stock_minus_index_ret_5d"] = pd.NA

    return out
