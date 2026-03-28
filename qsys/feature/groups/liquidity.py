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
    return out
