from __future__ import annotations

import numpy as np
import pandas as pd


def build_tradability_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    high_limit_col = "high_limit" if "high_limit" in out.columns else "up_limit"
    low_limit_col = "low_limit" if "low_limit" in out.columns else "down_limit"
    out["is_limit_up"] = (out[high_limit_col] > 0) & (out["close"] >= out[high_limit_col])
    out["is_limit_down"] = (out[low_limit_col] > 0) & (out["close"] <= out[low_limit_col])
    out["distance_to_limit_up"] = (out[high_limit_col] - out["close"]) / out["close"].replace(0, np.nan)
    out["distance_to_limit_down"] = (out["close"] - out[low_limit_col]) / out["close"].replace(0, np.nan)
    out["limit_up_count_5d"] = out.groupby("ts_code")["is_limit_up"].transform(lambda s: s.rolling(5).sum())
    out["opened_from_limit_up"] = out["is_limit_up"] & (out["open"] < out[high_limit_col])

    tradability = np.ones(len(out), dtype=float)
    tradability -= out.get("paused", 0).fillna(0).astype(float)
    tradability -= out["is_limit_up"].astype(float) * 0.5
    tradability -= out["is_limit_down"].astype(float) * 0.5
    out["tradability_score"] = np.clip(tradability, 0.0, 1.0)
    return out
