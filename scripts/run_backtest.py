"""
Primary backtest entrypoint.

Purpose:
- run a historical backtest for a saved model artifact
- save daily result csv + structured backtest report

Typical usage:
- python scripts/run_backtest.py --model_path data/models/qlib_lgbm_extended --start 2025-01-01 --end 2026-03-20 --top_k 5

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
from qsys.strategy.generator import load_model_artifact_metadata
from qsys.utils.logger import log


@click.command()
@click.option("--model_path", type=str, default=None, help="Model directory. Defaults to data/models/qlib_lgbm")
@click.option("--universe", type=str, default="csi300", help="Backtest universe")
@click.option("--start", type=str, default="2022-01-01", help="Backtest start date")
@click.option("--end", type=str, default="2022-03-01", help="Backtest end date")
@click.option("--top_k", type=int, default=30, help="Top K positions")
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
        model_meta=load_model_artifact_metadata(model_dir),
        start_date=start,
        end_date=end,
        top_k=top_k,
        universe=universe,
        duration_seconds=duration,
        daily_result_path=str(daily_path),
    )
    
    report_path = BacktestReport.save(report)
    log.info(f"Structured report saved to {report_path}")
    
    # Print markdown summary
    print("\n" + "=" * 60)
    print(report.to_markdown())
    print("=" * 60)


if __name__ == "__main__":
    main()
