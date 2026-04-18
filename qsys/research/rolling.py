from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.research.decision import DEFAULT_DECISIONS_DIR, decision_payload, resolve_subject_decision
from qsys.research.mainline import MAINLINE_OBJECTS, MainlineObjectSpec


DEFAULT_MAINLINE_OBJECT_NAMES = [
    "feature_173",
    "feature_254",
    "feature_254_absnorm",
]
DEFAULT_TEST_WINDOW_DAYS = 63
DEFAULT_STEP_DAYS = 21


@dataclass(frozen=True)
class RollingWindow:
    window_id: str
    train_start: str | None
    train_end: str | None
    test_start: str
    test_end: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_id": self.window_id,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "test_start": self.test_start,
            "test_end": self.test_end,
        }


@dataclass(frozen=True)
class RollingDefaults:
    universe: str
    top_k: int
    strategy_type: str
    label_horizon: str
    test_window_days: int
    step_days: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "universe": self.universe,
            "top_k": self.top_k,
            "strategy_type": self.strategy_type,
            "label_horizon": self.label_horizon,
            "test_window_days": self.test_window_days,
            "step_days": self.step_days,
        }


def canonical_model_path(project_root: str | Path, spec: MainlineObjectSpec) -> Path:
    return Path(project_root) / "data" / "models" / spec.model_name


def resolve_mainline_specs(names: list[str] | tuple[str, ...] | None = None) -> list[MainlineObjectSpec]:
    selected = list(names or DEFAULT_MAINLINE_OBJECT_NAMES)
    specs: list[MainlineObjectSpec] = []
    for name in selected:
        spec = MAINLINE_OBJECTS.get(name)
        if spec is None:
            raise ValueError(f"Unknown mainline object: {name}")
        specs.append(spec)
    return specs


def snapshot_train_window(snapshot: dict[str, Any]) -> tuple[str | None, str | None]:
    split_spec = snapshot.get("split_spec") or {}
    train_start = split_spec.get("train_start") or snapshot.get("train_start") or snapshot.get("start")
    train_end = split_spec.get("train_end_effective") or split_spec.get("train_end") or snapshot.get("train_end") or snapshot.get("end")
    return _normalize_date(train_start), _normalize_date(train_end)


def build_rolling_windows(
    *,
    start: str,
    end: str,
    test_window_days: int = DEFAULT_TEST_WINDOW_DAYS,
    step_days: int = DEFAULT_STEP_DAYS,
    train_start: str | None = None,
    train_end: str | None = None,
) -> list[RollingWindow]:
    if test_window_days <= 0:
        raise ValueError("test_window_days must be positive")
    if step_days <= 0:
        raise ValueError("step_days must be positive")

    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    if end_ts < start_ts:
        raise ValueError("end must be on or after start")

    windows: list[RollingWindow] = []
    current_start = start_ts
    index = 1
    while current_start <= end_ts:
        current_end = min(current_start + pd.Timedelta(days=test_window_days - 1), end_ts)
        windows.append(
            RollingWindow(
                window_id=f"window_{index:03d}",
                train_start=_normalize_date(train_start),
                train_end=_normalize_date(train_end),
                test_start=current_start.strftime("%Y-%m-%d"),
                test_end=current_end.strftime("%Y-%m-%d"),
            )
        )
        if current_end >= end_ts:
            break
        current_start = current_start + pd.Timedelta(days=step_days)
        index += 1
    return windows


def compute_window_metrics(
    *,
    spec: MainlineObjectSpec,
    window: RollingWindow,
    daily_result: pd.DataFrame,
    signal_metrics: dict[str, Any],
) -> dict[str, Any]:
    total_return = _compute_total_return(daily_result)
    max_drawdown = _compute_max_drawdown(daily_result)
    turnover = _compute_turnover(daily_result)
    empty_portfolio_ratio = _compute_empty_portfolio_ratio(daily_result)
    avg_holding_count = _compute_avg_holding_count(daily_result)
    return {
        "mainline_object_name": spec.mainline_object_name,
        "bundle_id": spec.bundle_id,
        "legacy_feature_set_alias": spec.legacy_feature_set_alias,
        **window.to_dict(),
        "total_return": total_return,
        "max_drawdown": max_drawdown,
        "turnover": turnover,
        "IC": _to_float(signal_metrics.get("IC")),
        "RankIC": _to_float(signal_metrics.get("RankIC")),
        "long_short_spread": _to_float(signal_metrics.get("long_short_spread")),
        "empty_portfolio_ratio": empty_portfolio_ratio,
        "avg_holding_count": avg_holding_count,
    }


