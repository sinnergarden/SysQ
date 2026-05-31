"""
Run a single backtest using the production model with 100k initial cash.

Usage:
    python scripts/run_prod_backtest.py

Output:
    experiments/backtest_result.csv
    experiments/reports/backtest_*.json  (visible in UI)
"""
import json
import sys
import time
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

import pandas as pd

from qsys.backtest import BacktestEngine
from qsys.config import cfg
from qsys.reports.backtest import BacktestReport
from qsys.reports.unified_schema import unified_run_artifacts, write_csv, write_json
from qsys.trader.account import Account
from qsys.utils.logger import log

START = "2024-01-01"
END = "2026-05-13"
INITIAL_CASH = 100_000.0
TOP_K = 5
UNIVERSE = "csi300"


def find_production_model() -> str:
    """Resolve the production model path from manifest."""
    manifest_path = Path(cfg.get_path("root")) / "models" / "production_manifest.yaml"
    if manifest_path.exists():
        import yaml
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        model_path = manifest.get("model_path")
        if model_path:
            return str(project_root / model_path)
    # Fallback: find the default model
    models_dir = Path(cfg.get_path("root")) / "models"
    candidates = sorted(models_dir.glob("qlib_lgbm_*"))
    if candidates:
        return str(candidates[-1])
    raise FileNotFoundError("No production model found")


def main():
    start_time = time.time()
    model_path = find_production_model()
    log.info("Production model: %s", model_path)
    log.info("Backtest period: %s to %s", START, END)
    log.info("Initial cash: %.0f, Top K: %d, Universe: %s", INITIAL_CASH, TOP_K, UNIVERSE)

    account = Account(init_cash=INITIAL_CASH)
    engine = BacktestEngine(
        model_path=model_path,
        universe=UNIVERSE,
        start_date=START,
        end_date=END,
        account=account,
        top_k=TOP_K,
    )

    res = engine.run()
    if res.empty:
        log.error("Backtest produced no results")
        return

    # Save daily result
    root_path = Path(cfg.get_path("root"))
    save_dir = root_path / "experiments"
    save_dir.mkdir(parents=True, exist_ok=True)
    daily_path = save_dir / "backtest_result.csv"
    res.to_csv(daily_path, index=False)
    log.info("Daily result saved: %s", daily_path)

    # Build and save report
    duration = time.time() - start_time
    report = BacktestReport.from_backtest_result(
        result_df=res,
        model_path=str(model_path),
        start_date=START,
        end_date=END,
        top_k=TOP_K,
        universe=UNIVERSE,
        duration_seconds=duration,
        daily_result_path=str(daily_path),
        experiment_spec={
            "initial_cash": INITIAL_CASH,
            "model_path": str(model_path),
            "universe": UNIVERSE,
            "start": START,
            "end": END,
            "top_k": TOP_K,
        },
    )

    unified_paths = unified_run_artifacts(save_dir)
    metrics_payload = {}
    for section in report.sections:
        if section.name == "Performance":
            metrics_payload = dict(section.metrics)
            break

    report.artifacts["signal_metrics"] = write_json(
        unified_paths["signal_metrics"],
        engine.last_signal_metrics or {"status": "not_available"},
    )
    report.artifacts["group_returns"] = write_csv(
        unified_paths["group_returns"],
        engine.last_group_returns.to_dict(orient="records") if not engine.last_group_returns.empty else [],
    )
    report.artifacts["execution_audit"] = write_csv(unified_paths["execution_audit"], [])
    report.artifacts["metrics"] = write_json(unified_paths["metrics"], metrics_payload)

    report_path = BacktestReport.save(report)
    log.info("Report saved: %s", report_path)

    print("\n" + "=" * 60)
    print(report.to_markdown())
    print("=" * 60)

    if not res.empty:
        final = res.iloc[-1]
        print(f"\nFinal: total_assets={final['total_assets']:.2f}, return={((final['total_assets']/INITIAL_CASH)-1)*100:.2f}%")


if __name__ == "__main__":
    main()
