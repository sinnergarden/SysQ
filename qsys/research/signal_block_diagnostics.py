from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.feature.library import FeatureLibrary
from qsys.feature.registry import list_feature_groups
from qsys.research.ablation import summarize_rolling_metrics
from qsys.research.mainline import resolve_mainline_feature_config

TARGET_OBJECTS = ["feature_173", "feature_254_trimmed"]
BASELINE_COMPARISON_ROOT = Path("experiments/mainline_trimmed_compare")
ALLOWED_LABELS = {"positive", "neutral", "dilutive", "unclear"}


def build_block_mapping(mainline_object_names: list[str] | tuple[str, ...] | None = None) -> pd.DataFrame:
    objects = list(mainline_object_names or TARGET_OBJECTS)
    rows: list[dict[str, Any]] = []
    for object_name in objects:
        fields = resolve_mainline_feature_config(object_name)
        if fields is None:
            raise ValueError(f"Unknown mainline object: {object_name}")
        for field in fields:
            block_name, source_family, is_core, notes = classify_field_block(object_name, field)
            rows.append(
                {
                    "field_name": field,
                    "mainline_object_name": object_name,
                    "block_name": block_name,
                    "source_family": source_family,
                    "is_core_block": is_core,
                    "notes": notes,
                }
            )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.sort_values(["mainline_object_name", "block_name", "field_name"]).reset_index(drop=True)


def classify_field_block(mainline_object_name: str, field_name: str) -> tuple[str, str, bool, str]:
    feature_groups = list_feature_groups()
    for group_name, payload in feature_groups.items():
        if field_name in payload.get("features", []):
            notes = f"registry:{group_name}"
            return "semantic_context", f"feature_group.{group_name}", False, notes

    if field_name in FeatureLibrary.MARGIN_FIELDS:
        return "margin_raw", "feature_library.margin_fields", False, "feature_library:MARGIN_FIELDS"

    if field_name in FeatureLibrary.EXTENDED_RAW_FIELDS:
        return "fundamental_raw", "feature_library.extended_raw_fields", True, "feature_library:EXTENDED_RAW_FIELDS"

    alpha158_fields = set(FeatureLibrary.get_alpha158_config())
    raw_market_fields = {
        "$open",
        "$high",
        "$low",
        "$close",
        "$vwap",
        "$volume",
        "$amount",
        "$factor",
        "$turnover_rate",
        "$high_limit",
        "$low_limit",
    }
    if field_name in alpha158_fields or field_name in raw_market_fields:
        note = "feature_library:get_alpha158_config"
        if field_name in raw_market_fields and field_name not in alpha158_fields:
            note = "raw_market_field:bundled_with_alpha158_core"
        return "alpha158_core", "feature_library.alpha158", True, note

    return "unclassified", "fallback", False, "not matched by feature registry or feature library lists"


def build_drop_one_feature_configs(mapping: pd.DataFrame) -> dict[str, dict[str, list[str]]]:
    configs: dict[str, dict[str, list[str]]] = {}
    for object_name, object_frame in mapping.groupby("mainline_object_name"):
        configs[object_name] = {}
        all_fields = object_frame["field_name"].astype(str).tolist()
        for block_name, block_frame in object_frame.groupby("block_name"):
            block_fields = set(block_frame["field_name"].astype(str).tolist())
            configs[object_name][str(block_name)] = [field for field in all_fields if field not in block_fields]
    return configs


def load_baseline_summary(project_root: str | Path, object_name: str) -> dict[str, Any]:
    metrics_path = Path(project_root) / BASELINE_COMPARISON_ROOT / object_name / "rolling_metrics.csv"
    metrics = pd.read_csv(metrics_path)
    summary = summarize_rolling_metrics(metrics)
    summary["mainline_object_name"] = object_name
    summary["diagnostic_mode"] = "baseline"
    summary["block_name"] = "__baseline__"
    summary["signal_contribution_label"] = "unclear"
    summary["notes"] = "baseline_mainline_trimmed_compare"
    return summary


