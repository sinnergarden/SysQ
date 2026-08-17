#!/usr/bin/env python3
"""Track 3 — Signal Edge by Year (weekly rebalance snapshots only).

Per weekly rebalance date (E1 refresh calendar, 275 snapshots): RankIC 20/60/180d
(Spearman score vs forward return), Top5 equal-weight forward return, universe
equal-weight forward return, Top5 excess.  Aggregated by year as median / q10 /
q90 / n.  No iid p-value claims on overlapping horizons — distributions only.

Preferred horizons are 60/180d (aligned with the model's label horizons);
20d is shown for completeness.
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
    load_score_panel,
    weekly_snapshots,
)

HORIZONS = (20, 60, 180)


def _pct(x: float | None, nd: int = 1) -> str:
    if x is None or not math.isfinite(x):
        return "—"
    return f"{x * 100:+.1f}%"


def _f(x: float | None, fmt: str) -> str:
    if x is None or not math.isfinite(x):
        return "—"
    return f"{x:{fmt}}"


def _year_rows(snap: pd.DataFrame, col: str, year: int) -> list[float]:
    seg = snap[snap["year"] == year][col]
    return [float(x) for x in seg if x is not None and math.isfinite(x)]


def _agg(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0, "median": None, "q10": None, "q90": None, "mean": None, "pos_frac": None}
    a = np.array(xs)
    return {
        "n": int(len(a)),
        "median": float(np.median(a)),
        "q10": float(np.percentile(a, 10)),
        "q90": float(np.percentile(a, 90)),
        "mean": float(np.mean(a)),
        "pos_frac": float(np.mean(a > 0)),
    }


def main() -> int:
    e1 = EXEC_ROOT / "E1_rank_exit"
    cm = load_close_matrix()
    sp = load_score_panel()
    bench = load_benchmark("000906.SH")
    snap = weekly_snapshots(e1, cm, sp, bench)

    out = {"n_snapshots": int(len(snap)), "by_year": {}}
    print("=" * 100)
    print("Track 3 — Signal Edge by Year (weekly snapshots)")
    print("=" * 100)

    for year in sorted(snap["year"].unique()):
        y = {}
        print(f"\n--- {year} ---")
        for h in HORIZONS:
            ric = _agg(_year_rows(snap, f"rankic_{h}", year))
            t5 = _agg(_year_rows(snap, f"fwd_top5_{h}", year))
            uv = _agg(_year_rows(snap, f"fwd_univ_{h}", year))
            ex = _agg(_year_rows(snap, f"top5_excess_{h}", year))
            y[str(h)] = {"rankic": ric, "top5": t5, "universe": uv, "excess": ex}
            print(
                f"  {h}d  RankIC med {_f(ric['median'], '+.3f')} (n={ric['n']}) | "
                f"Top5 med {_pct(t5['median'])} | univ med {_pct(uv['median'])} | "
                f"excess med {_pct(ex['median'])} (pos {_f(ex['pos_frac'], '.0%')})"
            )
        out["by_year"][str(year)] = y

    # Full-sample aggregate (all snapshots).
    print("\n--- FULL (all 275 snapshots) ---")
    for h in HORIZONS:
        ric = _agg(_all_rows(snap, f"rankic_{h}"))
        t5 = _agg(_all_rows(snap, f"fwd_top5_{h}"))
        ex = _agg(_all_rows(snap, f"top5_excess_{h}"))
        print(
            f"  {h}d  RankIC med {ric['median']:+.3f} | "
            f"Top5 med {_pct(t5['median'])} | "
            f"excess med {_pct(ex['median'])} (pos {ex['pos_frac']:.0%})"
        )
    out["full"] = {str(h): {
        "rankic": _agg(_all_rows(snap, f"rankic_{h}")),
        "top5": _agg(_all_rows(snap, f"fwd_top5_{h}")),
        "excess": _agg(_all_rows(snap, f"top5_excess_{h}")),
    } for h in HORIZONS}

    Path("/tmp/diag_track3.json").write_text(json.dumps(out, indent=1, default=float))
    print("\n-> /tmp/diag_track3.json")
    return 0


def _all_rows(snap: pd.DataFrame, col: str) -> list[float]:
    return [float(x) for x in snap[col] if x is not None and math.isfinite(x)]


if __name__ == "__main__":
    raise SystemExit(main())