def build_rolling_summary(metrics_frame: pd.DataFrame, defaults: RollingDefaults) -> dict[str, Any]:
    if metrics_frame.empty:
        return {
            "rolling_window_count": 0,
            "defaults": defaults.to_dict(),
            "status": "no_windows",
        }

    return {
        "mainline_object_name": metrics_frame.iloc[0]["mainline_object_name"],
        "bundle_id": metrics_frame.iloc[0]["bundle_id"],
        "legacy_feature_set_alias": metrics_frame.iloc[0]["legacy_feature_set_alias"],
        "rolling_window_count": int(len(metrics_frame)),
        "rolling_total_return_mean": _series_stat(metrics_frame, "total_return", "mean"),
        "rolling_total_return_median": _series_stat(metrics_frame, "total_return", "median"),
        "rolling_rankic_mean": _series_stat(metrics_frame, "RankIC", "mean"),
        "rolling_rankic_std": _series_stat(metrics_frame, "RankIC", "std"),
        "rolling_max_drawdown_worst": _series_stat(metrics_frame, "max_drawdown", "min"),
        "rolling_turnover_mean": _series_stat(metrics_frame, "turnover", "mean"),
        "rolling_empty_portfolio_ratio_mean": _series_stat(metrics_frame, "empty_portfolio_ratio", "mean"),
        "defaults": defaults.to_dict(),
        "status": "ok",
    }


