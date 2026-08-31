from __future__ import annotations

import json

from qsys.research_ui.assembler import ResearchCockpitRepository


def test_synthetic_live_rolling_sections_resolve_without_listing_runs(tmp_path):
    web_root = tmp_path / "qsys" / "research_ui" / "web"
    web_root.mkdir(parents=True, exist_ok=True)
    (web_root / "index.html").write_text("<html></html>", encoding="utf-8")

    run_dir = tmp_path / "experiments" / "mainline_rolling_runs" / "20260518_portfoliofix_xs5" / "feature_254_xs5"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "rolling_summary.json").write_text(
        json.dumps({
            "defaults": {"universe": "csi300", "top_k": 5, "strategy_type": "rank_topk", "step_days": 5},
            "lineage": {"label_type": "1d_fixed_in_v1_impl1"},
            "model_path": "data/models/feature_254_xs5",
        }),
        encoding="utf-8",
    )
    (run_dir / "rolling_metrics.csv").write_text(
        "mainline_object_name,bundle_id,legacy_feature_set_alias,window_id,train_start,train_end,test_start,test_end,total_return,max_drawdown,turnover,IC,RankIC,long_short_spread\n"
        "feature_254_xs5,bundle_feature_254_xs5,semantic_all_features,window_001,2021-01-01,2024-12-31,2025-01-02,2025-03-05,0.10,-0.10,1.20,0.01,0.02,0.03\n"
        "feature_254_xs5,bundle_feature_254_xs5,semantic_all_features,window_002,2021-01-06,2025-01-05,2025-03-06,2025-05-09,-0.05,-0.08,0.90,-0.02,0.01,-0.01\n",
        encoding="utf-8",
    )
    (run_dir / "rolling_windows.csv").write_text(
        "mainline_object_name,bundle_id,legacy_feature_set_alias,window_id,train_start,train_end,test_start,test_end\n"
        "feature_254_xs5,bundle_feature_254_xs5,semantic_all_features,window_001,2021-01-01,2024-12-31,2025-01-02,2025-03-05\n"
        "feature_254_xs5,bundle_feature_254_xs5,semantic_all_features,window_002,2021-01-06,2025-01-05,2025-03-06,2025-05-09\n",
        encoding="utf-8",
    )

    run_id = "live_rolling__20260518_portfoliofix_xs5__feature_254_xs5"

    repo = ResearchCockpitRepository(project_root=tmp_path)
    payload = repo.get_backtest_sections(run_id)
    assert payload["sections"]
    assert "rolling_windows" in payload["artifacts"]
    assert "rolling_metrics" in payload["artifacts"]
    assert "signal_metrics" in payload["artifacts"]
    assert "rolling_stability" in payload["artifacts"]

    summary = repo.get_backtest_summary(run_id)
    assert summary.run_id == run_id
    assert len(repo.get_backtest_daily_points(run_id)) == 2
