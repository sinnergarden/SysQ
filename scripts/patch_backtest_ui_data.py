#!/usr/bin/env python3
"""Patch existing backtest output files with missing UI data columns.

Fixes:
  1. CSI300 benchmark equity curve (universe avg return)
  2. Drawdown column
  3. Trade IDs for case study linkage
  4. Weekly returns JSON (instead of monthly)
  5. Simplified run summary in report JSON
  6. Preserves zero_cost_total_assets
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore")

from qlib.data import D
from qsys.data.adapter import QlibAdapter

UNIVERSE_MAP = {
    "csi300": "csi300",
    "csi800": "csi800",
}

REPORT_DIR = Path("experiments/reports")
DAILY_DIR = Path("experiments")


def patch_backtest(universe: str):
    run_id = f"alpha_v1_{universe}_blend20_weekly_top20_buffer"
    daily_path = DAILY_DIR / f"backtest_result_{run_id}.csv"
    report_path = REPORT_DIR / f"backtest_{run_id}.json"
    weekly_path = DAILY_DIR / f"weekly_returns_{run_id}.json"
    trade_path = DAILY_DIR / f"trade_detail_{run_id}.json"

    if not daily_path.exists():
        print(f"  SKIP: {daily_path} not found")
        return

    print(f"\n=== Patching {universe} ===")
    df = pd.read_csv(daily_path)
    print(f"  Daily rows: {len(df)}")

    # Compute benchmark equity (equal-weighted universe avg return)
    adapter = QlibAdapter()
    adapter.init_qlib()
    qlib_universe = UNIVERSE_MAP[universe]
    init_cap = 10_000_000.0

    dates = df["date"].unique()
    print(f"  Computing benchmark for {len(dates)} dates...")

    benchmark_prices = {}
    for date_str in dates:
        try:
            features = adapter.get_features(qlib_universe, ["$close"], start_time=date_str, end_time=date_str)
            if features is not None and not features.empty:
                closes = features["$close"].dropna()
                if len(closes) > 0:
                    benchmark_prices[date_str] = float(closes.mean())
        except Exception:
            pass

    if benchmark_prices:
        sorted_dates = sorted(benchmark_prices.keys())
        base_price = benchmark_prices[sorted_dates[0]]
        if base_price > 0:
            benchmark_vals = {}
            for d in sorted_dates:
                benchmark_vals[d] = init_cap * benchmark_prices[d] / base_price

            df["benchmark_equity"] = np.nan
            for d, val in benchmark_vals.items():
                df.loc[df["date"] == d, "benchmark_equity"] = val
            # Forward fill any gaps
            df["benchmark_equity"] = df["benchmark_equity"].ffill()
            # Fill start
            df["benchmark_equity"] = df["benchmark_equity"].fillna(init_cap)
            print(f"  benchmark_equity: {len(benchmark_vals)} dates")
        else:
            print("  WARN: base_price is 0, skipping benchmark")
            df["benchmark_equity"] = init_cap
    else:
        print("  WARN: no benchmark data, using flat line")
        df["benchmark_equity"] = init_cap

    # Drawdown from equity
    equity = df["equity"].values
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    df["drawdown"] = dd
    print(f"  drawdown: min={dd.min():.2%}")

    # Ensure zero_cost_total_assets exists
    if "zero_cost_total_assets" not in df.columns and "zc_equity" in df.columns:
        df["zero_cost_total_assets"] = df["zc_equity"]
        print("  renamed zc_equity → zero_cost_total_assets")

    # Weekly returns
    df["date_dt"] = pd.to_datetime(df["date"])
    df["week"] = df["date_dt"].dt.to_period("W").astype(str)
    weekly_grp = df.groupby("week").agg(
        start_assets=("equity", "first"), end_assets=("equity", "last")
    )
    weekly_grp["return"] = weekly_grp["end_assets"] / weekly_grp["start_assets"] - 1
    weekly_returns = [
        {"week": idx, "return": round(float(row["return"]), 6)}
        for idx, row in weekly_grp.iterrows()
    ]
    with open(weekly_path, "w") as f:
        json.dump(weekly_returns, f, indent=2)
    pos_weeks = sum(1 for w in weekly_returns if w["return"] > 0)
    print(f"  weekly_returns: {len(weekly_returns)} weeks, {pos_weeks} positive")

    # Trade IDs for case study
    trade_dir = Path(f"experiments/alpha_v1_backtest_{universe}/trades")
    trade_detail = {"status": "available", "trades": []}
    if trade_dir.exists():
        for tf in sorted(trade_dir.glob("*.csv")):
            tdf = pd.read_csv(tf)
            if not tdf.empty:
                tdf = tdf.reset_index()
                tdf["trade_id"] = tdf.index + 1
                trade_detail["trades"].extend(
                    tdf.to_dict("records")
                )
    with open(trade_path, "w") as f:
        json.dump(trade_detail, f, indent=2)
    print(f"  trade_detail: {len(trade_detail['trades'])} trades")

    # Save patched CSV
    cols_to_drop = ["date_dt", "week"] if "date_dt" in df.columns and "week" in df.columns else []
    save_cols = [c for c in df.columns if c not in cols_to_drop]
    df[save_cols].to_csv(daily_path, index=False)
    print(f"  CSV saved ({len(save_cols)} columns)")

    # Update report JSON — simplify sections
    if report_path.exists():
        with open(report_path) as f:
            report = json.load(f)

        report["artifacts"]["weekly_returns"] = str(weekly_path)
        report["artifacts"]["trade_detail"] = str(trade_path)

        # Update or add simplified sections
        perf_idx = next((i for i, s in enumerate(report["sections"]) if s["name"] == "Performance"), None)
        if perf_idx is not None:
            m = report["sections"][perf_idx]["metrics"]
            report["sections"][perf_idx]["metrics"] = {
                "total_return": m.get("total_return", ""),
                "annual_return": m.get("annual_return", ""),
                "sharpe": m.get("sharpe", ""),
                "max_drawdown": m.get("max_drawdown", ""),
                "calmar": m.get("calmar", ""),
            }

        cost_idx = next((i for i, s in enumerate(report["sections"]) if s["name"] == "Cost Analysis" or s["name"] == "Cost & Turnover"), None)
        if cost_idx is not None:
            report["sections"][cost_idx]["name"] = "Cost Analysis"
            m = report["sections"][cost_idx]["metrics"]
            report["sections"][cost_idx]["metrics"] = {
                "total_fees": m.get("total_fees", ""),
                "annualized_turnover": m.get("annualized_turnover", ""),
            }

        win_idx = next((i for i, s in enumerate(report["sections"]) if s["name"] == "Rolling Windows"), None)
        if win_idx is not None:
            m = report["sections"][win_idx]["metrics"]
            report["sections"][win_idx]["metrics"] = {
                "window_count": m.get("window_count", ""),
                "window_win_rate": m.get("window_win_rate", ""),
                "mean_window_return": m.get("mean_window_return", ""),
            }

        weekly_idx = next((i for i, s in enumerate(report["sections"]) if "Monthly" in s["name"] or "Weekly" in s["name"]), None)
        week_section = {
            "name": "Weekly Returns",
            "status": "success",
            "message": "",
            "metrics": {
                "total_weeks": str(len(weekly_returns)),
                "positive_weeks": f"{pos_weeks}/{len(weekly_returns)}",
                "weekly_win_rate": f"{pos_weeks / max(len(weekly_returns), 1) * 100:.1f}%",
                "best_week": f"{max(w['return'] for w in weekly_returns) * 100:.2f}%" if weekly_returns else "",
                "worst_week": f"{min(w['return'] for w in weekly_returns) * 100:.2f}%" if weekly_returns else "",
            },
            "details": {},
        }
        if weekly_idx is not None:
            report["sections"][weekly_idx] = week_section
        else:
            report["sections"].append(week_section)

        # Remove verbose sections
        report["sections"] = [s for s in report["sections"] if s["name"] not in ("Signal Quality", "Details")]

        with open(report_path, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"  Report updated: {report['sections']}")

    print(f"  Done!")


if __name__ == "__main__":
    for u in ["csi300", "csi800"]:
        patch_backtest(u)
    print("\nAll done!")
