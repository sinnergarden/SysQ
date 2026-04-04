from __future__ import annotations

import numpy as np
import pandas as pd


def build_fundamental_context_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    if "total_mv" in out.columns:
        out["log_mktcap"] = np.log(out["total_mv"].replace(0, np.nan))
    if "circ_mv" in out.columns:
        out["float_mktcap"] = out["circ_mv"]
    if "pe" in out.columns:
        out["pe_ttm"] = out["pe"]
    if "pb" in out.columns:
        out["pb_raw"] = out["pb"]
    if "ps_ttm" not in out.columns and "ps" in out.columns:
        out["ps_ttm"] = out["ps"]

    if {"net_income", "revenue"}.issubset(out.columns):
        out["net_margin"] = out["net_income"] / out["revenue"].replace(0, np.nan)
    if {"op_cashflow", "net_income"}.issubset(out.columns):
        out["operating_cf_to_profit"] = out["op_cashflow"] / out["net_income"].replace(0, np.nan)
    if {"net_income", "total_assets"}.issubset(out.columns):
        out["roa"] = out["net_income"] / out["total_assets"].replace(0, np.nan)

    if "grossprofit_margin" in out.columns and "gross_margin" not in out.columns:
        out["gross_margin"] = out["grossprofit_margin"]
    if "debt_to_assets" in out.columns and "debt_to_asset" not in out.columns:
        out["debt_to_asset"] = out["debt_to_assets"]

    for base_col, new_col in [
        ("revenue", "revenue_yoy"),
        ("net_income", "profit_yoy"),
    ]:
        if base_col in out.columns:
            prev = out.groupby("ts_code")[base_col].shift(252)
            out[new_col] = out[base_col] / prev.replace(0, np.nan) - 1

    if "inventory" in out.columns:
        prev = out.groupby("ts_code")["inventory"].shift(252)
        out["inventory_yoy"] = out["inventory"] / prev.replace(0, np.nan) - 1
    if "accounts_receiv" in out.columns:
        prev = out.groupby("ts_code")["accounts_receiv"].shift(252)
        out["ar_yoy"] = out["accounts_receiv"] / prev.replace(0, np.nan) - 1

    return out
