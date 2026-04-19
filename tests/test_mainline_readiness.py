from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from click.testing import CliRunner

from qsys.research.mainline import MAINLINE_OBJECTS
from qsys.research.readiness import (
    CORE_OK,
    EXTENDED_BLOCKED,
    EXTENDED_WARN,
    build_feature_coverage,
    build_missingness_summary,
    build_readiness_summary,
)
from scripts.run_mainline_readiness_audit import main as readiness_main


def _sample_frame() -> pd.DataFrame:
    index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2025-01-02"), "AAA"),
            (pd.Timestamp("2025-01-02"), "BBB"),
            (pd.Timestamp("2025-01-03"), "AAA"),
            (pd.Timestamp("2025-01-03"), "BBB"),
        ],
        names=["datetime", "instrument"],
    )
    return pd.DataFrame(
        {
            "$close": [10.0, 11.0, 10.5, 11.5],
            "$pe": [1.0, None, 1.2, None],
            "$pb": [0.0, 0.0, 0.0, 0.0],
            "Log($total_mv+1)": [5.0, 5.1, 5.0, 5.1],
        },
        index=index,
    )


def test_readiness_summary_classifies_extended_fields_without_blocking_core() -> None:
    spec = MAINLINE_OBJECTS["feature_254"]
    with patch("qsys.research.readiness.resolve_mainline_feature_config", return_value=["$close", "$pe", "$pb"]):
        coverage = build_feature_coverage(spec=spec, frame=_sample_frame())
    by_name = {row["field_name"]: row for row in coverage.to_dict(orient="records")}
    assert by_name["$close"]["degradation_level"] == CORE_OK
    assert by_name["$pe"]["degradation_level"] == EXTENDED_WARN
    assert by_name["$pb"]["degradation_level"] == EXTENDED_WARN
    summary = build_missingness_summary(coverage)
    assert summary["degradation_level"] == EXTENDED_WARN


def test_core_fields_can_block_when_feature_173_is_broken() -> None:
    spec = MAINLINE_OBJECTS["feature_173"]
    frame = _sample_frame()[["$close"]].copy()
    frame["$close"] = None
    with patch("qsys.research.readiness.resolve_mainline_feature_config", return_value=["$close"]):
        coverage = build_feature_coverage(spec=spec, frame=frame)
    assert coverage.iloc[0]["degradation_level"] == EXTENDED_BLOCKED


def test_absnorm_readiness_summary_compares_against_feature_254() -> None:
    base = pd.DataFrame([
        {"field_name": "$pe", "coverage_ratio": 0.5, "usable_for_train": False},
        {"field_name": "$pb", "coverage_ratio": 1.0, "usable_for_train": False},
    ])
    target = pd.DataFrame([
        {
            "mainline_object_name": "feature_254_absnorm",
            "bundle_id": "bundle_feature_254_absnorm",
            "legacy_feature_set_alias": "semantic_all_features_absnorm",
            "field_name": "$pe",
            "coverage_ratio": 0.8,
            "missing_ratio": 0.2,
            "constant_ratio": 0.1,
            "zero_ratio": 0.0,
            "usable_for_train": True,
            "degradation_level": CORE_OK,
            "notes": "ok",
        },
        {
            "mainline_object_name": "feature_254_absnorm",
            "bundle_id": "bundle_feature_254_absnorm",
            "legacy_feature_set_alias": "semantic_all_features_absnorm",
            "field_name": "$pb",
            "coverage_ratio": 1.0,
            "missing_ratio": 0.0,
            "constant_ratio": 1.0,
            "zero_ratio": 1.0,
            "usable_for_train": False,
            "degradation_level": EXTENDED_WARN,
            "notes": "dead_or_constant",
        },
    ])
    summary = build_readiness_summary(spec=MAINLINE_OBJECTS["feature_254_absnorm"], coverage=target, comparison_base=base)
    assert summary["baseline_comparison"]["improved_field_count"] >= 1
    assert "$pe" in summary["baseline_comparison"]["improved_fields"]


def test_mainline_readiness_audit_writes_contract_files(tmp_path: Path) -> None:
    runner = CliRunner()
    sample = _sample_frame()
    with patch("scripts.run_mainline_readiness_audit.project_root", tmp_path), \
         patch("scripts.run_mainline_readiness_audit.fetch_mainline_feature_frame", return_value=sample), \
         patch(
             "scripts.run_mainline_readiness_audit.resolve_mainline_specs",
             return_value=[MAINLINE_OBJECTS["feature_173"], MAINLINE_OBJECTS["feature_254"], MAINLINE_OBJECTS["feature_254_absnorm"]],
         ), \
         patch(
             "qsys.research.readiness.resolve_mainline_feature_config",
             side_effect=lambda name: {
                 "feature_173": ["$close"],
                 "feature_254": ["$close", "$pe", "$pb"],
                 "feature_254_absnorm": ["$close", "$pe", "$pb", "Log($total_mv+1)"],
             }[name],
         ):
        result = runner.invoke(readiness_main, ["--output_dir", "tmp/readiness"])
    assert result.exit_code == 0, result.output
    object_dir = tmp_path / "tmp" / "readiness" / "feature_254_absnorm"
    assert (object_dir / "feature_readiness_summary.json").exists()
    assert (object_dir / "feature_coverage_by_field.csv").exists()
    assert (object_dir / "feature_dead_or_constant.csv").exists()
    assert (object_dir / "feature_missingness_summary.json").exists()
    payload = json.loads((object_dir / "feature_readiness_summary.json").read_text(encoding="utf-8"))
    assert payload["mainline_object_name"] == "feature_254_absnorm"
