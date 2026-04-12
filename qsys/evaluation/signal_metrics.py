from __future__ import annotations

import math
from typing import Any

import pandas as pd


def _safe_corr(a: pd.Series, b: pd.Series, method: str = "pearson") -> float | None:
    if len(a) < 2 or len(b) < 2:
        return None
    if a.nunique(dropna=True) < 2 or b.nunique(dropna=True) < 2:
        return None
    value = a.corr(b, method=method)
    return None if pd.isna(value) else float(value)


def compute_signal_metrics(signal_panel: pd.DataFrame, label_horizon: str = "1d_fixed_in_v1_impl1") -> dict[str, Any]:
    if signal_panel.empty:
        return {"status": "not_available_in_flow", "label_horizon": label_horizon}
    work = signal_panel.dropna(subset=["signal_value", "forward_return"]).copy()
    if work.empty:
        return {"status": "not_available_in_flow", "label_horizon": label_horizon}
    daily_ic = work.groupby("date").apply(
        lambda g: _safe_corr(g["signal_value"], g["forward_return"], method="pearson"),
        include_groups=False,
    ).dropna()
    daily_rank_ic = work.groupby("date").apply(
        lambda g: _safe_corr(g["signal_value"], g["forward_return"], method="spearman"),
        include_groups=False,
    ).dropna()
    long_short = work.groupby("date").apply(_compute_long_short, include_groups=False).dropna()
    metrics = {
        "status": "available",
        "IC": float(daily_ic.mean()) if not daily_ic.empty else None,
        "RankIC": float(daily_rank_ic.mean()) if not daily_rank_ic.empty else None,
        "ICIR": _information_ratio(daily_ic),
        "RankICIR": _information_ratio(daily_rank_ic),
        "long_short_spread": float(long_short.mean()) if not long_short.empty else None,
        "days": int(work["date"].nunique()),
        "label_horizon": label_horizon,
    }
    return metrics


def _information_ratio(values: pd.Series) -> float | None:
    if values.empty:
        return None
    std = values.std()
    if pd.isna(std) or std == 0:
        return None
    return float(values.mean() / std * math.sqrt(252))


def _compute_long_short(group: pd.DataFrame) -> float | None:
    if len(group) < 5:
        return None
    ranked = group.sort_values("signal_value", ascending=False)
    top = ranked.head(max(1, len(ranked) // 5))["forward_return"].mean()
    bottom = ranked.tail(max(1, len(ranked) // 5))["forward_return"].mean()
    value = top - bottom
    return None if pd.isna(value) else float(value)


def compute_group_returns(signal_panel: pd.DataFrame, label_horizon: str = "1d_fixed_in_v1_impl1") -> pd.DataFrame:
    if signal_panel.empty:
        return pd.DataFrame(columns=["date", "group", "mean_return", "nav", "label_horizon"])
    work = signal_panel.dropna(subset=["signal_value", "forward_return"]).copy()
    if work.empty:
        return pd.DataFrame(columns=["date", "group", "mean_return", "nav", "label_horizon"])
    rows: list[dict[str, Any]] = []
    for date, group in work.groupby("date"):
        if len(group) < 5:
            continue
        ranked = group.sort_values("signal_value", ascending=False).reset_index(drop=True)
        ranked["bucket"] = pd.qcut(ranked.index + 1, 5, labels=[1, 2, 3, 4, 5])
        for bucket, bucket_df in ranked.groupby("bucket", observed=False):
            rows.append({
                "date": date,
                "group": int(bucket),
                "mean_return": float(bucket_df["forward_return"].mean()),
                "label_horizon": label_horizon,
            })
    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=["date", "group", "mean_return", "nav", "label_horizon"])
    out = out.sort_values(["group", "date"])
    out["nav"] = out.groupby("group")["mean_return"].transform(lambda s: (1 + s).cumprod())
    return out