def build_comparison_summary(
    summaries: pd.DataFrame,
    *,
    decisions_dir: str | Path | None = DEFAULT_DECISIONS_DIR,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in summaries.to_dict(orient="records"):
        mainline_object_name = str(row.get("mainline_object_name") or "")
        decision = resolve_subject_decision(
            subject_type="mainline_object",
            subject_ids=[mainline_object_name],
            decisions_dir=decisions_dir,
        )
        payload = decision_payload(decision)
        rows.append(
            {
                "mainline_object_name": mainline_object_name,
                "bundle_id": row.get("bundle_id"),
                "legacy_feature_set_alias": row.get("legacy_feature_set_alias"),
                "rolling_window_count": row.get("rolling_window_count"),
                "rolling_total_return_mean": row.get("rolling_total_return_mean"),
                "rolling_total_return_median": row.get("rolling_total_return_median"),
                "rolling_rankic_mean": row.get("rolling_rankic_mean"),
                "rolling_rankic_std": row.get("rolling_rankic_std"),
                "rolling_max_drawdown_worst": row.get("rolling_max_drawdown_worst"),
                "rolling_turnover_mean": row.get("rolling_turnover_mean"),
                "rolling_empty_portfolio_ratio_mean": row.get("rolling_empty_portfolio_ratio_mean"),
                "decision_status": payload.get("status"),
                "decision_reason": payload.get("reason"),
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame[
        [
            "mainline_object_name",
            "bundle_id",
            "legacy_feature_set_alias",
            "rolling_window_count",
            "rolling_total_return_mean",
            "rolling_total_return_median",
            "rolling_rankic_mean",
            "rolling_rankic_std",
            "rolling_max_drawdown_worst",
            "rolling_turnover_mean",
            "rolling_empty_portfolio_ratio_mean",
            "decision_status",
            "decision_reason",
        ]
    ]


def best_and_worst(comparison_frame: pd.DataFrame) -> dict[str, dict[str, Any] | None]:
    if comparison_frame.empty:
        return {"best": None, "worst": None}
    ranked = comparison_frame.copy()
    ranked["_rankic_sort"] = pd.to_numeric(ranked["rolling_rankic_mean"], errors="coerce")
    ranked["_return_sort"] = pd.to_numeric(ranked["rolling_total_return_mean"], errors="coerce")
    ranked = ranked.sort_values(["_rankic_sort", "_return_sort"], ascending=[False, False], na_position="last")
    return {
        "best": ranked.iloc[0].drop(labels=["_rankic_sort", "_return_sort"]).to_dict(),
        "worst": ranked.iloc[-1].drop(labels=["_rankic_sort", "_return_sort"]).to_dict(),
    }


def comparison_markdown(comparison_frame: pd.DataFrame) -> str:
    lines = ["# Mainline rolling comparison", ""]
    if comparison_frame.empty:
        lines.append("- No rolling summaries found.")
        return "\n".join(lines)

    lines.append(_frame_to_markdown(comparison_frame))
    lines.append("")
    outcome = best_and_worst(comparison_frame)
    best = outcome["best"]
    worst = outcome["worst"]
    if best is not None:
        lines.append(f"- Best by rolling_rankic_mean: `{best['mainline_object_name']}`")
    if worst is not None:
        lines.append(f"- Worst by rolling_rankic_mean: `{worst['mainline_object_name']}`")
    return "\n".join(lines)


def decision_evidence_payload(
    comparison_row: dict[str, Any],
    *,
    comparison_source: str,
) -> dict[str, Any]:
    return {
        "rolling_window_count": _int_or_none(comparison_row.get("rolling_window_count")),
        "rolling_rankic_mean": _to_float(comparison_row.get("rolling_rankic_mean")),
        "rolling_rankic_std": _to_float(comparison_row.get("rolling_rankic_std")),
        "rolling_total_return_mean": _to_float(comparison_row.get("rolling_total_return_mean")),
        "rolling_max_drawdown_worst": _to_float(comparison_row.get("rolling_max_drawdown_worst")),
        "rolling_turnover_mean": _to_float(comparison_row.get("rolling_turnover_mean")),
        "comparison_source": comparison_source,
        "lineage": {
            "mainline_object_name": comparison_row.get("mainline_object_name"),
            "bundle_id": comparison_row.get("bundle_id"),
            "legacy_feature_set_alias": comparison_row.get("legacy_feature_set_alias"),
        },
    }


def _compute_total_return(frame: pd.DataFrame) -> float | None:
    if frame.empty or "total_assets" not in frame.columns:
        return None
    start = pd.to_numeric(frame["total_assets"], errors="coerce").dropna()
    if start.empty or start.iloc[0] == 0:
        return None
    return round(float(start.iloc[-1] / start.iloc[0] - 1.0), 8)


def _compute_max_drawdown(frame: pd.DataFrame) -> float | None:
    if frame.empty or "total_assets" not in frame.columns:
        return None
    equity = pd.to_numeric(frame["total_assets"], errors="coerce").dropna()
    if equity.empty:
        return None
    drawdown = equity / equity.cummax() - 1.0
    return round(float(drawdown.min()), 8)


def _compute_turnover(frame: pd.DataFrame) -> float | None:
    if frame.empty or "daily_turnover" not in frame.columns or "total_assets" not in frame.columns:
        return None
    base = frame[["daily_turnover", "total_assets"]].copy()
    base["daily_turnover"] = pd.to_numeric(base["daily_turnover"], errors="coerce")
    base["total_assets"] = pd.to_numeric(base["total_assets"], errors="coerce")
    base = base[(base["total_assets"].notna()) & (base["total_assets"] > 0)]
    if base.empty:
        return None
    return round(float((base["daily_turnover"] / base["total_assets"]).mean()), 8)


def _compute_empty_portfolio_ratio(frame: pd.DataFrame) -> float | None:
    if frame.empty or "position_count" not in frame.columns:
        return None
    position_count = pd.to_numeric(frame["position_count"], errors="coerce")
    valid = position_count.dropna()
    if valid.empty:
        return None
    return round(float((valid <= 0).mean()), 8)


def _compute_avg_holding_count(frame: pd.DataFrame) -> float | None:
    if frame.empty or "position_count" not in frame.columns:
        return None
    position_count = pd.to_numeric(frame["position_count"], errors="coerce").dropna()
    if position_count.empty:
        return None
    return round(float(position_count.mean()), 8)


def _series_stat(frame: pd.DataFrame, column: str, op: str) -> float | None:
    values = pd.to_numeric(frame[column], errors="coerce")
    valid = values.dropna()
    if valid.empty:
        return None
    if op == "mean":
        result = valid.mean()
    elif op == "median":
        result = valid.median()
    elif op == "std":
        result = valid.std(ddof=0)
    elif op == "min":
        result = valid.min()
    else:
        raise ValueError(f"Unsupported stat op: {op}")
    return round(float(result), 8)


def _frame_to_markdown(frame: pd.DataFrame) -> str:
    columns = list(frame.columns)
    rows = [["" if pd.isna(v) else str(v) for v in row] for row in frame.to_numpy().tolist()]
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join(["---"] * len(columns)) + " |"
    body = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join([header, sep, *body])


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 8)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_date(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    except Exception:
        return str(value)
