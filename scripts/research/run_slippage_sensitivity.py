#!/usr/bin/env python3
"""Slippage sensitivity analysis — baseline / concentrated / diversified / daily_rebalance.

Usage
-----
    python scripts/research/run_slippage_sensitivity.py

Output
------
    experiments/research/slippage_sensitivity/<timestamp>/
        ├── strategy_comparison_net.csv   — all variants × slippage
        ├── signals.cache                 — cached predictions (reusable)
        └── <variant>_s<slip>/            — per-variant daily+trades
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from qsys.backtest.rolling_runner import (
    RollingBacktestRunner,
    VariantConfig,
    make_alpha_v1_train_func,
    make_alpha_v1_predict_func,
    make_alpha_v1_data_loader,
)
from qsys.model.alpha_v1_train import preload_training_data

# ── Strategy templates (portfolio params only, no slippage) ────────────────

STRATEGIES = {
    "baseline":      dict(top_n=20, buffer_hold=60, buffer_buy=40, single_stock_cap=0.07, rebalance_freq="weekly"),
    "concentrated":  dict(top_n=10, buffer_hold=10, buffer_buy=10, single_stock_cap=0.15, rebalance_freq="weekly"),
    "diversified":   dict(top_n=50, buffer_hold=100, buffer_buy=80, single_stock_cap=0.03, rebalance_freq="weekly"),
    "daily_rebalance": dict(top_n=20, buffer_hold=60, buffer_buy=40, single_stock_cap=0.07, rebalance_freq="daily"),
}

SLIPPAGE_LEVELS = [0.0, 0.0005, 0.001, 0.002]


def _build_variants() -> list[VariantConfig]:
    """Generate 4 × 4 = 16 variants."""
    variants = []
    for sname, sparams in STRATEGIES.items():
        for slip in SLIPPAGE_LEVELS:
            slip_label = f"{slip:.0e}" if slip > 0 else "0"
            variants.append(VariantConfig(
                name=f"{sname}_s{slip_label}",
                slippage=slip,
                **sparams,
            ))
    return variants


def print_net_table(results) -> pd.DataFrame:
    """Print net-of-all-costs comparison table."""
    print("\n" + "=" * 120)
    print("Slippage Sensitivity — Net Returns (after commission + stamp_duty + slippage)")
    print("=" * 120)

    header = (
        f"{'Variant':<24} {'Strategy':<14} {'Slippage':>9} "
        f"{'Ann Ret':>9} {'Sharpe':>8} {'MDD':>9} "
        f"{'Ann TO':>9} {'Days':>6}"
    )
    print(header)
    print("-" * 120)

    rows = []
    for vr in results.variants:
        m = vr.metrics
        # Parse name back
        parts = vr.name.rsplit("_s", 1)
        sname = parts[0]
        slip_str = parts[1] if len(parts) > 1 else "0"
        slip_val = float(slip_str.replace("e-0", "e-")) if slip_str != "0" else 0.0

        ann_ret = f"{m.get('annual_return', 0):.2%}"
        sp = f"{m.get('sharpe', 0):.4f}"
        mdd = f"{m.get('max_drawdown', 0):.2%}"
        to_val = m.get("annual_turnover", 0)
        to_str = f"{to_val:.2f}x" if to_val else "N/A"
        nd = str(m.get("n_trading_days", ""))

        print(
            f"{vr.name:<24} {sname:<14} {slip_val:>9.4f} "
            f"{ann_ret:>9} {sp:>8} {mdd:>9} "
            f"{to_str:>9} {nd:>6}"
        )
        rows.append({
            "variant": vr.name,
            "strategy": sname,
            "slippage": slip_val,
            "commission_included": True,
            "stamp_duty_included": True,
            "slippage_included": slip_val,
            "annual_return_net": m.get("annual_return"),
            "sharpe_net": m.get("sharpe"),
            "max_drawdown_net": m.get("max_drawdown"),
            "turnover": m.get("annual_turnover"),
            "win_rate": m.get("win_rate"),
            "total_return": m.get("total_return"),
            "n_trading_days": m.get("n_trading_days"),
        })

    print("=" * 120)
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Slippage sensitivity analysis")
    parser.add_argument("--start-date", default="2024-01-02")
    parser.add_argument("--end-date", default="2026-05-22")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--no-cache", action="store_true",
                        help="Recompute signals even if cache exists")
    args = parser.parse_args()

    # Output dir
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = _PROJECT_ROOT / "experiments" / "research" / "slippage_sensitivity" / ts
    output_dir.mkdir(parents=True, exist_ok=True)
    signals_cache = output_dir / "signals.cache"

    variants = _build_variants()
    print(f"Variants: {len(variants)} ({len(STRATEGIES)} strategies × {len(SLIPPAGE_LEVELS)} slippages)")
    print(f"Output:   {output_dir}/")
    print(f"Cache:    {signals_cache}")

    # Pre-load training data
    print("\n[Preload] Loading all training data...")
    t0 = time.time()
    preloaded = preload_training_data(args.end_date)
    print(f"  Done in {time.time() - t0:.1f}s")

    # Create runner
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
        signals_cache_path=None if args.no_cache else signals_cache,
    )
    total_time = time.time() - t_start

    # Save per-variant details
    for vr in results.variants:
        var_dir = output_dir / vr.name
        var_dir.mkdir(parents=True, exist_ok=True)
        if not vr.backtest_result.daily.empty:
            vr.backtest_result.daily.to_csv(var_dir / "daily_summary.csv", index=False)
        if not vr.backtest_result.trades.empty:
            vr.backtest_result.trades.to_csv(var_dir / "trades.csv", index=False)
        clean_metrics = {k: v for k, v in vr.metrics.items() if k != "details"}
        (var_dir / "metrics.json").write_text(
            json.dumps(clean_metrics, indent=2, default=str, ensure_ascii=False),
        )

    # Print + save comparison CSV
    comparison = print_net_table(results)
    csv_path = output_dir / "strategy_comparison_net.csv"
    comparison.to_csv(csv_path, index=False)
    print(f"\n→ {csv_path}")

    # Summary
    summary = {
        "experiment": "slippage_sensitivity",
        "run_at": datetime.now().isoformat(),
        "total_wall_time_seconds": total_time,
        "n_windows": len(results.windows),
        "n_variants": len(results.variants),
        "slippage_levels": SLIPPAGE_LEVELS,
        "strategies": list(STRATEGIES.keys()),
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str, ensure_ascii=False),
    )

    print(f"\nTotal time: {total_time:.0f}s ({total_time / 60:.1f} min)")


if __name__ == "__main__":
    main()
