from __future__ import annotations

from pathlib import Path
import textwrap

import pandas as pd

from qsys.research.decision import (
    DecisionRecord,
    decision_payload,
    find_latest_decision,
    load_decision_records,
    parse_decision_record,
    resolve_subject_decision,
)
from scripts.run_absnorm_comparison import _ordered_summary


def _write_yaml(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")
    return path


def test_decision_record_round_trip_and_payload(tmp_path: Path) -> None:
    decision_path = _write_yaml(
        tmp_path / "feature_173_candidate.yaml",
        """
        decision_id: mainline.feature_173.candidate.v1
        subject_type: mainline_object
        subject_id: feature_173
        status: candidate
        reason: baseline candidate
        evidence:
          total_return: 0.12
          RankIC: 0.08
        created_at: "2026-04-18T17:30:00+08:00"
        updated_at: "2026-04-18T17:30:00+08:00"
        author: qsys_phase_f
        notes:
          - tracked
        """,
    )

    record = parse_decision_record(decision_path)
    assert isinstance(record, DecisionRecord)
    assert record.subject_id == "feature_173"
    assert record.status == "candidate"
    assert record.to_dict()["reason"] == "baseline candidate"
    assert decision_payload(record)["status"] == "candidate"
    assert decision_payload(None)["status"] == "not_decided"


def test_decision_registry_resolves_latest_matching_record(tmp_path: Path) -> None:
    _write_yaml(
        tmp_path / "a.yaml",
        """
        decision_id: mainline.feature_254.research_only.v1
        subject_type: mainline_object
        subject_id: feature_254
        status: research_only
        reason: first pass
        evidence:
          total_return: 0.1
        created_at: "2026-04-18T17:00:00+08:00"
        updated_at: "2026-04-18T17:00:00+08:00"
        author: qsys_phase_f
        notes: [first]
        """,
    )
    _write_yaml(
        tmp_path / "b.yaml",
        """
        decision_id: mainline.feature_254.candidate.v2
        subject_type: mainline_object
        subject_id: feature_254
        status: candidate
        reason: later pass
        evidence:
          total_return: 0.2
        created_at: "2026-04-18T18:00:00+08:00"
        updated_at: "2026-04-18T18:00:00+08:00"
        author: qsys_phase_f
        notes: [second]
        """,
    )

    records = load_decision_records(tmp_path)
    assert len(records) == 2
    assert find_latest_decision("mainline_object", "feature_254", tmp_path).status == "candidate"
    assert resolve_subject_decision(subject_type="mainline_object", subject_ids=["missing", "feature_254"], decisions_dir=tmp_path).status == "candidate"


def test_absnorm_comparison_summary_includes_decision_columns() -> None:
    frame = pd.DataFrame([
        {
            "variant": "feature_254_absnorm",
            "mainline_object_name": "feature_254_absnorm",
            "bundle_id": "bundle_feature_254_absnorm",
            "legacy_feature_set_alias": "semantic_all_features_absnorm",
            "feature_set": "semantic_all_features_absnorm",
            "decision_status": "candidate",
            "decision_reason": "tracked",
            "run_decision_status": "shadow_ready",
            "run_decision_reason": "baseline approved for shadow",
            "status": "ok",
            "returncode": 0,
            "total_return": 0.12,
            "sharpe": 1.5,
            "max_drawdown": -0.08,
            "turnover": 0.3,
            "IC": 0.09,
            "RankIC": 0.08,
            "long_short_spread": 0.01,
            "group_monotonicity_proxy": 1.0,
            "empty_portfolio_ratio": 0.0,
            "avg_holding_count": 5.0,
            "size_tilt_vs_universe_mean": 0.1,
            "industry_drift_l1_mean": 0.2,
            "top1_weight_mean": 0.3,
            "topk_weight_hhi_mean": 0.4,
            "model_path": "/tmp/model",
            "stdout_tail": "",
            "stderr_tail": "",
        }
    ])

    ordered = _ordered_summary(frame)
    assert "decision_status" in ordered.columns
    assert "decision_reason" in ordered.columns
    assert "run_decision_status" in ordered.columns
    assert "run_decision_reason" in ordered.columns
