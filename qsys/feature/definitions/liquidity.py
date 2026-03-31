from __future__ import annotations

import numpy as np
import pandas as pd

from qsys.feature.transforms import rolling_zscore


def build_liquidity_capacity_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    amount = pd.to_numeric(out.get("amount"), errors="coerce")
    volume_col = "volume" if "volume" in out.columns else "vol"
    volume = pd.to_numeric(out.get(volume_col), errors="coerce")
    turnover = pd.to_numeric(out.get("turnover_rate"), errors="coerce")
    ret_1d = out.groupby("ts_code", sort=False)["close"].pct_change(fill_method=None)

    out["amount_log"] = np.log1p(amount.clip(lower=0))
    out["amount_zscore_20"] = amount.groupby(out["ts_code"]).transform(lambda s: rolling_zscore(s, 20))
    out["volume_shock_3"] = volume / volume.groupby(out["ts_code"]).transform(lambda s: s.rolling(3).mean())
    out["volume_shock_5"] = volume / volume.groupby(out["ts_code"]).transform(lambda s: s.rolling(5).mean())
    out["turnover_acceleration"] = turnover - turnover.groupby(out["ts_code"]).shift(3)
    out["illiquidity"] = ret_1d.abs() / amount.replace(0, np.nan)
    return out
