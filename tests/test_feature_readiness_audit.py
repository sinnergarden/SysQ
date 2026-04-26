from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from qsys.research.mainline import MAINLINE_OBJECTS
from qsys.research.readiness import build_feature_coverage, build_readiness_summary, field_dependency_summary
from scripts.ops import audit_feature_readiness


def _frame(columns: list[str], values: list[list[float | None]]) -> pd.DataFrame:
    index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2026-04-17"), "AAA"),
            (pd.Timestamp("2026-04-17"), "BBB"),
        ],
        names=["datetime", "instrument"],
    )
    return pd.DataFrame(values, index=index, columns=columns)


def test_expression_feature_is_not_marked_as_raw_missing() -> None:
    meta = field_dependency_summary("Ref($close, 5)/$close")
    assert meta["is_raw_field"] is False
    assert meta["is_expression"] is True
    assert meta["expected_source"] == "qlib_expression"
    assert "$close" in meta["raw_dependencies"]


def test_model_input_coverage_takes_priority_when_available() -> None:
    spec = MAINLINE_OBJECTS["feature_173"]
    raw_frame = _frame(["Ref($close, 5)/$close"], [[None], [None]])
    model_input_frame = _frame(["Ref($close, 5)/$close"], [[0.0], [1.0]])
    with patch("qsys.research.readiness.resolve_mainline_feature_config", return_value=["Ref($close, 5)/$close"]):
        coverage = build_feature_coverage(spec=spec, frame=raw_frame, model_input_frame=model_input_frame)
    summary = build_readiness_summary(spec=spec, coverage=coverage)
    assert summary["usable_field_count"] == 1
    assert summary["degradation_level"] == "core_ok"
    assert coverage.iloc[0]["coverage_source"] == "model_input"


def test_true_missing_feature_remains_blocked() -> None:
    spec = MAINLINE_OBJECTS["feature_173"]
    raw_frame = _frame(["$close"], [[None], [None]])
    model_input_frame = _frame(["$close"], [[None], [None]])
    with patch("qsys.research.readiness.resolve_mainline_feature_config", return_value=["$close"]):
        coverage = build_feature_coverage(spec=spec, frame=raw_frame, model_input_frame=model_input_frame)
    summary = build_readiness_summary(spec=spec, coverage=coverage)
    assert summary["usable_field_count"] == 0
    assert summary["degradation_level"] == "extended_blocked"


def test_audit_artifact_contract(tmp_path: Path) -> None:
    feature_frame = _frame(["Ref($close, 5)/$close", "$pe"], [[None, 1.0], [None, 2.0]])
    model_frame = _frame(["Ref($close, 5)/$close", "$pe"], [[0.0, 1.0], [1.0, 2.0]])

    with patch("scripts.ops.audit_feature_readiness.resolve_daily_trade_date", return_value={"requested_date": "2026-04-17", "resolved_trade_date": "2026-04-17", "last_qlib_date": "2026-04-17", "status": "success", "reason": "ok", "is_exact_match": True}), \
         patch("scripts.ops.audit_feature_readiness.resolve_mainline_feature_config", return_value=["Ref($close, 5)/$close", "$pe"]), \
         patch("scripts.ops.audit_feature_readiness.read_latest_shadow_model", return_value={"mainline_object_name": "feature_173", "bundle_id": "bundle_feature_173", "model_name": "qlib_lgbm_extended", "model_path": str(tmp_path / "data" / "models" / "qlib_lgbm_extended"), "status": "success"}), \
         patch("scripts.ops.audit_feature_readiness.QlibAdapter") as adapter_cls, \
         patch("scripts.ops.audit_feature_readiness.build_model_input_frame", return_value=model_frame), \
         patch("sys.argv", ["audit_feature_readiness.py", "--base-dir", str(tmp_path), "--mainline", "feature_173"]):
        adapter = adapter_cls.return_value
        adapter.get_features.side_effect = [feature_frame, _frame(["$close", "$pe"], [[10.0, 1.0], [11.0, 2.0]])]
        adapter.init_qlib.return_value = None
        audit_feature_readiness.main()
    output_dir = tmp_path / "experiments" / "ops_diagnostics" / "feature_173_readiness_audit"
    assert (output_dir / "expected_features.csv").exists()
    assert (output_dir / "missing_features.csv").exists()
    assert (output_dir / "readiness_audit_summary.json").exists()
    payload = json.loads((output_dir / "readiness_audit_summary.json").read_text(encoding="utf-8"))
    assert payload["mainline_object_name"] == "feature_173"
    assert payload["expected_feature_count"] == 2
    assert payload["model_input_usable_count"] == 2
    assert payload["root_cause"] == "readiness_metric_bug"
