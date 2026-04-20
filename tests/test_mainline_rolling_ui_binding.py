from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from qsys.research_ui.assembler import ResearchCockpitRepository
from scripts.publish_mainline_rolling_ui_reports import main as publish_mainline_rolling_ui_reports_main


def test_publish_mainline_rolling_ui_reports_writes_backtest_reports(tmp_path: Path) -> None:
    rolling_root = tmp_path / "experiments" / "mainline_rolling"
    reports_root = tmp_path / "experiments" / "reports"
    for name, bundle_id, alias in [
        ("feature_173", "bundle_feature_173", "extended"),
        ("feature_254", "bundle_feature_254", "semantic_all_features"),
        ("feature_254_absnorm", "bundle_feature_254_absnorm", "semantic_all_features_absnorm"),
        ("feature_254_trimmed", "bundle_feature_254_trimmed", "semantic_all_features_trimmed"),
        ("feature_254_absnorm_trimmed", "bundle_feature_254_absnorm_trimmed", "semantic_all_features_absnorm_trimmed"),
    ]:
        obj_dir = rolling_root / name
        obj_dir.mkdir(parents=True, exist_ok=True)
        (obj_dir / "rolling_summary.json").write_text(
            json.dumps(
                {
                    "mainline_object_name": name,
                    "bundle_id": bundle_id,
                    "legacy_feature_set_alias": alias,
                    "rolling_window_count": 20,
                    "rolling_total_return_mean": 0.1,
                    "rolling_total_return_median": 0.08,
                    "rolling_rankic_mean": 0.02,
                    "rolling_rankic_std": 0.01,
                    "rolling_max_drawdown_worst": -0.1,
                    "rolling_turnover_mean": 1.2,
                    "rolling_empty_portfolio_ratio_mean": 0.0,
                }
            ),
            encoding="utf-8",
        )
        (obj_dir / "rolling_metrics.csv").write_text(
            "mainline_object_name,bundle_id,legacy_feature_set_alias,window_id,train_start,train_end,test_start,test_end,total_return,max_drawdown,turnover,IC,RankIC,long_short_spread,empty_portfolio_ratio,avg_holding_count\n"
            f"{name},{bundle_id},{alias},window_001,2021-01-01,2024-12-31,2025-01-02,2025-03-05,0.1,-0.1,1.2,0.01,0.02,0.03,0.0,5\n",
            encoding="utf-8",
        )
        (obj_dir / "rolling_windows.csv").write_text(
            "mainline_object_name,bundle_id,legacy_feature_set_alias,window_id,train_start,train_end,test_start,test_end\n"
            f"{name},{bundle_id},{alias},window_001,2021-01-01,2024-12-31,2025-01-02,2025-03-05\n",
            encoding="utf-8",
        )
    (rolling_root / "comparison_summary.csv").write_text(
        "mainline_object_name,bundle_id,legacy_feature_set_alias,rolling_window_count,rolling_total_return_mean,rolling_total_return_median,rolling_rankic_mean,rolling_rankic_std,rolling_max_drawdown_worst,rolling_turnover_mean,rolling_empty_portfolio_ratio_mean,decision_status,decision_reason\n"
        "feature_173,bundle_feature_173,extended,20,0.1,0.08,0.02,0.01,-0.1,1.2,0.0,candidate,baseline\n"
        "feature_254,bundle_feature_254,semantic_all_features,20,0.0,-0.01,-0.02,0.03,-0.2,1.3,0.0,research_only,research\n"
        "feature_254_absnorm,bundle_feature_254_absnorm,semantic_all_features_absnorm,20,0.03,0.02,0.01,0.02,-0.12,1.1,0.0,research_only,watch\n"
        "feature_254_trimmed,bundle_feature_254_trimmed,semantic_all_features_trimmed,20,0.04,0.03,0.015,0.02,-0.1,1.0,0.0,research_only,trimmed\n"
        "feature_254_absnorm_trimmed,bundle_feature_254_absnorm_trimmed,semantic_all_features_absnorm_trimmed,20,0.05,0.04,0.018,0.015,-0.09,0.95,0.0,research_only,trimmed_absnorm\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    with patch("scripts.publish_mainline_rolling_ui_reports.project_root", tmp_path):
        result = runner.invoke(publish_mainline_rolling_ui_reports_main, [])
    assert result.exit_code == 0, result.output
    report_path = reports_root / "backtest_mainline_rolling_feature_173.json"
    assert report_path.exists()
    assert (reports_root / "backtest_mainline_rolling_feature_254.json").exists()
    assert (reports_root / "backtest_mainline_rolling_feature_254_absnorm.json").exists()
    assert (reports_root / "backtest_mainline_rolling_feature_254_trimmed.json").exists()
    assert (reports_root / "backtest_mainline_rolling_feature_254_absnorm_trimmed.json").exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["artifacts"]["daily_result"].endswith("rolling_daily_result.csv")
    assert (rolling_root / "feature_173" / "rolling_daily_result.csv").exists()


def test_research_ui_keeps_three_mainline_rolling_runs_distinct(tmp_path: Path) -> None:
    reports_root = tmp_path / "experiments" / "reports"
    reports_root.mkdir(parents=True, exist_ok=True)
    for name in ["feature_173", "feature_254", "feature_254_absnorm", "feature_254_trimmed", "feature_254_absnorm_trimmed"]:
        payload = {
            "workflow": "backtest",
            "run_id": f"mainline_rolling_{name}",
            "timestamp": "2026-04-19T23:00:00",
            "status": "success",
            "signal_date": "2025-01-02",
            "execution_date": "2026-03-20",
            "model_info": {
                "model_path": f"data/models/{name}",
                "model_name": name,
                "top_k": 5,
                "universe": "csi300",
                "mainline_object_name": name,
                "bundle_id": f"bundle_{name}",
            },
            "sections": [{"name": "Performance", "metrics": {"total_return": "1.00%"}}],
            "artifacts": {},
            "notes": ["version=mainline_rolling_v1"],
        }
        (reports_root / f"backtest_mainline_rolling_{name}.json").write_text(json.dumps(payload), encoding="utf-8")

    repo = ResearchCockpitRepository(project_root=tmp_path)
    runs = repo.list_backtest_runs(limit=10)
    run_ids = {run.run_id for run in runs}
    assert "mainline_rolling_feature_173" in run_ids
    assert "mainline_rolling_feature_254" in run_ids
    assert "mainline_rolling_feature_254_absnorm" in run_ids
    assert "mainline_rolling_feature_254_trimmed" in run_ids
    assert "mainline_rolling_feature_254_absnorm_trimmed" in run_ids
