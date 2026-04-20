from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from click.testing import CliRunner

from qsys.research.diagnostics import build_bad_field_diagnostics
from scripts.run_mainline_bad_field_diagnostics import main as bad_field_main


def _coverage_rows(object_name: str, alias: str):
    return pd.DataFrame(
        [
            {
                "mainline_object_name": object_name,
                "bundle_id": f"bundle_{object_name}",
                "legacy_feature_set_alias": alias,
                "field_name": "market_breadth",
                "coverage_ratio": 1.0,
                "missing_ratio": 0.0,
                "constant_ratio": 1.0,
                "zero_ratio": 0.0,
                "usable_for_train": False,
                "degradation_level": "extended_warn",
                "notes": "dead_or_constant,extended_warn",
            },
            {
                "mainline_object_name": object_name,
                "bundle_id": f"bundle_{object_name}",
                "legacy_feature_set_alias": alias,
                "field_name": "ps_ttm",
                "coverage_ratio": 0.0,
                "missing_ratio": 1.0,
                "constant_ratio": 1.0,
                "zero_ratio": 1.0,
                "usable_for_train": False,
                "degradation_level": "extended_warn",
                "notes": "high_missingness",
            },
            {
                "mainline_object_name": object_name,
                "bundle_id": f"bundle_{object_name}",
                "legacy_feature_set_alias": alias,
                "field_name": "$pe",
                "coverage_ratio": 0.95,
                "missing_ratio": 0.05,
                "constant_ratio": 0.0,
                "zero_ratio": 0.05,
                "usable_for_train": True,
                "degradation_level": "core_ok",
                "notes": "ok",
            },
        ]
    )


def test_bad_field_diagnostics_artifacts_have_stable_fields() -> None:
    bad_254, bad_absnorm, proposal = build_bad_field_diagnostics(
        coverage_254=_coverage_rows("feature_254", "semantic_all_features"),
        coverage_254_absnorm=_coverage_rows("feature_254_absnorm", "semantic_all_features_absnorm"),
    )
    required = {
        "field_name",
        "in_feature_254",
        "in_feature_254_absnorm",
        "coverage_ratio",
        "constant_ratio",
        "zero_ratio",
        "degradation_level",
        "absnorm_improved",
        "trim_recommendation",
        "reason",
    }
    assert required.issubset(bad_254.columns)
    assert required.issubset(bad_absnorm.columns)
    assert "feature_254" in proposal
    assert "feature_254_absnorm" in proposal


def test_trim_recommendation_and_absnorm_improved_logic_stay_stable() -> None:
    base = _coverage_rows("feature_254", "semantic_all_features")
    absnorm = _coverage_rows("feature_254_absnorm", "semantic_all_features_absnorm")
    absnorm.loc[absnorm["field_name"] == "ps_ttm", "coverage_ratio"] = 0.2
    absnorm.loc[absnorm["field_name"] == "ps_ttm", "missing_ratio"] = 0.8
    bad_254, _, _ = build_bad_field_diagnostics(coverage_254=base, coverage_254_absnorm=absnorm)
    by_field = {row["field_name"]: row for row in bad_254.to_dict(orient="records")}
    assert by_field["market_breadth"]["trim_recommendation"] == "remove_from_trimmed"
    assert by_field["ps_ttm"]["absnorm_improved"] is True


def test_bad_field_script_writes_expected_artifacts(tmp_path: Path) -> None:
    readiness_root = tmp_path / "experiments" / "mainline_readiness"
    for name, alias in [
        ("feature_254", "semantic_all_features"),
        ("feature_254_absnorm", "semantic_all_features_absnorm"),
    ]:
        object_dir = readiness_root / name
        object_dir.mkdir(parents=True, exist_ok=True)
        _coverage_rows(name, alias).to_csv(object_dir / "feature_coverage_by_field.csv", index=False)

    runner = CliRunner()
    from unittest.mock import patch

    with patch("scripts.run_mainline_bad_field_diagnostics.project_root", tmp_path):
        result = runner.invoke(bad_field_main, ["--readiness_dir", "experiments/mainline_readiness", "--output_dir", "experiments/mainline_diagnostics"])
    assert result.exit_code == 0, result.output
    out_root = tmp_path / "experiments" / "mainline_diagnostics"
    assert (out_root / "feature_254_bad_fields.csv").exists()
    assert (out_root / "feature_254_absnorm_bad_fields.csv").exists()
    proposal = json.loads((out_root / "trimmed_proposal.json").read_text(encoding="utf-8"))
    assert "feature_254_trimmed" in proposal["trimmed_objects"]
