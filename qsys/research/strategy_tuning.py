from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

STRATEGY_VARIANTS = [
    {"strategy_variant": "k5_daily_b000", "top_k": 5, "rebalance_mode": "daily", "turnover_buffer": 0.0},
    {"strategy_variant": "k8_daily_b000", "top_k": 8, "rebalance_mode": "daily", "turnover_buffer": 0.0},
    {"strategy_variant": "k10_daily_b000", "top_k": 10, "rebalance_mode": "daily", "turnover_buffer": 0.0},
    {"strategy_variant": "k5_weekly_b000", "top_k": 5, "rebalance_mode": "weekly", "turnover_buffer": 0.0},
    {"strategy_variant": "k5_daily_b002", "top_k": 5, "rebalance_mode": "daily", "turnover_buffer": 0.02},
    {"strategy_variant": "k5_daily_b005", "top_k": 5, "rebalance_mode": "daily", "turnover_buffer": 0.05},
]


def summarize_variant_metrics(metrics: pd.DataFrame) -> dict[str, Any]:
    return {
        "rolling_window_count": int(len(metrics)),
        "rolling_total_return_mean": _stat(metrics, "total_return", "mean"),
        "rolling_rankic_mean": _stat(metrics, "RankIC", "mean"),
        "rolling_rankic_std": _stat(metrics, "RankIC", "std"),
        "rolling_max_drawdown_worst": _stat(metrics, "max_drawdown", "min"),
        "rolling_turnover_mean": _stat(metrics, "turnover", "mean"),
        "rolling_empty_portfolio_ratio_mean": _stat(metrics, "empty_portfolio_ratio", "mean"),
    }


def build_strategy_summary(rows: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    baseline_map = {
        row["strategy_variant"]: row
        for row in rows
        if row["mainline_object_name"] == "feature_173"
    }
    out = []
    for row in rows:
        base = baseline_map.get(row["strategy_variant"])
        delta_return = None
        delta_rankic = None
        if base is not None:
            if row.get("rolling_total_return_mean") is not None and base.get("rolling_total_return_mean") is not None:
                delta_return = round(float(row["rolling_total_return_mean"] - base["rolling_total_return_mean"]), 8)
            if row.get("rolling_rankic_mean") is not None and base.get("rolling_rankic_mean") is not None:
                delta_rankic = round(float(row["rolling_rankic_mean"] - base["rolling_rankic_mean"]), 8)
        out.append({**row, "vs_baseline_delta_return": delta_return, "vs_baseline_delta_rankic": delta_rankic})
    return pd.DataFrame(out)


def build_window_stability_summary(mainline_object_name: str, strategy_variant: str, metrics: pd.DataFrame) -> dict[str, Any]:
    total_return = pd.to_numeric(metrics["total_return"], errors="coerce")
    rankic = pd.to_numeric(metrics["RankIC"], errors="coerce")
    positive_return_ratio = float((total_return > 0).mean()) if len(metrics) else 0.0
    positive_rankic_ratio = float((rankic > 0).mean()) if len(metrics) else 0.0
    pos_sum = total_return.clip(lower=0).sum()
    top3_positive_return_share = float(metrics.sort_values("total_return", ascending=False).head(3)["total_return"].clip(lower=0).sum() / pos_sum) if pos_sum > 0 else 0.0
    return {
        "mainline_object_name": mainline_object_name,
        "strategy_variant": strategy_variant,
        "positive_return_window_ratio": round(positive_return_ratio, 8),
        "rankic_positive_window_ratio": round(positive_rankic_ratio, 8),
        "top3_positive_return_share": round(top3_positive_return_share, 8),
        "worst_3_windows": json.dumps(_window_rows(metrics.sort_values("total_return", ascending=True).head(3)), ensure_ascii=False),
        "best_3_windows": json.dumps(_window_rows(metrics.sort_values("total_return", ascending=False).head(3)), ensure_ascii=False),
    }


def markdown_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in df.iterrows():
        vals = ["" if pd.isna(v) else str(v) for v in row.tolist()]
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def write_df(path: str | Path, df: pd.DataFrame) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return str(path)


def write_text(path: str | Path, content: str) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


def _window_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    cols = ["window_id", "test_start", "test_end", "total_return", "RankIC"]
    rows = []
    for _, row in df.iterrows():
        item = {}
        for c in cols:
            v = row.get(c)
            if isinstance(v, float):
                item[c] = round(v, 8)
            else:
                item[c] = v
        rows.append(item)
    return rows


def _stat(df: pd.DataFrame, col: str, op: str) -> float | None:
    s = pd.to_numeric(df[col], errors="coerce").dropna()
    if s.empty:
        return None
    if op == "mean":
        v = s.mean()
    elif op == "std":
        v = s.std(ddof=0)
    elif op == "min":
        v = s.min()
    else:
        raise ValueError(op)
    return round(float(v), 8)
