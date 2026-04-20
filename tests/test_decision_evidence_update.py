from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from click.testing import CliRunner

from scripts.update_mainline_decision_evidence import main as update_decision_main


DECISION_TEMPLATE = """
decision_id: mainline.feature_173.candidate.v1
subject_type: mainline_object
subject_id: feature_173
status: candidate
reason: baseline candidate
evidence:
  total_return: null
  RankIC: null
  max_drawdown: null
  turnover: null
  empty_portfolio_ratio: null
  comparison_source: historical_mainline_baseline
  lineage:
    mainline_object_name: feature_173
    bundle_id: bundle_feature_173
    legacy_feature_set_alias: extended
created_at: "2026-04-18T17:30:00+08:00"
updated_at: "2026-04-18T17:30:00+08:00"
author: qsys_phase_f
notes:
  - Baseline candidate state is explicit now rather than implied by naming and habit.
"""


def test_decision_evidence_updates_from_real_comparison(tmp_path: Path) -> None:
    compare_dir = tmp_path / "experiments" / "mainline_rolling"
    compare_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([
        {
            "mainline_object_name": "feature_173",
            "bundle_id": "bundle_feature_173",
            "legacy_feature_set_alias": "extended",
            "rolling_window_count": 4,
            "rolling_rankic_mean": 0.06,
            "rolling_rankic_std": 0.01,
            "rolling_total_return_mean": 0.13,
            "rolling_max_drawdown_worst": -0.09,
            "rolling_turnover_mean": 0.22,
        }
    ]).to_csv(compare_dir / "comparison_summary.csv", index=False)

    decision_path = tmp_path / "research" / "decisions" / "feature_173_candidate.yaml"
    decision_path.parent.mkdir(parents=True, exist_ok=True)
    decision_path.write_text(DECISION_TEMPLATE, encoding="utf-8")

    runner = CliRunner()
    with patch("scripts.update_mainline_decision_evidence.project_root", tmp_path), \
         patch("scripts.update_mainline_decision_evidence.DEFAULT_DECISION_FILES", {"feature_173": "research/decisions/feature_173_candidate.yaml", "feature_254": "research/decisions/feature_173_candidate.yaml", "feature_254_absnorm": "research/decisions/feature_173_candidate.yaml"}, create=True), \
         patch("scripts.update_mainline_decision_evidence.MAINLINE_OBJECTS", {"feature_173": type("Spec", (), {"bundle_id": "bundle_feature_173", "legacy_feature_set_alias": "extended"})()}, create=True):
        result = runner.invoke(
            update_decision_main,
            ["--comparison_csv", "experiments/mainline_rolling/comparison_summary.csv", "--comparison_source", "experiments/mainline_rolling/comparison_summary.csv"],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(json.dumps(__import__("yaml").safe_load(decision_path.read_text(encoding="utf-8"))))
    evidence = payload["evidence"]
    assert evidence["rolling_window_count"] == 4
    assert evidence["rolling_rankic_mean"] == 0.06
    assert evidence["rolling_rankic_std"] == 0.01
    assert evidence["rolling_total_return_mean"] == 0.13
    assert evidence["rolling_max_drawdown_worst"] == -0.09
    assert evidence["rolling_turnover_mean"] == 0.22
    assert evidence["comparison_source"] == "experiments/mainline_rolling/comparison_summary.csv"
    assert evidence["lineage"]["bundle_id"] == "bundle_feature_173"
