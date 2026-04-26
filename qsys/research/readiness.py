from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.data.adapter import QlibAdapter
from qsys.research.mainline import MAINLINE_OBJECTS, MainlineObjectSpec, resolve_mainline_feature_config
from qsys.strategy.generator import SignalGenerator

CORE_OK = "core_ok"
EXTENDED_WARN = "extended_warn"
EXTENDED_BLOCKED = "extended_blocked"

DEFAULT_MAINLINE_READINESS_NAMES = [
    "feature_173",
    "feature_254",
    "feature_254_absnorm",
]

RAW_FIELD_PATTERN = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class ReadinessThresholds:
    usable_coverage_ratio: float = 0.7
    warning_coverage_ratio: float = 0.4
    constant_ratio_threshold: float = 0.95
    zero_ratio_warn_threshold: float = 0.98


def resolve_mainline_specs(names: list[str] | tuple[str, ...] | None = None) -> list[MainlineObjectSpec]:
    selected = list(names or DEFAULT_MAINLINE_READINESS_NAMES)
    specs: list[MainlineObjectSpec] = []
    for name in selected:
        spec = MAINLINE_OBJECTS.get(name)
        if spec is None:
            raise ValueError(f"Unknown mainline object: {name}")
        specs.append(spec)
    return specs


def fetch_mainline_feature_frame(
    *,
    spec: MainlineObjectSpec,
    start: str,
    end: str,
    universe: str,
) -> pd.DataFrame:
    fields = resolve_mainline_feature_config(spec.mainline_object_name)
    if not fields:
        raise ValueError(f"No feature config found for {spec.mainline_object_name}")
    adapter = QlibAdapter()
    adapter.init_qlib()
    return adapter.get_features(universe, fields, start_time=start, end_time=end)


def build_model_input_frame(*, feature_frame: pd.DataFrame, model_path: str | Path | None) -> pd.DataFrame:
    if feature_frame is None:
        return pd.DataFrame()
    if model_path is None:
        return feature_frame.copy()
    model_dir = Path(model_path)
    if not model_dir.exists():
        return feature_frame.copy()
    generator = SignalGenerator(model_dir)
    feature_config = list(generator.model.feature_config or [])
    if not feature_config:
        return feature_frame.copy()
    if any(field not in feature_frame.columns for field in feature_config):
        return feature_frame.copy()
    return generator.model._apply_preprocess(feature_frame[feature_config].copy())


def build_feature_coverage(
    *,
    spec: MainlineObjectSpec,
    frame: pd.DataFrame,
    thresholds: ReadinessThresholds | None = None,
    model_input_frame: pd.DataFrame | None = None,
) -> pd.DataFrame:
    thresholds = thresholds or ReadinessThresholds()
    rows: list[dict[str, Any]] = []
    feature_fields = resolve_mainline_feature_config(spec.mainline_object_name) or []
    total_rows = len(frame.index) if frame is not None else 0
    uses_model_input = model_input_frame is not None and not model_input_frame.empty
    coverage_frame = model_input_frame if uses_model_input else frame
    coverage_total_rows = len(coverage_frame.index) if coverage_frame is not None else total_rows

    for field in feature_fields:
        if coverage_frame is None or coverage_frame.empty or field not in coverage_frame.columns:
            rows.append(_missing_field_row(spec, field, coverage_total_rows, notes="field_missing_from_frame"))
            continue
        series = pd.to_numeric(coverage_frame[field], errors="coerce")
        if coverage_total_rows == 0:
            rows.append(_missing_field_row(spec, field, coverage_total_rows, notes="empty_frame"))
            continue
        non_na = series.notna().sum()
        coverage_ratio = float(non_na / coverage_total_rows)
        missing_ratio = float(1.0 - coverage_ratio)
        zero_ratio = float((series.fillna(0.0) == 0).mean()) if coverage_total_rows else None
        constant_ratio = _constant_ratio(coverage_frame, field)
        degradation_level = _field_degradation_level(
            coverage_ratio=coverage_ratio,
            constant_ratio=constant_ratio,
            thresholds=thresholds,
            is_core=spec.mainline_object_name == "feature_173",
            uses_model_input=uses_model_input,
        )
        usable_for_train = bool(
            coverage_ratio >= thresholds.usable_coverage_ratio
            and (uses_model_input or constant_ratio < thresholds.constant_ratio_threshold)
        )
        notes = _field_notes(
            coverage_ratio=coverage_ratio,
            constant_ratio=constant_ratio,
            zero_ratio=zero_ratio,
            degradation_level=degradation_level,
            thresholds=thresholds,
        )
        rows.append(
            {
                "mainline_object_name": spec.mainline_object_name,
                "bundle_id": spec.bundle_id,
                "legacy_feature_set_alias": spec.legacy_feature_set_alias,
                "field_name": field,
                "coverage_ratio": round(coverage_ratio, 8),
                "missing_ratio": round(missing_ratio, 8),
                "constant_ratio": round(constant_ratio, 8),
                "zero_ratio": round(zero_ratio, 8) if zero_ratio is not None else None,
                "usable_for_train": usable_for_train,
                "degradation_level": degradation_level,
                "notes": notes,
                "coverage_source": "model_input" if uses_model_input else "raw_feature_frame",
            }
        )
    return pd.DataFrame(rows)


