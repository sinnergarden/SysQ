#!/usr/bin/env python3
"""Verify the re-run C2 backtest is PATH-IDENTICAL to C0 (S180_20d).

C2 is defined to hold the same set as C0 on every retrain day (confirmed-first
re-orders within the set but cannot change holdings).  This script compares the
two runs' executions day-by-day and reports the first divergence (if any) and
the full holdings trace.  A genuine confirmed-first counterfactual must show
zero divergence; any divergence means the construction is still leaking engine
path-dependence (tiebreak lottery / partial-fill).

Usage:
  python verify_c2_vs_c0.py            # full day-by-day comparison
  python verify_c2_vs_c0.py --summary  # just the verdict + first divergence
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

EXEC_ROOT = (
    "/home/liuming/.openclaw/workspace/SysQ-execution-ledger/data/research/ablation/"
    "execution_policy"
)


def trace_holdings(exec_csv: str) -> tuple[dict[str, set], list]:
    """Return {day: held_set} and the raw executions list."""
    e = pd.read_csv(exec_csv)
    e = e.sort_values(["trade_date", "sequence"])
    qty: dict[str, float] = {}
    held: dict[str, set] = {}
    for r in e.itertuples(index=False):
        d = str(r.trade_date)[:10]
        inst = r.instrument
        q = float(r.filled_qty)
        if r.side == "buy":
            qty[inst] = qty.get(inst, 0.0) + q
        else:
            qty[inst] = qty.get(inst, 0.0) - q
            if abs(qty[inst]) < 1e-9:
                qty.pop(inst, None)
        held[d] = {i for i, qq in qty.items() if qq > 0}
    return held, e.to_dict("records")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args()

    h0, _ = trace_holdings(f"{EXEC_ROOT}/S180_20d/executions.csv")
    h2, _ = trace_holdings(f"{EXEC_ROOT}/C2_confirmed/executions.csv")

    days = sorted(set(h0) | set(h2))
    div = []
    for d in days:
        if h0.get(d, set()) != h2.get(d, set()):
            div.append((d, h0.get(d, set()), h2.get(d, set())))

    print(f"C0 trade days: {len(h0)}   C2 trade days: {len(h2)}   union: {len(days)}")
    print(f"days where holdings differ: {len(div)}")
    if not div:
        print("VERDICT: C2 holdings == C0 holdings on every day  -> path-identical")
        return 0
    print("first divergence:", div[0][0])
    if not args.summary:
        for d, s0, s2 in div[:8]:
            print(f"  {d}:  C0={sorted(s0)}  C2={sorted(s2)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
