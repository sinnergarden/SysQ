#!/usr/bin/env python3
"""Track 4 — Model Confidence Diagnostics.

For each weekly rebalance snapshot compute score-conviction metrics:
  top5_mean_score, top5_min_score, score_5_minus_6 (rank5-rank6 gap),
  top5_mean_minus_universe_median, cross_section_score_std.
Relate each metric to future 20/60/180d Top5 excess return by bucketing
snapshots into LOW / MED / HIGH based on the STRICTLY-PRIOR rolling
distribution of that metric (snapshots before the current one only; quantiles
q33/q66; min 26 prior snapshots warm-up).  No best-cutoff search.

Core question: when the model has no strong conviction, is Top5 basically
without alpha?
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

METRICS = [
    "top5_mean_score",
    "top5_min_score",
    "score_5_minus_6",
    "top5_mean_minus_universe_median",
    "cross_section_score_std",
]
HORIZONS = (20, 60, 180)
WARMUP = 26  # ~6 months of weekly snapshots


def _pct(x: float | None, nd: int = 1) -> str:
    if x is None or not math.isfinite(x):
        return "—"
    return f"{x * 100:+.1f}%"


def bucket_rows(snap: pd.DataFrame) -> pd.DataFrame:
    """Add low/med/high bucket columns per metric (strictly-prior rolling)."""
    out = snap.copy()
    for m in METRICS:
        labels = []
        prior = []
        for v in snap[m]:
            if len(prior) >= WARMUP and v is not None and math.isfinite(v):
                q33 = float(np.quantile([p for p in prior if p is not None], 0.33))
                q66 = float(np.quantile([p for p in prior if p is not None], 0.66))
                if v < q33:
                    labels.append("LOW")
                elif v > q66:
                    labels.append("HIGH")
                else:
                    labels.append("MED")
            else:
                labels.append(None)
            if v is not None and math.isfinite(v):
                prior.append(v)
        out[f"bucket_{m}"] = labels
    return out


def main() -> int:
    e1 = EXEC_ROOT / "E1_rank_exit"
    cm = load_close_matrix()
    sp = load_score_panel()
    bench = load_benchmark("000906.SH")
    snap = weekly_snapshots(e1, cm, sp, bench)
    s = bucket_rows(snap)

    out = {"warmup": WARMUP, "n_snapshots": int(len(s)), "metrics": {}}
    print("=" * 100)
    print("Track 4 — Model Confidence Diagnostics (prior-rolling buckets)")
    print("=" * 100)

    for m in METRICS:
        out["metrics"][m] = {"buckets": {}}
        print(f"\n--- {m} ---")
        for b in ("LOW", "MED", "HIGH"):
            sub = s[s[f"bucket_{m}"] == b]
            row = {"n": int(len(sub))}
            print(f"  {b}: n={len(sub)}", end="")
            for h in HORIZONS:
                xs = [float(x) for x in sub[f"top5_excess_{h}"] if x is not None and math.isfinite(x)]
                if xs:
                    row[str(h)] = {
                        "median": float(np.median(xs)),
                        "mean": float(np.mean(xs)),
                        "pos_frac": float(np.mean(np.array(xs) > 0)),
                        "n": len(xs),
                    }
                    print(f" | {h}d excess med {_pct(row[str(h)]['median'])} "
                          f"pos {row[str(h)]['pos_frac']:.0%}", end="")
                else:
                    row[str(h)] = None
            print()
            out["metrics"][m]["buckets"][b] = row

    Path("/tmp/diag_track4.json").write_text(json.dumps(out, indent=1, default=float))
    print("\n-> /tmp/diag_track4.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
