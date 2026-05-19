"""Alpha V1 — label / z-score / IC 纯函数。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def cs_zscore(s: pd.Series, clip: float = 3.0) -> pd.Series:
    std = s.std(ddof=0)
    if pd.isna(std) or std < 1e-12:
        return pd.Series(0.0, index=s.index)
    return ((s - s.mean()) / std).clip(-clip, clip)


def robust_zscore_fit(X: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    center = X.median()
    scale = (X - center).abs().median().replace(0, 1.0)
    return center, scale


def robust_zscore_transform(X: pd.DataFrame, center: pd.Series, scale: pd.Series, clip: float = 3.0) -> pd.DataFrame:
    return ((X.astype(np.float32) - center) / scale).clip(-clip, clip).fillna(0.0)


def daily_ic(pred, target, groupby):
    df = pd.DataFrame({"pred": np.asarray(pred), "target": np.asarray(target), "g": np.asarray(groupby)})
    return df.dropna().groupby("g").apply(lambda g: g["pred"].corr(g["target"], method="spearman"))


def compute_ic_stats(ic_s):
    ic_s = ic_s.dropna()
    mean_ic = float(ic_s.mean())
    std_ic = float(ic_s.std())
    icir = mean_ic / std_ic if std_ic > 0 else 0.0
    pos = float((ic_s > 0).mean())
    return {"Mean_IC": mean_ic, "ICIR": icir, "Pos%": pos}


def make_zs_label(horizon: int):
    def label_fn(df):
        g = df.groupby("instrument")["$close"]
        fwd = g.shift(-horizon) / df["$close"] - 1.0
        temp = df.copy()
        temp["_r"] = np.asarray(fwd)
        return temp.groupby("trade_date")["_r"].transform(cs_zscore)

    return label_fn


def make_forward_returns(df: pd.DataFrame, horizons=(1, 5, 20)) -> None:
    g = df.groupby("instrument")["$close"]
    for h in horizons:
        df[f"fwd_{h}d"] = g.shift(-h) / df["$close"] - 1.0
