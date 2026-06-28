from __future__ import annotations

import numpy as np
import pandas as pd

from qsys.feature.transforms import rolling_zscore


def build_liquidity_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    ret_1d = out.groupby("ts_code")["close"].pct_change(fill_method=None)
    volume_col = "volume" if "volume" in out.columns else "vol"
    vol_grp = out.groupby("ts_code")[volume_col]
    to_grp = out.groupby("ts_code")["turnover_rate"] if "turnover_rate" in out.columns else None

    out["amount_log"] = np.log1p(out["amount"].clip(lower=0))
    out["amount_zscore_20"] = out.groupby("ts_code")["amount"].transform(lambda s: rolling_zscore(s, 20))
    out["volume_shock_3"] = out[volume_col] / vol_grp.transform(lambda s: s.rolling(3).mean())
    out["volume_shock_5"] = out[volume_col] / vol_grp.transform(lambda s: s.rolling(5).mean())
    if to_grp is not None:
        out["turnover_acceleration"] = out["turnover_rate"] - to_grp.shift(3)
    else:
        out["turnover_acceleration"] = np.nan
    out["illiquidity"] = ret_1d.abs() / out["amount"].replace(0, np.nan)

    # ── Industry-adjusted liquidity features ──
    # amount_log and turnover_rate normalized within (trade_date, industry)
    # This prevents high-amount industries (e.g. 证券, 白酒) from being
    # systematically over-predicted vs low-amount ones (e.g. 纺织, 钢铁)
    if "industry" in out.columns:
        ind_mean = out.groupby(["trade_date", "industry"])["amount_log"].transform("mean")
        ind_std = out.groupby(["trade_date", "industry"])["amount_log"].transform("std").replace(0, np.nan)
        out["amount_log_ind_zscore"] = (out["amount_log"] - ind_mean) / ind_std

        if "turnover_rate" in out.columns:
            tr_mean = out.groupby(["trade_date", "industry"])["turnover_rate"].transform("mean")
            tr_std = out.groupby(["trade_date", "industry"])["turnover_rate"].transform("std").replace(0, np.nan)
            out["turnover_rate_ind_zscore"] = (out["turnover_rate"] - tr_mean) / tr_std
    else:
        out["amount_log_ind_zscore"] = np.nan
        out["turnover_rate_ind_zscore"] = np.nan

    return out
