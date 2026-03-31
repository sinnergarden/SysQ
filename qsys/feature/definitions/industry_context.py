from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from qsys.feature.definitions.common import resolve_data_root


INDUSTRY_FEATURE_COLUMNS = [
    "industry_ret_1d",
    "industry_ret_3d",
    "industry_ret_5d",
    "industry_breadth",
    "stock_minus_industry_ret",
    "stock_minus_industry_ret_3d",
    "stock_minus_industry_ret_5d",
]


def attach_industry_info(df: pd.DataFrame, meta_db_path: str | Path | None = None) -> pd.DataFrame:
    out = df.copy()
    if "industry" in out.columns and out["industry"].notna().any():
        return out

    meta_db = Path(meta_db_path) if meta_db_path is not None else resolve_data_root() / "meta.db"
    if not meta_db.exists():
        out["industry"] = out.get("industry", pd.Series(pd.NA, index=out.index))
        return out

    with sqlite3.connect(meta_db) as conn:
        stock_basic = pd.read_sql("select ts_code, industry from stock_basic", conn)

    if stock_basic.empty:
        out["industry"] = out.get("industry", pd.Series(pd.NA, index=out.index))
        return out

    return out.merge(stock_basic, on="ts_code", how="left")


def build_industry_context_features(df: pd.DataFrame, meta_db_path: str | Path | None = None) -> pd.DataFrame:
    out = attach_industry_info(df, meta_db_path=meta_db_path)
    if "industry" not in out.columns or out["industry"].isna().all():
        for column in INDUSTRY_FEATURE_COLUMNS:
            out[column] = pd.NA
        return out

    ret_1d = out.groupby("ts_code", sort=False)["close"].pct_change(1)
    ret_3d = out.groupby("ts_code", sort=False)["close"].pct_change(3)
    ret_5d = out.groupby("ts_code", sort=False)["close"].pct_change(5)

    grouped = out.groupby(["trade_date", "industry"])
    out["_ret_1d_tmp"] = ret_1d
    out["_ret_3d_tmp"] = ret_3d
    out["_ret_5d_tmp"] = ret_5d
    out["industry_ret_1d"] = grouped["_ret_1d_tmp"].transform("mean")
    out["industry_ret_3d"] = grouped["_ret_3d_tmp"].transform("mean")
    out["industry_ret_5d"] = grouped["_ret_5d_tmp"].transform("mean")
    out["industry_breadth"] = grouped["_ret_1d_tmp"].transform(lambda s: (s > 0).mean())
    out["stock_minus_industry_ret"] = out["_ret_1d_tmp"] - out["industry_ret_1d"]
    out["stock_minus_industry_ret_3d"] = out["_ret_3d_tmp"] - out["industry_ret_3d"]
    out["stock_minus_industry_ret_5d"] = out["_ret_5d_tmp"] - out["industry_ret_5d"]
    return out.drop(columns=["_ret_1d_tmp", "_ret_3d_tmp", "_ret_5d_tmp"], errors="ignore")
