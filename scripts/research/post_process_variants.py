#!/usr/bin/env python3
"""Post-process strategy variant results to add rank_bucket_attribution + yearly breakdown.

Usage
-----
    cd /home/liuming/.openclaw/workspace/SysQ
    python scripts/research/post_process_variants.py experiments/research/strategy_variants/20260527_230733/

This reads the existing output directory and creates two new files:
    rank_bucket_attribution.csv    — proportion of buys/weight per rank decile
    period_breakdown_annual.csv    — year-by-year breakdown (more granular)
"""

from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

# Ensure project root is on path (needed for loading cache that references qsys objects)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import numpy as np
import pandas as pd


def load_signals(cache_path: Path) -> dict:
    """Load signals from cache file."""
    with open(cache_path, "rb") as f:
        cache = pickle.load(f)
    return cache["all_signals"]


def build_decile_map(signals: dict) -> dict[tuple[str, str], int]:
    """Map each (date, inst) → decile (1=best, 10=worst) based on blended z-score.

    Decile 1 = top 10% blended scores (strongest buy signals)
    Decile 10 = bottom 10% (weakest)
    """
    from collections import defaultdict

    date_insts: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for (d, inst), (z5, z20, b) in signals.items():
        if not np.isnan(b):
            date_insts[d].append((inst, b))

    decile_map: dict[tuple[str, str], int] = {}
    for d, items in date_insts.items():
        items.sort(key=lambda x: x[1], reverse=True)
        n = len(items)
        for rank, (inst, _) in enumerate(items):
            decile = min(rank * 10 // n, 9) + 1  # 1-indexed, 1=best
            decile_map[(d, inst)] = decile

    return decile_map


def build_rank_bucket_attribution(
    output_dir: Path,
    decile_map: dict[tuple[str, str], int],
) -> pd.DataFrame:
    """For each variant, count what proportion of buys fall in each decile."""
    rows = []

    variant_dirs = sorted([
        d for d in output_dir.iterdir()
        if d.is_dir() and d.name.startswith("alpha_v1")
    ])

    for var_dir in variant_dirs:
        variant_name = var_dir.name

        # Parse slippage from name
        slip = 0.0
        if "_s" in variant_name:
            parts = variant_name.rsplit("_s", 1)
            slip_str = parts[1] if len(parts) > 1 else "0"
            try:
                slip = float(slip_str.replace("e-0", "e-")) if slip_str != "0" else 0.0
            except ValueError:
                slip = 0.0

        trades_path = var_dir / "trades.csv"
        daily_path = var_dir / "daily_summary.csv"
        if not trades_path.exists():
            continue

        trades = pd.read_csv(trades_path)
        buys = trades[trades["side"] == "buy"].copy()
        if buys.empty:
            continue

        # ── Attribution by buy count ──
        decile_counts: dict[int, int] = {d: 0 for d in range(1, 11)}
        mapped = 0
        for _, row in buys.iterrows():
            key = (row["date"], str(row["symbol"]))
            dec = decile_map.get(key)
            if dec is not None:
                decile_counts[dec] = decile_counts.get(dec, 0) + 1
                mapped += 1

        if mapped == 0:
            continue

        total = len(buys)
        unmapped = total - mapped

        row = {
            "strategy_id": variant_name,
            "slippage": slip,
        }
        for d in range(1, 11):
            row[f"decile_{d}_pct"] = decile_counts[d] / total
        row["unmapped_pct"] = unmapped / total
        row["total_buys"] = total
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows)


