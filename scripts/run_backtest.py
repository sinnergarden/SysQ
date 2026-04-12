"""
Primary backtest entrypoint.

Purpose:
- run a historical backtest for a saved model artifact
- save daily result csv + structured backtest report

Typical usage:
- python scripts/run_backtest.py --model_path data/models/qlib_lgbm_phase123_extended --start 2025-01-01 --end 2026-03-20 --top_k 5

Key args:
- --model_path: model directory to evaluate
- --universe: backtest universe
- --start / --end: backtest window
- --top_k: portfolio breadth
"""

import sys
from pathlib import Path
import time

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

import click

from qsys.backtest import BacktestEngine
from qsys.config import cfg
from qsys.reports.backtest import BacktestReport
from qsys.reports.unified_schema import unified_run_artifacts, write_csv, write_json
from qsys.strategy.engine import DEFAULT_TOP_K
from qsys.utils.logger import log


@click.command()
@click.option("--model_path", type=str, default=None, help="Model directory. Defaults to data/models/qlib_lgbm_phase123")
@click.option("--universe", type=str, default="csi300", help="Backtest universe")
@click.option("--start", type=str, default="2022-01-01", help="Backtest start date")
@click.option("--end", type=str, default="2022-03-01", help="Backtest end date")
@click.option("--top_k", type=int, default=DEFAULT_TOP_K, help="Top K positions")
def main(model_path, universe, start, end, top_k):
    start_time = time.time()
    root_path = cfg.get_path("root")
    if root_path is None:
        raise ValueError("Root path not configured")

    model_dir = Path(model_path) if model_path else root_path / "models" / "qlib_lgbm"
    engine = BacktestEngine(model_dir, universe=universe, start_date=start, end_date=end, top_k=top_k)
    res = engine.run()

    if res.empty:
        log.error("Backtest produced no results")
        return

    save_dir = root_path / "experiments"
    save_dir.mkdir(parents=True, exist_ok=True)
    daily_path = save_dir / "backtest_result.csv"
    res.to_csv(daily_path, index=False)
    written = engine.save_report(save_dir, prefix="backtest")

    log.info(f"Backtest daily result saved to {daily_path}")
    for name, path in written.items():
        log.info(f"Backtest {name} saved to {path}")
    
    # Generate structured report
    duration = time.time() - start_time
    report = BacktestReport.from_backtest_result(
        result_df=res,
        model_path=str(model_dir),
        start_date=start,
        end_date=end,
        top_k=top_k,
        universe=universe,
        duration_seconds=duration,
        daily_result_path=str(daily_path),
    )

    unified_paths = unified_run_artifacts(save_dir)
    training_summary_path = model_dir / "training_summary.json"
    training_summary = {}
    if training_summary_path.exists():
        import json
        training_summary = json.loads(training_summary_path.read_text(encoding="utf-8"))
    metrics_payload = {}
    for section in report.sections:
        if section.name == "Performance":
            metrics_payload = dict(section.metrics)
            break
    report.artifacts["config_snapshot"] = write_json(unified_paths["config_snapshot"], {
        "model_path": str(model_dir),
        "start": start,
        "end": end,
        "top_k": top_k,
        "universe": universe,
    })
    report.artifacts["training_summary"] = write_json(unified_paths["training_summary"], training_summary)
    report.artifacts["execution_audit"] = write_csv(unified_paths["execution_audit"], [])
    report.artifacts["suspicious_trades"] = write_csv(unified_paths["suspicious_trades"], [])
    report.artifacts["metrics"] = write_json(unified_paths["metrics"], metrics_payload)

    report_path = BacktestReport.save(report)
    log.info(f"Structured report saved to {report_path}")
    
    # Print markdown summary
    print("\n" + "=" * 60)
    print(report.to_markdown())
    print("=" * 60)


if __name__ == "__main__":
    main()
