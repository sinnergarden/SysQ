#!/usr/bin/env python3
"""E1 vs rank_weight_top5 vs A5_all — three-way comparison.

Required metrics (per user):
  CAGR / MaxDD / Calmar / turnover / fees / avg exposure / yearly return /
  Top1 & Top5 actual PnL contribution / median holding / 41d+ winner perf.

Uses currency PnL (`episode_pnl`) for concentration, never realized_return
summed as a PnL proxy.

Usage (run from the MAIN repo cwd so qsys + data resolve):
  python scratch/ablation/analyze_e1.py \
    --e1 <execution_policy/E1_rank_exit> \
    --rank-top5 <rank_weight_top5 bt dir> \
    --a5 <execution_policy/A5_all> \
    --episodes-root /tmp/ablation_episodes \
    --out /tmp/e1_comparison.json
"""
from __future__ import annotations

import argparse
import json
import math
import statistics as st
from pathlib import Path

import pandas as pd

from analyze_layers import portfolio_metrics, episode_behavior


def _pct(x: float | None, nd: int = 1) -> str:
    if x is None or not math.isfinite(x):
        return "—"
    return f"{x * 100:+.{nd}f}%"


def _num(x: float | None, nd: int = 2) -> str:
    if x is None or not math.isfinite(x):
        return "—"
    return f"{x:.{nd}f}"


def fee_stats(run_dir: Path) -> dict:
    execs = pd.read_csv(run_dir / "executions.csv")
    return {
        "commission": float(execs["commission"].sum()),
        "stamp_duty": float(execs["tax"].sum()),
        "total_fee": float(execs["total_fee"].sum()),
        "n_fills": int(len(execs)),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--e1", required=True)
    ap.add_argument("--rank-top5", required=True)
    ap.add_argument("--a5", required=True)
    ap.add_argument("--episodes-root", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    e1_dir = Path(args.e1)
    rw_dir = Path(args.rank_top5)
    a5_dir = Path(args.a5)
    ep_root = Path(args.episodes_root)

    runs = {
        "E1_rank_exit": e1_dir,
        "rank_weight_top5": rw_dir,
        "A5_all": a5_dir,
    }

    out = {"metrics": {}, "episodes": {}, "fees": {}, "yearly": {}}
    for name, rd in runs.items():
        l1 = portfolio_metrics(rd)
        env = json.loads((ep_root / f"{name}.json").read_text())
        l2 = episode_behavior(env)
        out["metrics"][name] = l1
        out["episodes"][name] = l2
        out["fees"][name] = fee_stats(rd)
        out["yearly"][name] = l1["yearly"]

    Path(args.out).write_text(json.dumps(out, indent=1, ensure_ascii=False))

    # ---- human-readable table ----
    names = list(runs)
    rows = [
        ("Total return", lambda n: _pct(out["metrics"][n]["total_return"], 2)),
        ("CAGR", lambda n: _pct(out["metrics"][n]["cagr"], 2)),
        ("MaxDD", lambda n: _pct(out["metrics"][n]["maxdd"], 2)),
        ("Calmar", lambda n: _num(out["metrics"][n]["calmar"])),
        ("Avg gross exposure", lambda n: _pct(out["metrics"][n]["avg_exposure"])),
        ("Turnover (M)", lambda n: f"{out['metrics'][n]['turnover'] / 1e6:.1f}"),
        ("Commission (k)", lambda n: f"{out['fees'][n]['commission'] / 1e3:.0f}"),
        ("Stamp duty (k)", lambda n: f"{out['fees'][n]['stamp_duty'] / 1e3:.0f}"),
        ("Total fees (k)", lambda n: f"{out['fees'][n]['total_fee'] / 1e3:.0f}"),
        ("Fills", lambda n: str(out["metrics"][n]["fills"])),
        ("Median holding (d)", lambda n: _num(out["episodes"][n]["median_holding"], 1)),
        ("Avg holding (d)", lambda n: _num(out["episodes"][n]["avg_holding"], 1)),
        ("Closed episodes", lambda n: str(out["episodes"][n]["n_closed"])),
        ("Net realized PnL (M)", lambda n: f"{out['episodes'][n]['total_pnl'] / 1e6:+.1f}"),
        ("Top1 PnL share", lambda n: _pct(out["episodes"][n]["top1_share"])),
        ("Top5 PnL share", lambda n: _pct(out["episodes"][n]["top5_share"])),
        ("Top1 PnL (M)", lambda n: _topN(out, n, 1)),
        ("Top5 PnL (M)", lambda n: _topN(out, n, 5)),
        ("41d+ survivors", lambda n: str(out["episodes"][n]["survivor_41d_count"])),
        ("41d+ median ret", lambda n: _pct(out["episodes"][n]["survivor_41d_median"])),
    ]
    print("\n" + "=" * 100)
    print("E1 (pure score refresh) vs rank_weight_top5 vs A5 (full posterior)")
    print("=" * 100)
    hdr = "metric".ljust(26) + "".join(n.ljust(22) for n in names)
    print(hdr)
    print("-" * 100)
    for label, fn in rows:
        print(label.ljust(26) + "".join(str(fn(n)).ljust(22) for n in names))

    print("\nYearly return:")
    for n in names:
        y = out["yearly"][n]
        yrs = sorted(y.keys())
        print("  " + n.ljust(18) + "  ".join(f"{yr}:{_pct(y[yr]['return'],0)}" for yr in yrs))

    print(f"\n-> {args.out}")
    return 0


def _topN(out: dict, name: str, k: int) -> str:
    """Currency PnL of the top-k episodes (from episode_pnl)."""
    env = out["episodes"][name]
    # Recover sorted pnl list is not stored; recompute cheaply from stored fields.
    total = env["total_pnl"]
    share = env["top1_share"] if k == 1 else env["top5_share"]
    if total == 0 or share is None:
        return "—"
    return f"{total * share / 1e6:+.1f}"


if __name__ == "__main__":
    raise SystemExit(main())
