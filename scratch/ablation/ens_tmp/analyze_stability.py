#!/usr/bin/env python3
"""Task 4 — realization-variance / selection stability analysis.

Question: how much does single-model realization lottery affect the raw-ranking
Top5 selection within a training window, and does ens3/ens5 stabilise it?

Measures (per phase, from the seed raw bank — ranking by raw within a day is
order-equivalent to the zscore_no_clip ranking the pipeline uses):

  A. prediction-level lottery : pairwise Spearman rho between seeds on the same
     training window (per window, aggregated).
  B. Top5-level lottery       : per day, overlap of Top5(single seed i) vs
     Top5(single seed j).  High overlap = selection robust to seed choice.
  C. ensemble stabilisation   : per day, overlap of Top5(ens3/ens5 mean-raw)
     vs Top5(single seed42) and vs Top5(mean of all single seeds).

All raw: no clipped zscore ranking anywhere.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ROOT = Path("/home/liuming/.openclaw/workspace/SysQ")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scratch/ablation"))

from build_ensemble_seeds import SEED_RAW_DIR  # noqa: E402

SEEDS = [42, 7, 77, 123, 456]
PHASES = ["p0", "p5", "p10", "p15"]
TOPN = 5


def load_phase(phase: str) -> list[pd.DataFrame]:
    """One df per window: [trade_date, instrument] + raw_{sd} per seed."""
    n_win = 68 if phase != "p15" else 67
    out = []
    for w in range(n_win):
        frames = []
        for sd in SEEDS:
            d = pd.read_parquet(SEED_RAW_DIR / phase / f"seed{sd}" / f"w{w:04d}.parquet")
            d["trade_date"] = d["trade_date"].astype(str)
            d["instrument"] = d["instrument"].astype(str)
            frames.append(d[["trade_date", "instrument", "raw"]].rename(columns={"raw": f"raw_{sd}"}))
        m = frames[0]
        for f in frames[1:]:
            m = m.merge(f, on=["trade_date", "instrument"])
        out.append(m)
    return out


def top5(df: pd.DataFrame, col: str) -> set[str]:
    return set(df.nlargest(TOPN, col)["instrument"])


def main() -> None:
    rows = []
    for phase in PHASES:
        wins = load_phase(phase)
        pair_rhos, seed_overlaps = [], []
        ens3_vs_s42, ens5_vs_s42, ens5_vs_mean5 = [], [], []
        seed_names = [f"raw_{s}" for s in SEEDS]
        for m in wins:
            # A: pairwise seed rho (prediction-level lottery)
            r = []
            for i in range(len(SEEDS)):
                for j in range(i + 1, len(SEEDS)):
                    c = m[[seed_names[i], seed_names[j]]].dropna()
                    if len(c) > 100:
                        rho = spearmanr(c[seed_names[i]], c[seed_names[j]]).statistic
                        r.append(rho)
            if r:
                pair_rhos.append(np.median(r))

            # B: Top5 seed-vs-seed overlap (selection lottery)
            tops = {s: top5(m, f"raw_{s}") for s in SEEDS}
            o = [len(tops[a] & tops[b]) / TOPN
                 for a in SEEDS for b in SEEDS if a < b]
            seed_overlaps.append(np.mean(o))

            # C: ensemble stabilisation
            m = m.copy()
            m["ens3_raw"] = m[[f"raw_{s}" for s in SEEDS[:3]]].mean(axis=1)
            m["ens5_raw"] = m[[f"raw_{s}" for s in SEEDS]].mean(axis=1)
            mean5 = m[seed_names].mean(axis=1)
            m["mean5_raw"] = mean5
            t_ens3, t_ens5, t_m5 = top5(m, "ens3_raw"), top5(m, "ens5_raw"), top5(m, "mean5_raw")
            t_s42 = tops[42]
            ens3_vs_s42.append(len(t_ens3 & t_s42) / TOPN)
            ens5_vs_s42.append(len(t_ens5 & t_s42) / TOPN)
            ens5_vs_mean5.append(len(t_ens5 & t_m5) / TOPN)

        rows.append({
            "phase": phase,
            "med_seed_pair_rho": float(np.median(pair_rhos)),
            "seed_rho_p10": float(np.percentile(pair_rhos, 10)),
            "med_Top5_seed_overlap": float(np.median(seed_overlaps)),
            "Top5_seed_overlap_p10": float(np.percentile(seed_overlaps, 10)),
            "med_Top5_ens3_vs_s42": float(np.median(ens3_vs_s42)),
            "med_Top5_ens5_vs_s42": float(np.median(ens5_vs_s42)),
            "med_Top5_ens5_vs_mean5": float(np.median(ens5_vs_mean5)),
        })

    df = pd.DataFrame(rows)
    print("### Task 4 — realization variance / selection stability (raw ranking) ###")
    print(df.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print()
    print("A. med_seed_pair_rho        : within-window pairwise seed Spearman "
          "(low = high single-model lottery)")
    print("   seed_rho_p10             : worst-window value (10th pct)")
    print("B. med_Top5_seed_overlap    : fraction of Top5 shared between two "
          "single seeds, same window (0.6 = 3/5 names)")
    print("C. med_Top5_ens3_vs_s42     : how much ens3 Top5 keeps of single-seed42 Top5")
    print("   med_Top5_ens5_vs_mean5   : ens5 vs 'consensus' Top5 (mean of all 5)")


if __name__ == "__main__":
    main()
