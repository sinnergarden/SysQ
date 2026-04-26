#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qsys.data.adapter import QlibAdapter
from qsys.ops import read_latest_shadow_model, resolve_daily_trade_date
from qsys.research.mainline import MAINLINE_OBJECTS, resolve_mainline_feature_config
from qsys.research.readiness import (
    EXTENDED_BLOCKED,
    EXTENDED_WARN,
    build_feature_coverage,
    build_model_input_frame,
    build_readiness_summary,
    field_dependency_summary,
)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _resolve_model_payload(base_dir: Path, mainline_object_name: str) -> dict[str, Any]:
    latest = read_latest_shadow_model(base_dir)
    if latest and latest.get("mainline_object_name") == mainline_object_name:
        return latest
    spec = MAINLINE_OBJECTS[mainline_object_name]
    model_path = base_dir / "data" / "models" / spec.model_name
    return {
        "model_name": spec.model_name,
        "model_path": str(model_path),
        "mainline_object_name": mainline_object_name,
        "bundle_id": spec.bundle_id,
        "train_run_id": latest.get("train_run_id") if latest else None,
        "trained_at": latest.get("trained_at") if latest else None,
        "status": "success" if model_path.exists() else "missing",
    }


def _raw_probe_rows(frame: pd.DataFrame, raw_fields: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    total_rows = len(frame.index) if frame is not None else 0
    for field in raw_fields:
        if frame is None or frame.empty or field not in frame.columns:
            non_null_count = 0
            non_null_ratio = 0.0 if total_rows else None
            available = False
        else:
            series = pd.to_numeric(frame[field], errors="coerce")
            non_null_count = int(series.notna().sum())
            non_null_ratio = float(non_null_count / total_rows) if total_rows else None
            available = bool(non_null_count > 0)
        rows.append(
            {
                "field_name": field,
                "raw_available": available,
                "non_null_count": non_null_count,
                "non_null_ratio": round(non_null_ratio, 8) if non_null_ratio is not None else None,
            }
        )
    return rows


def _model_probe_rows(frame: pd.DataFrame, expected_features: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    total_rows = len(frame.index) if frame is not None else 0
    for field in expected_features:
        if frame is None or frame.empty or field not in frame.columns:
            non_null_count = 0
            non_null_ratio = 0.0 if total_rows else None
            available = False
        else:
            series = pd.to_numeric(frame[field], errors="coerce")
            non_null_count = int(series.notna().sum())
            non_null_ratio = float(non_null_count / total_rows) if total_rows else None
            available = bool(non_null_count > 0)
        rows.append(
            {
                "feature_name": field,
                "model_input_available": available,
                "non_null_count": non_null_count,
                "non_null_ratio": round(non_null_ratio, 8) if non_null_ratio is not None else None,
            }
        )
    return rows


def _missing_reason(*, meta: dict[str, Any], raw_available: bool, model_input_available: bool, non_null_ratio: float | None) -> str:
    if model_input_available and meta["is_expression"]:
        return "not_required_raw_field"
    if model_input_available:
        return "ok"
    if meta["is_expression"] and not model_input_available:
        return "expression_not_materialized"
    if meta["is_raw_field"] and not raw_available:
        return "raw_field_missing"
    if non_null_ratio == 0:
        return "all_nan"
    if non_null_ratio is not None and non_null_ratio < 0.7:
        return "low_coverage"
    return "unsupported_feature_name"


def run_audit(*, base_dir: Path, mainline_object_name: str, universe: str = "csi300") -> dict[str, Any]:
    spec = MAINLINE_OBJECTS[mainline_object_name]
    date_resolution = resolve_daily_trade_date(None, universe=universe)
    trade_date = str(date_resolution.get("resolved_trade_date") or date_resolution["requested_date"])
    feature_config = list(resolve_mainline_feature_config(mainline_object_name) or [])
    model_payload = _resolve_model_payload(base_dir, mainline_object_name)

    adapter = QlibAdapter()
    adapter.init_qlib()
    feature_frame = adapter.get_features(universe, feature_config, start_time=trade_date, end_time=trade_date)
    model_input_frame = build_model_input_frame(feature_frame=feature_frame, model_path=model_payload.get("model_path"))
    reported_coverage = build_feature_coverage(spec=spec, frame=feature_frame)
    reported_summary = build_readiness_summary(spec=spec, coverage=reported_coverage)
    readiness_coverage = build_feature_coverage(spec=spec, frame=feature_frame, model_input_frame=model_input_frame)
    readiness_summary = build_readiness_summary(spec=spec, coverage=readiness_coverage)

    expected_rows: list[dict[str, Any]] = []
    raw_dependencies: list[str] = []
    for field in feature_config:
        meta = field_dependency_summary(field)
        expected_rows.append(meta)
        raw_dependencies.extend(meta["raw_dependencies"])
    raw_fields = sorted(set(raw_dependencies))

    raw_frame = adapter.get_features(universe, raw_fields, start_time=trade_date, end_time=trade_date) if raw_fields else pd.DataFrame()
    raw_probe_rows = _raw_probe_rows(raw_frame, raw_fields)
    raw_probe_map = {row["field_name"]: row for row in raw_probe_rows}
    model_probe_rows = _model_probe_rows(model_input_frame, feature_config)
    model_probe_map = {row["feature_name"]: row for row in model_probe_rows}

    missing_rows: list[dict[str, Any]] = []
    blocked_feature_count = 0
    warn_feature_count = 0
    for meta in expected_rows:
        raw_available = all(raw_probe_map.get(dep, {}).get("raw_available", False) for dep in meta["raw_dependencies"]) if meta["raw_dependencies"] else False
        model_meta = model_probe_map.get(meta["feature_name"], {})
        model_input_available = bool(model_meta.get("model_input_available", False))
        non_null_count = int(model_meta.get("non_null_count", 0) or 0)
        non_null_ratio = model_meta.get("non_null_ratio")
        reason = _missing_reason(
            meta=meta,
            raw_available=raw_available,
            model_input_available=model_input_available,
            non_null_ratio=non_null_ratio,
        )
        degradation = readiness_coverage.loc[readiness_coverage["field_name"] == meta["feature_name"], "degradation_level"]
        if not degradation.empty and degradation.iloc[0] == EXTENDED_BLOCKED:
            blocked_feature_count += 1
        elif not degradation.empty and degradation.iloc[0] == EXTENDED_WARN:
            warn_feature_count += 1
        missing_rows.append(
            {
                "feature_name": meta["feature_name"],
                "feature_group": meta["feature_group"],
                "feature_kind": meta["feature_kind"],
                "expected_source": meta["expected_source"],
                "is_raw_field": meta["is_raw_field"],
                "is_expression": meta["is_expression"],
                "raw_available": raw_available,
                "model_input_available": model_input_available,
                "non_null_count": non_null_count,
                "non_null_ratio": non_null_ratio,
                "reason": reason,
            }
        )

    reported_usable_count = int(reported_summary["usable_field_count"])
    model_input_non_null_ratio_mean = 0.0
    if model_input_frame is not None and not model_input_frame.empty and len(model_input_frame.columns) > 0:
        model_input_non_null_ratio_mean = float((model_input_frame.notna().sum() / len(model_input_frame.index)).mean())

    root_cause = "unknown"
    recommendation = "Inspect audit artifacts and missing feature reasons."
    if readiness_summary["usable_field_count"] > reported_usable_count:
        root_cause = "readiness_metric_bug"
        recommendation = "Use model-input coverage for readiness, keep raw probe only as diagnostics."
    elif readiness_summary["usable_field_count"] == 0 and model_input_frame.empty:
        root_cause = "model_input_generation_failed"
        recommendation = "Fix feature loader or model preprocessing contract before rerunning daily shadow."
    elif any(not row["raw_available"] for row in raw_probe_rows):
        root_cause = "qlib_data_missing"
        recommendation = "Review qlib dump / raw update pipeline for the missing raw fields before rerunning readiness."
    elif len(model_input_frame.columns) != len(feature_config):
        root_cause = "feature_config_mismatch"
        recommendation = "Align model feature_config, mainline feature config, and daily loader."

    summary = {
        "mainline_object_name": mainline_object_name,
        "bundle_id": spec.bundle_id,
        "legacy_feature_set_alias": spec.legacy_feature_set_alias,
        "trade_date": trade_date,
        "universe": universe,
        "feature_set": spec.legacy_feature_set_alias,
        "expected_feature_count": len(feature_config),
        "reported_usable_count": reported_usable_count,
        "raw_required_count": len(raw_fields),
        "raw_available_count": int(sum(1 for row in raw_probe_rows if row["raw_available"])),
        "model_input_column_count": int(len(model_input_frame.columns)) if model_input_frame is not None else 0,
        "model_input_usable_count": int(readiness_summary["usable_field_count"]),
        "model_input_non_null_ratio_mean": round(model_input_non_null_ratio_mean, 8),
        "blocked_feature_count": blocked_feature_count,
        "warn_feature_count": warn_feature_count,
        "reported_degradation_level": reported_summary["degradation_level"],
        "degradation_level": readiness_summary["degradation_level"],
        "root_cause": root_cause,
        "recommendation": recommendation,
        "go_no_go": "Go" if readiness_summary["degradation_level"] != EXTENDED_BLOCKED else "No-Go",
        "model_payload": model_payload,
        "date_resolution": date_resolution,
    }

    return {
        "feature_config": feature_config,
        "expected_rows": expected_rows,
        "raw_probe_rows": raw_probe_rows,
        "model_probe_rows": model_probe_rows,
        "missing_rows": missing_rows,
        "summary": summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit feature readiness for a mainline object.")
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--mainline", default="feature_173")
    parser.add_argument("--universe", default="csi300")
    parser.add_argument("--output-dir", default="experiments/ops_diagnostics/feature_173_readiness_audit")
    args = parser.parse_args()

    base_dir = Path(args.base_dir).resolve()
    output_dir = (base_dir / args.output_dir).resolve()
    result = run_audit(base_dir=base_dir, mainline_object_name=args.mainline, universe=args.universe)

    _write_json(
        output_dir / "feature_config_snapshot.json",
        {
            "mainline_object_name": args.mainline,
            "feature_count": len(result["feature_config"]),
            "feature_config": result["feature_config"],
            "model_payload": result["summary"]["model_payload"],
        },
    )
    _write_csv(
        output_dir / "expected_features.csv",
        result["expected_rows"],
        ["feature_name", "feature_group", "feature_kind", "expected_source", "is_raw_field", "is_expression", "raw_dependencies"],
    )
    _write_csv(
        output_dir / "qlib_raw_probe.csv",
        result["raw_probe_rows"],
        ["field_name", "raw_available", "non_null_count", "non_null_ratio"],
    )
    _write_csv(
        output_dir / "model_input_probe.csv",
        result["model_probe_rows"],
        ["feature_name", "model_input_available", "non_null_count", "non_null_ratio"],
    )
    _write_csv(
        output_dir / "missing_features.csv",
        result["missing_rows"],
        [
            "feature_name",
            "feature_group",
            "feature_kind",
            "expected_source",
            "is_raw_field",
            "is_expression",
            "raw_available",
            "model_input_available",
            "non_null_count",
            "non_null_ratio",
            "reason",
        ],
    )
    _write_json(output_dir / "readiness_audit_summary.json", result["summary"])
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
