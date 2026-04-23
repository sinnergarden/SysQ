from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.research.mainline import MAINLINE_OBJECTS

DEFAULT_BAD_FIELDS = [
    "market_breadth",
    "limit_up_breadth",
    "small_vs_large_strength",
    "growth_vs_value_proxy",
    "ps_ttm",
]


def build_add_back_feature_config(base_fields: list[str], added_back_field: str) -> list[str]:
    fields = list(base_fields)
    if added_back_field not in fields:
        fields.append(added_back_field)
    return fields


def summarize_rolling_metrics(metrics: pd.DataFrame) -> dict[str, Any]:
    return {
        "rolling_window_count": int(len(metrics)),
        "rolling_total_return_mean": _mean(metrics, "total_return"),
        "rolling_rankic_mean": _mean(metrics, "RankIC"),
        "rolling_rankic_std": _std(metrics, "RankIC"),
        "rolling_max_drawdown_worst": _min(metrics, "max_drawdown"),
        "rolling_turnover_mean": _mean(metrics, "turnover"),
    }


def build_window_stability_summary(object_name: str, metrics: pd.DataFrame) -> dict[str, Any]:
    total = len(metrics)
    positive_return_ratio = float((pd.to_numeric(metrics["total_return"], errors="coerce") > 0).mean()) if total else 0.0
    positive_rankic_ratio = float((pd.to_numeric(metrics["RankIC"], errors="coerce") > 0).mean()) if total else 0.0
    ranked_best = metrics.sort_values("total_return", ascending=False).head(3)
    ranked_worst = metrics.sort_values("total_return", ascending=True).head(3)
    top3_positive_share = float(ranked_best["total_return"].clip(lower=0).sum() / metrics["total_return"].clip(lower=0).sum()) if total and metrics["total_return"].clip(lower=0).sum() > 0 else 0.0
    concentrated = top3_positive_share >= 0.6
    return {
        "mainline_object_name": object_name,
        "positive_return_window_ratio": round(positive_return_ratio, 8),
        "rankic_positive_window_ratio": round(positive_rankic_ratio, 8),
        "best_3_windows": _window_list(ranked_best),
        "worst_3_windows": _window_list(ranked_worst),
        "improvement_concentrated_in_few_windows": concentrated,
        "top3_positive_return_share": round(top3_positive_share, 8),
    }


def write_ablation_summary_csv(path: str | Path, rows: list[dict[str, Any]]) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return str(path)


def write_stability_summary_csv(path: str | Path, rows: list[dict[str, Any]]) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    flat_rows: list[dict[str, Any]] = []
    for row in rows:
        flat_rows.append(
            {
                "mainline_object_name": row["mainline_object_name"],
                "positive_return_window_ratio": row["positive_return_window_ratio"],
                "rankic_positive_window_ratio": row["rankic_positive_window_ratio"],
                "best_3_windows": json.dumps(row["best_3_windows"], ensure_ascii=False),
                "worst_3_windows": json.dumps(row["worst_3_windows"], ensure_ascii=False),
                "improvement_concentrated_in_few_windows": row["improvement_concentrated_in_few_windows"],
                "top3_positive_return_share": row["top3_positive_return_share"],
            }
        )
    pd.DataFrame(flat_rows).to_csv(path, index=False)
    return str(path)


def markdown_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in df.iterrows():
        vals = ["" if pd.isna(v) else str(v) for v in row.tolist()]
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def write_markdown(path: str | Path, content: str) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


def _window_list(df: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for _, row in df.iterrows():
        rows.append({
            "window_id": row.get("window_id"),
            "test_start": row.get("test_start"),
            "test_end": row.get("test_end"),
            "total_return": _num(row.get("total_return")),
            "RankIC": _num(row.get("RankIC")),
        })
    return rows


def _mean(df: pd.DataFrame, col: str) -> float | None:
    s = pd.to_numeric(df[col], errors="coerce")
    return round(float(s.mean()), 8) if not s.dropna().empty else None


def _std(df: pd.DataFrame, col: str) -> float | None:
    s = pd.to_numeric(df[col], errors="coerce")
    return round(float(s.std(ddof=0)), 8) if not s.dropna().empty else None


def _min(df: pd.DataFrame, col: str) -> float | None:
    s = pd.to_numeric(df[col], errors="coerce")
    return round(float(s.min()), 8) if not s.dropna().empty else None


def _num(v: Any) -> float | None:
    try:
        return round(float(v), 8)
    except Exception:
        return None
