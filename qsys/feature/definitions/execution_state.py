from __future__ import annotations

import numpy as np
import pandas as pd


def build_execution_state_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    high_limit_col = "high_limit" if "high_limit" in out.columns else "up_limit"
    low_limit_col = "low_limit" if "low_limit" in out.columns else "down_limit"

    high_limit = pd.to_numeric(out.get(high_limit_col), errors="coerce")
    low_limit = pd.to_numeric(out.get(low_limit_col), errors="coerce")
    close = pd.to_numeric(out.get("close"), errors="coerce")
    open_price = pd.to_numeric(out.get("open"), errors="coerce")

    out["is_limit_up"] = (high_limit > 0) & (close >= high_limit)
    out["is_limit_down"] = (low_limit > 0) & (close <= low_limit)
    out["distance_to_limit_up"] = (high_limit - close) / close.replace(0, np.nan)
    out["distance_to_limit_down"] = (close - low_limit) / close.replace(0, np.nan)
    out["limit_up_count_5d"] = out.groupby("ts_code", sort=False)["is_limit_up"].transform(lambda s: s.rolling(5).sum())
    out["opened_from_limit_up"] = out["is_limit_up"] & (open_price < high_limit)

    paused = pd.to_numeric(out.get("paused", 0), errors="coerce").fillna(0.0)
    tradability = np.ones(len(out), dtype=float)
    tradability -= paused.astype(float)
    tradability -= out["is_limit_up"].astype(float) * 0.5
    tradability -= out["is_limit_down"].astype(float) * 0.5
    out["tradability_score"] = np.clip(tradability, 0.0, 1.0)
    return out
