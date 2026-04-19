#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import click
import pandas as pd

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.research import MAINLINE_OBJECTS, decision_payload, resolve_subject_decision
from qsys.reports.unified_schema import write_csv, write_json


def _pct(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value * 100:.2f}%"


def _fmt(value: float | None, digits: int = 4) -> str:
    if value is None:
        return ""
    return f"{value:.{digits}f}"


def _build_rolling_daily_result(metrics: pd.DataFrame) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    equity = 1_000_000.0
    for row in metrics.to_dict(orient="records"):
        window_return = float(row.get("total_return") or 0.0)
        equity = equity * (1.0 + window_return)
        turnover_ratio = float(row.get("turnover") or 0.0)
        rows.append(
            {
                "date": row.get("test_end"),
                "total_assets": equity,
                "zero_cost_total_assets": equity,
                "daily_return": window_return,
                "daily_turnover": turnover_ratio * 1_000_000.0,
                "trade_count": None,
            }
        )
    return rows


@click.command(name="publish_mainline_rolling_ui_reports")
@click.option("--rolling_dir", default="experiments/mainline_rolling", help="Directory containing per-object rolling outputs")
@click.option("--reports_dir", default="experiments/reports", help="Directory for UI-readable backtest reports")
def main(rolling_dir: str, reports_dir: str) -> None:
    rolling_root = (project_root / rolling_dir).resolve()
    reports_root = (project_root / reports_dir).resolve()
    reports_root.mkdir(parents=True, exist_ok=True)

    comparison_path = rolling_root / "comparison_summary.csv"
    comparison = pd.read_csv(comparison_path) if comparison_path.exists() else pd.DataFrame()
    comparison_index = {
        str(row["mainline_object_name"]): row
        for _, row in comparison.iterrows()
    }

    for mainline_object_name, spec in MAINLINE_OBJECTS.items():
        summary_path = rolling_root / mainline_object_name / "rolling_summary.json"
        metrics_path = rolling_root / mainline_object_name / "rolling_metrics.csv"
        windows_path = rolling_root / mainline_object_name / "rolling_windows.csv"
        if not summary_path.exists() or not metrics_path.exists() or not windows_path.exists():
            raise FileNotFoundError(f"Missing rolling artifacts for {mainline_object_name}")

        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        metrics = pd.read_csv(metrics_path)
        first_window = metrics.iloc[0].to_dict() if not metrics.empty else {}
        last_window = metrics.iloc[-1].to_dict() if not metrics.empty else {}
        daily_result_path = rolling_root / mainline_object_name / "rolling_daily_result.csv"
        write_csv(daily_result_path, _build_rolling_daily_result(metrics))
        decision = resolve_subject_decision(
            subject_type="mainline_object",
            subject_ids=[mainline_object_name],
        )
        decision_info = decision_payload(decision)
        comparison_row = comparison_index.get(mainline_object_name)

        payload = {
            "workflow": "backtest",
            "run_id": f"mainline_rolling_{mainline_object_name}",
            "timestamp": datetime.now().isoformat(),
            "status": "success",
            "signal_date": first_window.get("test_start"),
            "execution_date": last_window.get("test_end"),
            "data_status": {},
            "model_info": {
                "model_path": f"data/models/{spec.model_name}",
                "model_name": spec.model_name,
                "top_k": 5,
                "universe": "csi300",
                "mainline_object_name": mainline_object_name,
                "bundle_id": spec.bundle_id,
                "legacy_feature_set_alias": spec.legacy_feature_set_alias,
                "input_mode": "rolling_eval",
                "decision_status": decision_info.get("status"),
            },
            "plan_summary": {},
            "blockers": [],
            "notes": [
                "version=mainline_rolling_v1",
                f"rolling_summary={summary_path.relative_to(project_root)}",
                f"comparison_summary={comparison_path.relative_to(project_root) if comparison_path.exists() else ''}",
            ],
            "sections": [
                {
                    "name": "Performance",
                    "status": "success",
                    "message": "rolling mainline evaluation summary",
                    "metrics": {
                        "rolling_window_count": int(summary.get("rolling_window_count") or 0),
                        "total_return": _pct(summary.get("rolling_total_return_mean")),
                        "total_return_median": _pct(summary.get("rolling_total_return_median")),
                        "rankic_mean": _fmt(summary.get("rolling_rankic_mean")),
                        "rankic_std": _fmt(summary.get("rolling_rankic_std")),
                        "max_drawdown": _pct(summary.get("rolling_max_drawdown_worst")),
                        "turnover_mean": _fmt(summary.get("rolling_turnover_mean")),
                        "empty_portfolio_ratio_mean": _fmt(summary.get("rolling_empty_portfolio_ratio_mean")),
                    },
                    "details": {
                        "decision_status": decision_info.get("status"),
                        "decision_reason": decision_info.get("reason"),
                        "comparison": comparison_row.to_dict() if comparison_row is not None else {},
                    },
                }
            ],
            "artifacts": {
                "daily_result": str(daily_result_path.relative_to(project_root)),
                "metrics": str(summary_path.relative_to(project_root)),
                "config_snapshot": str(windows_path.relative_to(project_root)),
                "decisions": str((project_root / "research" / "decisions").relative_to(project_root)),
            },
            "duration_seconds": None,
        }
        report_path = reports_root / f"backtest_mainline_rolling_{mainline_object_name}.json"
        write_json(report_path, payload)
        print(f"ui_report={report_path}")


if __name__ == "__main__":
    main()
