#!/usr/bin/env python3
"""Track 2 — Active-return Stability of E1 vs CSI800 / CSI300 / universe EW.

Rolling 60/120/250d compounded excess return: % windows > 0, median, P10,
worst, active drawdown of the cumulative excess curve, and the longest
continuous underperformance period.

Question: is alpha *often present* (not just high total return)?
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
    active_stats_from_nav,
    load_benchmark,
    load_close_matrix,
    load_nav,
    load_score_panel,
    universe_benchmark,
)

HORIZONS = (60, 120, 250)


def _pct(x: float | None, nd: int = 1) -> str:
    if x is None or not math.isfinite(x):
        return "—"
    return f"{x * 100:+.1f}%"


def _active_drawdown(active_ret: pd.Series) -> dict:
    """Max drawdown + duration of the cumulative excess curve."""
    cum = (1.0 + active_ret.fillna(0.0)).cumprod()
    peak = cum.cummax()
    dd = cum / peak - 1.0
    maxdd = float(dd.min())
    # longest active drawdown duration: longest streak below prior peak
    in_dd = dd < 0
    best = cur = 0
    for v in in_dd:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return {"active_maxdd": maxdd, "longest_drawdown_days": int(best)}


def _longest_underperformance(rolling_excess: pd.Series) -> dict:
    """Longest consecutive-window run with rolling excess < 0."""
    neg = (rolling_excess < 0).astype(int)
    best = cur = 0
    for v in neg:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return {"longest_underperform_windows": int(best)}


def analyze(df: pd.DataFrame) -> dict:
    out = {}
    ar = df["active_ret"]
    out["full"] = {
        "total_active_return": float((1.0 + ar.fillna(0.0)).prod() - 1.0),
        **_active_drawdown(ar),
        "daily_active_win_frac": float((ar > 0).mean()),
    }
    for w in HORIZONS:
        xs = []
        for i in range(w, len(df)):
            xs.append(float((1.0 + ar.iloc[i - w:i]).prod() - 1.0))
        x = np.array(xs)
        out[str(w)] = {
            "n_windows": int(len(x)),
            "pos_frac": float(np.mean(x > 0)),
            "median": float(np.median(x)),
            "p10": float(np.percentile(x, 10)),
            "worst": float(np.min(x)),
            "best": float(np.max(x)),
            **_longest_underperformance(pd.Series(x)),
        }
    return out


def main() -> int:
    e1 = EXEC_ROOT / "E1_rank_exit"
    nav = load_nav(e1)
    cm = load_close_matrix()
    sp = load_score_panel()

    benchs = {
        "CSI800": load_benchmark("000906.SH"),
        "CSI300": load_benchmark("000300.SH"),
        "universe_ew": universe_benchmark(cm, sp),
    }

    out = {}
    print("=" * 100)
    print("Track 2 — Active-return Stability (E1 pure score refresh)")
    print("=" * 100)
    for name, bench in benchs.items():
        df = active_stats_from_nav(nav, bench)
        out[name] = analyze(df)
        r = out[name]
        print(f"\n--- {name} ---")
        print(f"  total active return {_pct(r['full']['total_active_return'])} | "
              f"active MaxDD {_pct(r['full']['active_maxdd'])} | "
              f"longest active DD {r['full']['longest_drawdown_days']}d | "
              f"daily active win {r['full']['daily_active_win_frac']:.1%}")
        for w in HORIZONS:
            s = r[str(w)]
            print(
                f"  {w}d: n={s['n_windows']} pos={s['pos_frac']:.1%} "
                f"med={_pct(s['median'])} p10={_pct(s['p10'])} "
                f"worst={_pct(s['worst'])} best={_pct(s['best'])} | "
                f"longest underperform {s['longest_underperform_windows']} windows"
            )

    Path("/tmp/diag_track2.json").write_text(json.dumps(out, indent=1, default=float))
    print("\n-> /tmp/diag_track2.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
