import json
from pathlib import Path

import pandas as pd
import yaml

from qsys.research.stage_c import StageCAssessment
from qsys.research.stage_c_validation import validate_stage_c_assessment


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_stage_c_data_blocked_assessment_is_independently_validated(tmp_path) -> None:
    project = tmp_path / "project"
    research = project / "data/research"
    signal = {
        "signal_id": "signal",
        "signal_run_id": "run",
        "predictions_sha256": "a" * 64,
    }
    stage_b_path = research / "experiments/stage_b/stage_b_validation.json"
    _write_json(stage_b_path, {
        "status": "pass", "holdout_start": "2025-01-02",
        "holdout_consumed": False, "experiment_id": "stage_b",
        "signals": [signal],
    })

    market_root = project / "canonical"
    market_root.mkdir(parents=True)
    pd.DataFrame({
        "trade_date": ["20231129", "20231208"],
        "factor": [8.0, 9.0],
        "total_share": [100.0, 125.0],
    }).to_feather(market_root / "A.feather")

    action_dir = research / "corporate_actions/actions"
    action_dir.mkdir(parents=True)
    _write_json(action_dir / "manifest.json", {
        "schema_version": "corporate_actions_v1",
        "source": "dividend_source",
        "source_raw_artifact_sha256": "b" * 64,
    })
    pd.DataFrame({
        "instrument": ["B"], "effective_date": ["2023-12-08"],
    }).to_parquet(action_dir / "events.parquet", index=False)

    runs = []
    for top_n in (5, 20, 50):
        run_dir = research / f"backtests/top{top_n}"
        run_dir.mkdir(parents=True)
        manifest = {
            "corporate_action_policy": "not_modeled",
            "allocation_params": {"top_n": top_n},
            "signal_id": "signal", "signal_run_id": "run",
            "effective_end_date": "2024-12-31",
            "total_return": 0.1 + top_n / 1000,
            "final_value": 11_000_000 + top_n,
            "pit_execution_universe": {"artifact": "pit"},
            "signal_sources": [{"predictions_sha256": "a" * 64}],
        }
        metrics = {
            "trading_day_count": 484,
            "total_return": manifest["total_return"],
            "final_value": manifest["final_value"],
            "cagr": 0.05 + top_n / 1000,
            "sharpe": 0.4 + top_n / 1000,
            "max_drawdown": -0.25,
            "turnover_annualized": 4.8,
            "order_count_total": top_n,
            "filled_count_total": top_n,
            "rejected_count_total": 0,
        }
        _write_json(run_dir / "manifest.json", manifest)
        _write_json(run_dir / "metrics.json", metrics)
        pd.DataFrame({"trade_date": ["2023-01-03"]}).to_csv(
            run_dir / "daily_summary.csv", index=False
        )
        pd.DataFrame({
            "trade_date": ["2023-09-27", "2023-11-02"],
            "instrument": ["A", "A"], "status": ["filled", "filled"],
            "filled_qty": [100, 10], "side": ["buy", "sell"],
        }).to_csv(run_dir / "executions.csv", index=False)
        runs.append({"top_n": top_n, "path": str(run_dir)})

    config_path = project / "stage_c.yaml"
    config_path.write_text(yaml.safe_dump({
        "schema_version": "stage_c_assessment_request_v1",
        "assessment_id": "stage_c",
        "stage_b_validation_path": str(stage_b_path),
        "holdout_start": "2025-01-02",
        "promoted_signal": signal,
        "portfolio_sizes": [5, 20, 50],
        "strict_protocol": {"complete_accounting_required": True},
        "accounting_evidence": {
            "corporate_action_artifact": "actions",
            "canonical_data_root": str(market_root),
            "instrument": "A", "previous_trade_date": "20231129",
            "detection_trade_date": "20231208",
            "factor_rounding_relative_tolerance": 0.0005,
            "guard_error": "uncovered factor jump",
            "missing_capability": "rights-event coverage and policy",
        },
        "exploratory_runs": runs,
        "next_gate": "add coverage and policy",
    }, sort_keys=False), encoding="utf-8")

    produced = StageCAssessment.from_config(
        config_path, root=research, repo_root=project
    ).run()
    validated = validate_stage_c_assessment(
        config_path, root=research, repo_root=Path(__file__).parents[2]
    )
    assert produced["assessment_id"] == "stage_c"
    assert validated["validated"] is True
    assert validated["formal_status"] == "accounting_data_blocked"
    artifact = json.loads((
        research / "stage_c_assessments/stage_c/stage_c_assessment.json"
    ).read_text(encoding="utf-8"))
    assert artifact["portfolio_extractability"] == "not_established"
    assert [row["top_n"] for row in artifact[
        "exploratory_portfolio_comparison"
    ]] == [5, 20, 50]