def build_missingness_summary(coverage: pd.DataFrame) -> dict[str, Any]:
    if coverage.empty:
        return {
            "field_count": 0,
            "usable_field_count": 0,
            "degradation_level": EXTENDED_BLOCKED,
        }

    usable = coverage[coverage["usable_for_train"] == True]  # noqa: E712
    blocked = coverage[coverage["degradation_level"] == EXTENDED_BLOCKED]
    warn = coverage[coverage["degradation_level"] == EXTENDED_WARN]
    degradation_level = CORE_OK
    if not blocked.empty:
        degradation_level = EXTENDED_BLOCKED
    elif not warn.empty:
        degradation_level = EXTENDED_WARN

    return {
        "mainline_object_name": coverage.iloc[0]["mainline_object_name"],
        "bundle_id": coverage.iloc[0]["bundle_id"],
        "legacy_feature_set_alias": coverage.iloc[0]["legacy_feature_set_alias"],
        "field_count": int(len(coverage)),
        "usable_field_count": int(len(usable)),
        "usable_ratio": round(float(len(usable) / len(coverage)), 8),
        "high_missing_field_count": int((coverage["missing_ratio"] >= 0.3).sum()),
        "dead_or_constant_field_count": int((coverage["constant_ratio"] >= 0.95).sum()),
        "degradation_level": degradation_level,
        "coverage_source": str(coverage.iloc[0].get("coverage_source") or "raw_feature_frame"),
    }


def build_readiness_summary(
    *,
    spec: MainlineObjectSpec,
    coverage: pd.DataFrame,
    comparison_base: pd.DataFrame | None = None,
) -> dict[str, Any]:
    missingness = build_missingness_summary(coverage)
    dead_fields = coverage[coverage["constant_ratio"] >= 0.95]["field_name"].tolist()
    low_coverage_fields = coverage[coverage["coverage_ratio"] < 0.7]["field_name"].tolist()
    summary = {
        **missingness,
        "dead_or_constant_fields": dead_fields[:20],
        "low_coverage_fields": low_coverage_fields[:20],
        "notes": _summary_notes(coverage, spec.mainline_object_name),
    }
    if comparison_base is not None and not comparison_base.empty:
        summary["baseline_comparison"] = compare_readiness(
            base_coverage=comparison_base,
            target_coverage=coverage,
        )
    return summary


def compare_readiness(*, base_coverage: pd.DataFrame, target_coverage: pd.DataFrame) -> dict[str, Any]:
    base = base_coverage[["field_name", "coverage_ratio", "usable_for_train"]].rename(
        columns={
            "coverage_ratio": "base_coverage_ratio",
            "usable_for_train": "base_usable_for_train",
        }
    )
    target = target_coverage[["field_name", "coverage_ratio", "usable_for_train"]].rename(
        columns={
            "coverage_ratio": "target_coverage_ratio",
            "usable_for_train": "target_usable_for_train",
        }
    )
    merged = target.merge(base, on="field_name", how="outer")
    improved = merged[
        (merged["target_coverage_ratio"].fillna(0.0) > merged["base_coverage_ratio"].fillna(0.0))
        | ((merged["target_usable_for_train"] == True) & (merged["base_usable_for_train"] != True))
    ]
    worsened = merged[
        (merged["target_coverage_ratio"].fillna(0.0) < merged["base_coverage_ratio"].fillna(0.0))
        | ((merged["target_usable_for_train"] != True) & (merged["base_usable_for_train"] == True))
    ]
    return {
        "improved_field_count": int(len(improved)),
        "worsened_field_count": int(len(worsened)),
        "improved_fields": improved["field_name"].dropna().astype(str).tolist()[:20],
        "worsened_fields": worsened["field_name"].dropna().astype(str).tolist()[:20],
    }


