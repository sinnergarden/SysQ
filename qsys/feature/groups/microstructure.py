from __future__ import annotations

import numpy as np
import pandas as pd


def build_microstructure_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    prev_close = out.groupby("ts_code")["close"].shift(1)
    intraday_range = (out["high"] - out["low"]).replace(0, np.nan)

    out["close_to_open_gap_1d"] = out["open"] / prev_close - 1
    out["open_to_close_ret"] = out["close"] / out["open"] - 1
    out["close_pos_in_range"] = (out["close"] - out["low"]) / intraday_range
    out["open_pos_in_range"] = (out["open"] - out["low"]) / intraday_range
    out["upper_shadow_ratio"] = (out["high"] - np.maximum(out["open"], out["close"])) / intraday_range
    out["lower_shadow_ratio"] = (np.minimum(out["open"], out["close"]) - out["low"]) / intraday_range
    out["intraday_reversal_strength"] = -(out["close"] / prev_close - 1) * (out["close"] / out["open"] - 1)
    return out