def build_diagnostic_summary(
    *,
    block_mapping: pd.DataFrame,
    experiment_summaries: list[dict[str, Any]],
    project_root: str | Path,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for object_name in TARGET_OBJECTS:
        rows.append(load_baseline_summary(project_root, object_name))
        baseline = rows[-1]
        object_blocks = block_mapping[block_mapping["mainline_object_name"] == object_name]
        block_meta = (
            object_blocks.groupby("block_name")
            .agg(
                source_family=("source_family", lambda s: ",".join(sorted(set(s.astype(str))))),
                is_core_block=("is_core_block", "max"),
            )
            .reset_index()
        )
        for _, block_row in block_meta.iterrows():
            block_name = str(block_row["block_name"])
            summary = next(
                (
                    item
                    for item in experiment_summaries
                    if item.get("mainline_object_name") == object_name and item.get("block_name") == block_name
                ),
                None,
            )
            if summary is None:
                raise ValueError(f"Missing experiment summary for {object_name}:{block_name}")
            note = _build_notes(
                baseline=baseline,
                summary=summary,
                source_family=str(block_row["source_family"]),
                is_core_block=bool(block_row["is_core_block"]),
            )
            rows.append(
                {
                    "mainline_object_name": object_name,
                    "diagnostic_mode": "drop_one",
                    "block_name": block_name,
                    "rolling_total_return_mean": summary.get("rolling_total_return_mean"),
                    "rolling_rankic_mean": summary.get("rolling_rankic_mean"),
                    "rolling_rankic_std": summary.get("rolling_rankic_std"),
                    "rolling_max_drawdown_worst": summary.get("rolling_max_drawdown_worst"),
                    "signal_contribution_label": assign_signal_contribution_label(baseline=baseline, summary=summary),
                    "notes": note,
                }
            )
    frame = pd.DataFrame(rows)
    columns = [
        "mainline_object_name",
        "diagnostic_mode",
        "block_name",
        "rolling_total_return_mean",
        "rolling_rankic_mean",
        "rolling_rankic_std",
        "rolling_max_drawdown_worst",
        "signal_contribution_label",
        "notes",
    ]
    return frame[columns].sort_values(["mainline_object_name", "diagnostic_mode", "block_name"]).reset_index(drop=True)


def assign_signal_contribution_label(*, baseline: dict[str, Any], summary: dict[str, Any]) -> str:
    delta_rankic = _safe_delta(summary.get("rolling_rankic_mean"), baseline.get("rolling_rankic_mean"))
    delta_return = _safe_delta(summary.get("rolling_total_return_mean"), baseline.get("rolling_total_return_mean"))
    if delta_rankic is None and delta_return is None:
        return "unclear"

    score = 0
    if delta_rankic is not None:
        if delta_rankic <= -0.003:
            score += 2
        elif delta_rankic <= -0.001:
            score += 1
        elif delta_rankic >= 0.003:
            score -= 2
        elif delta_rankic >= 0.001:
            score -= 1
    if delta_return is not None:
        if delta_return <= -0.01:
            score += 1
        elif delta_return >= 0.01:
            score -= 1

    if score >= 2:
        return "positive"
    if score <= -2:
        return "dilutive"
    return "neutral"


def render_markdown(summary: pd.DataFrame, mapping: pd.DataFrame) -> str:
    lines = ["# Mainline signal block diagnostics", ""]
    for object_name in TARGET_OBJECTS:
        obj_summary = summary[summary["mainline_object_name"] == object_name].copy()
        baseline = obj_summary[obj_summary["diagnostic_mode"] == "baseline"]
        drop_one = obj_summary[obj_summary["diagnostic_mode"] == "drop_one"].copy()
        lines.append(f"## {object_name}")
        lines.append("")
        if not baseline.empty:
            row = baseline.iloc[0]
            lines.append(
                f"- baseline: total_return_mean={row['rolling_total_return_mean']}, rankic_mean={row['rolling_rankic_mean']}, max_drawdown_worst={row['rolling_max_drawdown_worst']}"
            )
        strongest = drop_one[drop_one["signal_contribution_label"] == "positive"].head(3)
        dilutive = drop_one[drop_one["signal_contribution_label"] == "dilutive"].head(3)
        neutral = drop_one[drop_one["signal_contribution_label"] == "neutral"].head(3)
        if not strongest.empty:
            lines.append("- strongest_blocks: " + ", ".join(strongest["block_name"].astype(str).tolist()))
        if not dilutive.empty:
            lines.append("- dilutive_blocks: " + ", ".join(dilutive["block_name"].astype(str).tolist()))
        if not neutral.empty:
            lines.append("- neutral_or_weak_blocks: " + ", ".join(neutral["block_name"].astype(str).tolist()))
        block_counts = mapping[mapping["mainline_object_name"] == object_name].groupby("block_name").size().to_dict()
        lines.append("- block_sizes: " + json.dumps(block_counts, ensure_ascii=False, sort_keys=True))
        lines.append("")
        lines.append(_markdown_table(drop_one))
        lines.append("")
    return "\n".join(lines)


def write_outputs(
    *,
    output_dir: str | Path,
    block_mapping: pd.DataFrame,
    diagnostic_summary: pd.DataFrame,
) -> dict[str, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    mapping_path = out / "block_mapping.csv"
    summary_csv_path = out / "block_diagnostic_summary.csv"
    summary_md_path = out / "block_diagnostic_summary.md"
    block_mapping.to_csv(mapping_path, index=False)
    diagnostic_summary.to_csv(summary_csv_path, index=False)
    summary_md_path.write_text(render_markdown(diagnostic_summary, block_mapping), encoding="utf-8")
    return {
        "block_mapping": str(mapping_path),
        "block_diagnostic_summary_csv": str(summary_csv_path),
        "block_diagnostic_summary_md": str(summary_md_path),
    }


def _build_notes(*, baseline: dict[str, Any], summary: dict[str, Any], source_family: str, is_core_block: bool) -> str:
    delta_rankic = _safe_delta(summary.get("rolling_rankic_mean"), baseline.get("rolling_rankic_mean"))
    delta_return = _safe_delta(summary.get("rolling_total_return_mean"), baseline.get("rolling_total_return_mean"))
    parts = [
        f"source_family={source_family}",
        f"is_core_block={str(is_core_block).lower()}",
        f"delta_rankic={_fmt(delta_rankic)}",
        f"delta_total_return={_fmt(delta_return)}",
    ]
    return "; ".join(parts)


def _safe_delta(value: Any, base: Any) -> float | None:
    try:
        if value is None or base is None:
            return None
        return round(float(value) - float(base), 8)
    except (TypeError, ValueError):
        return None


def _fmt(value: float | None) -> str:
    if value is None:
        return "na"
    return f"{value:.6f}"


def _markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "- no_drop_one_rows"
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in df.iterrows():
        values = ["" if pd.isna(v) else str(v) for v in row.tolist()]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)