def field_dependency_summary(field_name: str) -> dict[str, Any]:
    raw_dependencies = sorted(set(RAW_FIELD_PATTERN.findall(field_name or "")))
    is_raw_field = bool(field_name and field_name.startswith("$") and field_name in raw_dependencies)
    is_expression = bool(raw_dependencies and not is_raw_field)
    if is_raw_field:
        feature_kind = "raw_field"
        feature_group = "fundamental_raw" if field_name not in {"$open", "$high", "$low", "$close", "$volume", "$amount", "$vwap"} else "qlib_raw_field"
        expected_source = "raw_qlib"
    elif is_expression:
        feature_kind = "expression"
        feature_group = "qlib_expression"
        expected_source = "qlib_expression"
    else:
        feature_kind = "other"
        feature_group = "other"
        expected_source = "other"
    return {
        "feature_name": field_name,
        "feature_group": feature_group,
        "feature_kind": feature_kind,
        "expected_source": expected_source,
        "is_raw_field": is_raw_field,
        "is_expression": is_expression,
        "raw_dependencies": raw_dependencies,
    }


def write_json(path: str | Path, payload: dict[str, Any]) -> str:
    path_obj = Path(path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    path_obj.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path_obj)


def _missing_field_row(spec: MainlineObjectSpec, field: str, total_rows: int, notes: str) -> dict[str, Any]:
    return {
        "mainline_object_name": spec.mainline_object_name,
        "bundle_id": spec.bundle_id,
        "legacy_feature_set_alias": spec.legacy_feature_set_alias,
        "field_name": field,
        "coverage_ratio": 0.0 if total_rows else None,
        "missing_ratio": 1.0 if total_rows else None,
        "constant_ratio": 1.0 if total_rows else None,
        "zero_ratio": None,
        "usable_for_train": False,
        "degradation_level": EXTENDED_BLOCKED if spec.mainline_object_name == "feature_173" else EXTENDED_WARN,
        "notes": notes,
        "coverage_source": "raw_feature_frame",
    }


def _constant_ratio(frame: pd.DataFrame, field: str) -> float:
    if not isinstance(frame.index, pd.MultiIndex) or "datetime" not in frame.index.names:
        values = pd.to_numeric(frame[field], errors="coerce")
        return 1.0 if values.nunique(dropna=True) <= 1 else 0.0
    values = pd.to_numeric(frame[field], errors="coerce")
    by_date = values.groupby(level="datetime")
    constant_flags = by_date.apply(lambda s: 1.0 if s.dropna().nunique() <= 1 else 0.0)
    if constant_flags.empty:
        return 1.0
    return float(constant_flags.mean())


def _field_degradation_level(*, coverage_ratio: float, constant_ratio: float, thresholds: ReadinessThresholds, is_core: bool, uses_model_input: bool = False) -> str:
    if coverage_ratio >= thresholds.usable_coverage_ratio and (uses_model_input or constant_ratio < thresholds.constant_ratio_threshold):
        return CORE_OK
    if is_core:
        return EXTENDED_BLOCKED
    return EXTENDED_WARN


def _field_notes(*, coverage_ratio: float, constant_ratio: float, zero_ratio: float | None, degradation_level: str, thresholds: ReadinessThresholds) -> str:
    notes: list[str] = []
    if coverage_ratio < thresholds.usable_coverage_ratio:
        notes.append("low_coverage")
    if coverage_ratio < thresholds.warning_coverage_ratio:
        notes.append("high_missingness")
    if constant_ratio >= thresholds.constant_ratio_threshold:
        notes.append("dead_or_constant")
    if zero_ratio is not None and zero_ratio >= thresholds.zero_ratio_warn_threshold:
        notes.append("mostly_zero")
    if degradation_level != CORE_OK:
        notes.append(degradation_level)
    return ",".join(notes) if notes else "ok"


def _summary_notes(coverage: pd.DataFrame, mainline_object_name: str) -> list[str]:
    notes: list[str] = []
    if not coverage.empty and (coverage["degradation_level"] == EXTENDED_BLOCKED).any():
        if mainline_object_name == "feature_173":
            notes.append("core_chain_has_blocking_fields")
        else:
            notes.append("extended_layer_has_blocking_fields_but_should_degrade")
    if not coverage.empty and (coverage["degradation_level"] == EXTENDED_WARN).any():
        notes.append("extended_fields_need_warning_only_degradation")
    return notes or ["ok"]
