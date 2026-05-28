#!/usr/bin/env python3
"""Rolling backtest experiment — run all alpha_v1 variants.

Usage
-----
    python scripts/research/run_rolling_backtest.py
    python scripts/research/run_rolling_backtest.py --end-date 2026-05-22

Output
------
    experiments/research/rolling_backtest/<timestamp>/
        ├── summary.json              — aggregate metrics per variant
        ├── <variant>/
        │   ├── daily_summary.csv
        │   ├── trades.csv
        │   └── metrics.json
        └── variants_comparison.csv   — side-by-side metrics table
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from qsys.backtest.rolling_runner import (
    RollingBacktestRunner,
    make_alpha_v1_train_func,
    make_alpha_v1_predict_func,
    make_alpha_v1_data_loader,
    ALL_VARIANTS,
)
from qsys.model.alpha_v1_train import preload_training_data


def _format_pct(v: float | None) -> str:
    if v is None:
        return "N/A"
    return f"{v:.2%}"


def _format_float(v: float | None, decimals: int = 4) -> str:
    if v is None:
        return "N/A"
    return f"{v:.{decimals}f}"


def print_comparison_table(results) -> None:
    """Print a side-by-side comparison of all variants."""
    print("\n" + "=" * 120)
    print("Variant Comparison")
    print("=" * 120)

    header = (
        f"{'Variant':<20} {'Ann Ret':>9} {'Sharpe':>8} {'MDD':>9} {'Calmar':>8} "
        f"{'Ann TO':>9} {'Win Rate':>9} {'Hold Days':>10} {'Cost':>8} {'Days':>6}"
    )
    print(header)
    print("-" * 120)

    rows = []
    for vr in results.variants:
        m = vr.metrics
        ann_ret = _format_pct(m.get("annual_return"))
        sp = _format_float(m.get("sharpe"))
        mdd = _format_pct(m.get("max_drawdown"))
        cal = _format_float(m.get("calmar"))
        to_val = m.get("annual_turnover")
        to_str = f"{to_val:.2f}x" if to_val is not None and to_val != 0 else "N/A"
        wr = _format_pct(m.get("win_rate"))
        ahd = m.get("avg_holding_days")
        ahd_str = f"{ahd:.1f}" if ahd is not None and ahd != float("inf") else "inf"
        cost = _format_pct(m.get("cost_drag"))
        nd = str(m.get("n_trading_days", ""))

        print(
            f"{vr.name:<20} {ann_ret:>9} {sp:>8} {mdd:>9} {cal:>8} "
            f"{to_str:>9} {wr:>9} {ahd_str:>10} {cost:>8} {nd:>6}"
        )
        rows.append({
            "variant": vr.name,
            "annual_return": m.get("annual_return"),
            "sharpe": m.get("sharpe"),
            "max_drawdown": m.get("max_drawdown"),
            "calmar": m.get("calmar"),
            "annual_turnover": m.get("annual_turnover"),
            "win_rate": m.get("win_rate"),
            "avg_holding_days": m.get("avg_holding_days"),
            "cost_drag": m.get("cost_drag"),
            "n_trading_days": m.get("n_trading_days"),
            "total_return": m.get("total_return"),
            "annual_volatility": m.get("annual_volatility"),
        })

    print("=" * 120)
    return pd.DataFrame(rows)


def save_results(
    results,
    output_dir: Path,
) -> None:
    """Save all results to disk."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Per-variant artifacts
    for vr in results.variants:
        var_dir = output_dir / vr.name
        var_dir.mkdir(parents=True, exist_ok=True)

        # Daily summary
        if not vr.backtest_result.daily.empty:
            daily = vr.backtest_result.daily.copy()
            daily.to_csv(var_dir / "daily_summary.csv", index=False)

        # Trades
        if not vr.backtest_result.trades.empty:
            vr.backtest_result.trades.to_csv(var_dir / "trades.csv", index=False)

        # Metrics (without details)
        clean_metrics = {
            k: v for k, v in vr.metrics.items()
            if k != "details"
        }
        (var_dir / "metrics.json").write_text(
            json.dumps(clean_metrics, indent=2, default=str, ensure_ascii=False),
        )

    # Comparison table
    comparison = print_comparison_table(results)
    comparison.to_csv(output_dir / "variants_comparison.csv", index=False)

    # Summary JSON
    summary = {
        "experiment": "alpha_v1_rolling_backtest",
        "run_at": datetime.now().isoformat(),
        "total_wall_time_seconds": results.total_wall_time,
        "n_windows": len(results.windows),
        "n_variants": len(results.variants),
        "variants": [],
    }
    for vr in results.variants:
        clean_metrics = {
            k: v for k, v in vr.metrics.items()
            if k != "details"
        }
        summary["variants"].append({
            "name": vr.name,
            "metrics": clean_metrics,
        })
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str, ensure_ascii=False),
    )

    print(f"\nResults saved to: {output_dir}/")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rolling backtest experiment for alpha_v1 variants",
    )
    parser.add_argument(
        "--start-date", default="2024-01-01",
        help="Backtest start date (YYYY-MM-DD, default: 2024-01-01)",
    )
    parser.add_argument(
        "--end-date", default="2026-05-22",
        help="Backtest end date (YYYY-MM-DD, default: 2026-05-22)",
    )
    parser.add_argument(
        "--output-dir", default=None,
        help="Output directory (default: experiments/research/rolling_backtest/<timestamp>)",
    )
    parser.add_argument(
        "--variants", nargs="+", default=None,
        help="Specific variants to run (default: all). "
             f"Options: {[v.name for v in ALL_VARIANTS]}",
    )
    parser.add_argument(
        "--train-days", type=int, default=504,
    )
    parser.add_argument(
        "--test-days", type=int, default=5,
    )
    parser.add_argument(
        "--step-days", type=int, default=5,
    )
    parser.add_argument(
        "--initial-capital", type=float, default=1_000_000.0,
    )
    parser.add_argument(
        "--no-preload", action="store_true",
        help="Skip pre-loading training data (load per-window instead)",
    )
    args = parser.parse_args()

    # Select variants
    if args.variants:
        variants = [v for v in ALL_VARIANTS if v.name in args.variants]
        missing = set(args.variants) - {v.name for v in variants}
        if missing:
            print(f"Unknown variants: {missing}")
            sys.exit(1)
    else:
        variants = ALL_VARIANTS

    # Resolve output dir
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = _PROJECT_ROOT / "experiments" / "research" / "rolling_backtest" / timestamp

    print(f"Output: {output_dir}/")

    # Pre-load training data (one qlib call)
    preloaded = None
    if not args.no_preload:
        print("\n[Preload] Loading all training data...")
        t0 = time.time()
        preloaded = preload_training_data(args.end_date)
        print(f"[Preload] Done in {time.time() - t0:.1f}s")

    # Create runner with alpha_v1-specific functions
    runner = RollingBacktestRunner(
        train_func=make_alpha_v1_train_func(project_root=_PROJECT_ROOT),
        predict_func=make_alpha_v1_predict_func(universe="csi300"),
        data_loader=make_alpha_v1_data_loader(universe="csi300"),
        variants=variants,
    )

    # Run
    t_start = time.time()
    results = runner.run(
        start_date=args.start_date,
        end_date=args.end_date,
        preloaded_data=preloaded,
        train_days=args.train_days,
        test_days=args.test_days,
        step_days=args.step_days,
        initial_capital=args.initial_capital,
    )
    total = time.time() - t_start

    # Save + print
    save_results(results, output_dir)

    print(f"\nTotal time: {total:.0f}s ({total / 60:.1f} min)")


if __name__ == "__main__":
    main()