def build_yearly_breakdown(output_dir: Path) -> pd.DataFrame:
    """Build year-by-year breakdown for each variant."""
    # Year boundaries
    years = list(range(2015, 2027))
    periods = []
    for y in years:
        if y == 2026:
            periods.append((str(y), f"{y}-01-01", "2026-05-22"))
        else:
            periods.append((str(y), f"{y}-01-01", f"{y}-12-31"))

    rows = []
    variant_dirs = sorted([
        d for d in output_dir.iterdir()
        if d.is_dir() and d.name.startswith("alpha_v1")
    ])

    for var_dir in variant_dirs:
        variant_name = var_dir.name
        slip = 0.0
        if "_s" in variant_name:
            parts = variant_name.rsplit("_s", 1)
            slip_str = parts[1] if len(parts) > 1 else "0"
            try:
                slip = float(slip_str.replace("e-0", "e-")) if slip_str != "0" else 0.0
            except ValueError:
                slip = 0.0

        daily_path = var_dir / "daily_summary.csv"
        if not daily_path.exists():
            continue
        daily = pd.read_csv(daily_path)
        if daily.empty:
            continue

        # Rename columns
        col_map = {}
        for c in daily.columns:
            if c == "equity":
                col_map[c] = "total_value_after"
            elif c == "date":
                col_map[c] = "trade_date"
        if col_map:
            daily = daily.rename(columns=col_map)

        daily["trade_date"] = pd.to_datetime(daily["trade_date"])

        # Read metrics for full-period data (turnover, holding days, win rate)
        metrics_path = var_dir / "metrics.json"
        full_metrics = {}
        if metrics_path.exists():
            with open(metrics_path) as f:
                full_metrics = json.load(f)

        for period_name, p_start, p_end in periods:
            mask = (daily["trade_date"] >= p_start) & (daily["trade_date"] <= p_end)
            sub = daily[mask].sort_values("trade_date").reset_index(drop=True)
            if len(sub) < 20:
                continue

            equity = sub["total_value_after"].astype(float)
            rets = equity.pct_change().dropna()

            # Annualized return
            tr = float(equity.iloc[-1] / equity.iloc[0] - 1)
            n = len(rets)
            ann_ret = (1 + tr) ** (252 / max(n, 1)) - 1

            # Sharpe
            sp = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 1e-12 else 0.0

            # MDD
            mdd = float((equity / equity.cummax() - 1).min())

            # Calmar
            cal = ann_ret / abs(mdd) if abs(mdd) > 1e-12 else 0.0

            # Volatility
            vol = float(rets.std() * np.sqrt(252))

            # Turnover (from daily if available)
            to = 0.0
            if "turnover" in sub.columns:
                total_t = sub["turnover"].astype(float).sum()
                avg_val = float(equity.mean())
                if avg_val > 0:
                    to = total_t / avg_val * 252 / max(n, 1)

            # Drawdown details
            cummax_val = equity.cummax()
            dd_series = equity / cummax_val - 1
            in_dd = (dd_series < -0.05).sum()  # days in drawdown >5%
            dd_days_pct = in_dd / max(n, 1)

            row = {
                "strategy_id": variant_name,
                "slippage": slip,
                "year": period_name,
                "annual_return": ann_ret,
                "sharpe": sp,
                "max_drawdown": mdd,
                "calmar": cal,
                "volatility": vol,
                "turnover": to,
                "n_days": n,
                "dd_days_pct": dd_days_pct,
            }

            # Add full-period reference metrics
            if full_metrics:
                for k in ["annual_turnover", "avg_holding_days", "win_rate"]:
                    if k in full_metrics:
                        row[k] = full_metrics[k]

            rows.append(row)

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows)


def main():
    if len(sys.argv) < 2:
        print("Usage: post_process_variants.py <output_dir>")
        sys.exit(1)

    output_dir = Path(sys.argv[1])
    if not output_dir.exists():
        print(f"Output directory not found: {output_dir}")
        sys.exit(1)

    cache_path = output_dir / "signals.cache"
    if not cache_path.exists():
        print(f"signals.cache not found in {output_dir}, cannot compute rank attribution")
        # We can still do yearly breakdown
        signals = None
    else:
        print("[Load] signals.cache ...")
        signals = load_signals(cache_path)
        print(f"  {len(signals)} entries loaded")

    # ── Rank bucket attribution ──
    if signals:
        print("[Build] rank decile map ...")
        decile_map = build_decile_map(signals)
        print(f"  {len(decile_map)} (date, inst) → decile mapped")

        print("[Build] rank_bucket_attribution ...")
        rank_df = build_rank_bucket_attribution(output_dir, decile_map)
        if not rank_df.empty:
            rank_path = output_dir / "rank_bucket_attribution.csv"
            rank_df.to_csv(rank_path, index=False)
            print(f"  → {rank_path}")

            # Print summary
            print("\n  Rank Bucket Attribution (buys % per decile, 1=best):")
            print(f"  {'strategy_id':<28} {'D1':>6} {'D2':>6} {'D3':>6} {'D4':>6} {'D5':>6} {'D6':>6} {'D7':>6} {'D8':>6} {'D9':>6} {'D10':>6} {'Unm':>6}")
            print("  " + "-" * 88)
            for _, r in rank_df.iterrows():
                if r["slippage"] != 0.0:
                    continue
                d_cols = [f"{r[f'decile_{d}_pct']:.1%}" for d in range(1, 11)]
                print(f"  {r['strategy_id']:<28} {' '.join(d_cols)} {r['unmapped_pct']:>5.1%}")
        else:
            print("  [WARN] No rank attribution data generated")

    # ── Yearly breakdown ──
    print("\n[Build] yearly breakdown ...")
    yearly_df = build_yearly_breakdown(output_dir)
    if not yearly_df.empty:
        yearly_path = output_dir / "period_breakdown_annual.csv"
        yearly_df.to_csv(yearly_path, index=False)
        print(f"  → {yearly_path}")

        # Print summary
        print("\n  Yearly Breakdown (s0 only):")
        strategies = sorted(yearly_df[yearly_df["slippage"] == 0.0]["strategy_id"].unique())
        years = sorted(yearly_df["year"].unique())

        header = f"  {'Year':<6}" + "".join(f"{s[:12]:>13}" for s in strategies)
        print(header)
        print("  " + "-" * len(header))
        for y in years:
            vals = []
            for s in strategies:
                mask = (yearly_df["strategy_id"] == s) & (yearly_df["year"] == y) & (yearly_df["slippage"] == 0.0)
                sub = yearly_df[mask]
                if not sub.empty:
                    vals.append(f"{sub.iloc[0]['annual_return']:>11.2%}")
                else:
                    vals.append(f"{'':>12}")
            print(f"  {y:<6}" + "".join(vals))
    else:
        print("  [WARN] No yearly breakdown data generated")

    print("\nDone.")


if __name__ == "__main__":
    main()
