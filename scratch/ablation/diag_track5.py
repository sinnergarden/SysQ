#!/usr/bin/env python3
"""Track 5 — Market Regime Diagnosis (2x2 coarse table).

Regime defined at each weekly rebalance snapshot from strictly-trailing info:
  market: CSI800 trailing 120 trading-day return > 0 (up) / <= 0 (down)
  breadth: fraction of scored universe with positive trailing 20d return,
           split at its full-sample median (good / bad).

Each cell reports forward outcomes (60d, aligned with model horizon):
  n snapshots, E1 NAV forward return, CSI800 forward return, active return,
  E1 forward MaxDD, Top5 forward excess, RankIC(60d).

Regime only; no gate parameters.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from diag_common import (
    EXEC_ROOT,
    load_benchmark,
    load_close_matrix,
    load_nav,
    load_score_panel,
    weekly_snapshots,
)

H = 60  # forward horizon (trading rows), aligned with the model's 60d label


def _pct(x: float | None, nd: int = 1) -> str:
    if x is None or not math.isfinite(x):
        return "—"
    return f"{x * 100:+.1f}%"


def _max_dd_series(vals: np.ndarray) -> float:
    if len(vals) == 0:
        return np.nan
    peak = vals[0]
    dd = 0.0
    for v in vals:
        if v > peak:
            peak = v
        dd = min(dd, v / peak - 1.0)
    return float(dd)


def main() -> int:
    e1 = EXEC_ROOT / "E1_rank_exit"
    cm = load_close_matrix()
    sp = load_score_panel()
    bench = load_benchmark("000906.SH")
    nav = load_nav(e1)
    snap = weekly_snapshots(e1, cm, sp, bench)

    idx = cm.index
    nav_al = nav.reindex(idx)  # aligns E1 NAV to close-matrix calendar rows
    bench_al = bench.reindex(idx)

    breadth_med = float(snap["breadth_20d"].median())
    rows = []
    for t, r in snap.iterrows():
        pos = idx.get_loc(t)
        j = pos + H
        row = {
            "date": t,
            "market_up": bool(r["bench_120d_ret"] > 0) if pd.notna(r["bench_120d_ret"]) else None,
            "breadth_good": bool(r["breadth_20d"] > breadth_med) if pd.notna(r["breadth_20d"]) else None,
        }
        if j < len(idx):
            n0, n1 = nav_al.iloc[pos], nav_al.iloc[j]
            b0, b1 = bench_al.iloc[pos], bench_al.iloc[j]
            if pd.notna(n0) and pd.notna(n1) and n0 > 0:
                row["e1_fwd_ret"] = float(n1 / n0 - 1.0)
                row["e1_fwd_maxdd"] = _max_dd_series(nav_al.iloc[pos:j + 1].to_numpy())
            if pd.notna(b0) and pd.notna(b1) and b0 > 0:
                row["bench_fwd_ret"] = float(b1 / b0 - 1.0)
            if "e1_fwd_ret" in row and "bench_fwd_ret" in row:
                row["active_fwd"] = (1 + row["e1_fwd_ret"]) / (1 + row["bench_fwd_ret"]) - 1
        row["top5_excess_60"] = float(r["top5_excess_60"]) if pd.notna(r["top5_excess_60"]) else None
        row["rankic_60"] = float(r["rankic_60"]) if pd.notna(r["rankic_60"]) else None
        rows.append(row)

    df = pd.DataFrame(rows)
    df = df.dropna(subset=["market_up", "breadth_good"])

    cells = {}
    print("=" * 104)
    print("Track 5 — Market Regime Diagnosis (2x2; forward 60d outcomes)")
    print("=" * 104)
    print(f"breadth threshold = median {breadth_med:.3f}; "
          f"market = CSI800 trailing 120d > 0")
    hdr = "cell".ljust(22) + "n  E1_ret  bench  active  E1MaxDD  Top5exc  RankIC"
    print(hdr)

    for mu in (True, False):
        for bg in (True, False):
            sub = df[(df["market_up"] == mu) & (df["breadth_good"] == bg)]
            label = f"up={int(mu)} brd={int(bg)}"
            n = len(sub)
            c = {
                "n": int(n),
                "e1_fwd_ret": float(np.nanmedian(sub["e1_fwd_ret"])) if n else None,
                "bench_fwd_ret": float(np.nanmedian(sub["bench_fwd_ret"])) if n else None,
                "active_fwd": float(np.nanmedian(sub["active_fwd"])) if n else None,
                "e1_fwd_maxdd": float(np.nanmedian(sub["e1_fwd_maxdd"])) if n else None,
                "top5_excess_60": float(np.nanmedian(sub["top5_excess_60"])) if n else None,
                "rankic_60": float(np.nanmedian(sub["rankic_60"])) if n else None,
            }
            cells[label] = c
            print(
                f"{label:22s} {n:4d}  {_pct(c['e1_fwd_ret']):>7} {_pct(c['bench_fwd_ret']):>7} "
                f"{_pct(c['active_fwd']):>8} {_pct(c['e1_fwd_maxdd']):>8} "
                f"{_pct(c['top5_excess_60']):>8} {c['rankic_60']:+.3f}"
            )

    out = {"breadth_threshold": breadth_med, "horizon_days": H, "cells": cells}
    Path("/tmp/diag_track5.json").write_text(json.dumps(out, indent=1, default=float))
    print("\n-> /tmp/diag_track5.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
