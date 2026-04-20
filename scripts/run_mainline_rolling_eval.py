#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import click
import pandas as pd

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.backtest import BacktestEngine
from qsys.research.rolling import (
    DEFAULT_STEP_DAYS,
    DEFAULT_TEST_WINDOW_DAYS,
    RollingDefaults,
    build_rolling_summary,
    build_rolling_windows,
    canonical_model_path,
    compute_window_metrics,
    resolve_mainline_specs,
    snapshot_train_window,
)
from qsys.research.spec import V1_IMPL1_FIXED_LABEL_HORIZON
from qsys.strategy.engine import DEFAULT_TOP_K
from scripts.run_backtest import build_backtest_lineage, load_training_snapshot


@click.command(name="run_mainline_rolling_eval")
@click.option("--start", default="2025-01-02", help="Rolling evaluation start date")
@click.option("--end", default="2026-03-20", help="Rolling evaluation end date")
@click.option("--universe", default="csi300", help="Backtest universe")
@click.option("--top_k", default=DEFAULT_TOP_K, type=int, help="Top-K holdings")
@click.option("--strategy_type", default="rank_topk", help="Strategy type")
@click.option("--label_horizon", default=V1_IMPL1_FIXED_LABEL_HORIZON, help="Label horizon used by signal metrics")
@click.option("--test_window_days", default=DEFAULT_TEST_WINDOW_DAYS, type=int, show_default=True, help="Fixed rolling test window length in calendar days")
@click.option("--step_days", default=DEFAULT_STEP_DAYS, type=int, show_default=True, help="Fixed rolling step size in calendar days")
@click.option("--mainline_object", "mainline_objects", multiple=True, help="Optional mainline object filter; repeat for multiple values")
@click.option("--output_dir", default="experiments/mainline_rolling", help="Directory for rolling outputs")
def main(
    start: str,
    end: str,
    universe: str,
    top_k: int,
    strategy_type: str,
    label_horizon: str,
    test_window_days: int,
    step_days: int,
    mainline_objects: tuple[str, ...],
    output_dir: str,
) -> None:
    out_dir = (project_root / output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    defaults = RollingDefaults(
        universe=universe,
        top_k=top_k,
        strategy_type=strategy_type,
        label_horizon=label_horizon,
        test_window_days=test_window_days,
        step_days=step_days,
    )

    summary_rows: list[dict] = []
    for spec in resolve_mainline_specs(mainline_objects or None):
        model_path = canonical_model_path(project_root, spec)
        if not model_path.exists():
            raise FileNotFoundError(f"Canonical model path not found for {spec.mainline_object_name}: {model_path}")

        snapshot = load_training_snapshot(model_path)
        lineage = build_backtest_lineage(snapshot)
        train_start, train_end = snapshot_train_window(snapshot)
        windows = build_rolling_windows(
            start=start,
            end=end,
            test_window_days=test_window_days,
            step_days=step_days,
            train_start=train_start,
            train_end=train_end,
        )

        object_dir = out_dir / spec.mainline_object_name
        object_dir.mkdir(parents=True, exist_ok=True)

        window_rows = [
            {
                "mainline_object_name": spec.mainline_object_name,
                "bundle_id": spec.bundle_id,
                "legacy_feature_set_alias": spec.legacy_feature_set_alias,
                **window.to_dict(),
            }
            for window in windows
        ]
        windows_frame = pd.DataFrame(window_rows)
        windows_path = object_dir / "rolling_windows.csv"
        windows_frame.to_csv(windows_path, index=False)

        metrics_path = object_dir / "rolling_metrics.csv"
        metric_rows: list[dict] = []
        completed_window_ids: set[str] = set()
        if metrics_path.exists():
            existing_metrics = pd.read_csv(metrics_path)
            metric_rows = existing_metrics.to_dict(orient="records")
            completed_window_ids = {str(v) for v in existing_metrics.get("window_id", pd.Series(dtype=str)).dropna().tolist()}

        for window in windows:
            if window.window_id in completed_window_ids:
                continue
            engine = BacktestEngine(
                model_path=model_path,
                universe=universe,
                start_date=window.test_start,
                end_date=window.test_end,
                top_k=top_k,
                strategy_type=strategy_type,
                label_horizon=label_horizon,
                strategy_params={},
            )
            result = engine.run()
            metric_rows.append(
                compute_window_metrics(
                    spec=spec,
                    window=window,
                    daily_result=result,
                    signal_metrics=engine.last_signal_metrics or {},
                )
            )
            pd.DataFrame(metric_rows).to_csv(metrics_path, index=False)

        metrics_frame = pd.DataFrame(metric_rows)
        metrics_frame.to_csv(metrics_path, index=False)

        summary_payload = build_rolling_summary(metrics_frame, defaults)
        summary_payload.update(
            {
                "model_path": str(model_path),
                "lineage": lineage,
                "artifacts": {
                    "rolling_windows": str(windows_path),
                    "rolling_metrics": str(metrics_path),
                },
            }
        )
        summary_path = object_dir / "rolling_summary.json"
        summary_path.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        summary_rows.append(summary_payload)

    if summary_rows:
        pd.DataFrame(summary_rows).to_csv(out_dir / "rolling_summaries.csv", index=False)

    print(f"rolling_output_dir={out_dir}")


if __name__ == "__main__":
    main()
