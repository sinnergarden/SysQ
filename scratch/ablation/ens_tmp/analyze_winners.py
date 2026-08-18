#!/usr/bin/env python3
"""Task 5 — right-tail winner capture: single vs ens3 vs ens5.

For each phase, per trade_date (where labels are fully realised), take Top5 by
raw ranking for single(seed42)/ens3/ens5 and measure:

  - median 180d fwd label of the Top5 portfolio (higher = better tail capture)
  - expected right-tail winners in Top5: how many of the day's top-decile names
    made it into Top5 (0..5)
  - winner-hit rate: P(Top5 contains >=1 top-decile winner)
  - pairwise winner-overlap: of the names a selector picks AND are true winners,
    how many are also picked by the other selector (who keeps whom)

Right-tail winner := fwd_ret_180d_raw in the day's top decile of the cross-section.
Only fully-realised labels (label maturity: trade_date <= last-label-date - horizon).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/home/liuming/.openclaw/workspace/SysQ")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scratch/ablation"))

from build_ensemble_seeds import SEED_RAW_DIR  # noqa: E402
from qsys.label.store import LabelStore  # noqa: E402

SEEDS = [42, 7, 77, 123, 456]
PHASES = ["p0", "p5", "p10", "p15"]
TOPN = 5
LABEL_ID = "fwd_ret_180d_raw"
HORIZON_TD = 180


def load_phase(phase: str) -> pd.DataFrame:
    """All windows merged: [trade_date, instrument, raw_42..raw_456]."""
    n_win = 68 if phase != "p15" else 67
    parts = []
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
        parts.append(m)
    return pd.concat(parts, ignore_index=True)


def main() -> None:
    label_df = LabelStore().load_labels(LABEL_ID)
    label_df["trade_date"] = label_df["trade_date"].astype(str)
    label_df = label_df[["trade_date", "instrument", "label_value"]].dropna()

    rows = []
    for phase in PHASES:
        df = load_phase(phase)
        m = df.merge(label_df, on=["trade_date", "instrument"], how="inner")
        if m.empty:
            print(f"{phase}: no labels, skip")
            continue
        n_days = m["trade_date"].nunique()

        # right-tail winner per day = top decile by label_value
        m["winner"] = m.groupby("trade_date")["label_value"].transform(
            lambda s: s >= s.quantile(0.9))

        s42 = m.copy(); s42["rank"] = s42.groupby("trade_date")["raw_42"].rank(ascending=False)
        s42_t5 = s42[s42["rank"] <= TOPN]
        e3 = m.copy(); e3["mean"] = e3[[f"raw_{s}" for s in SEEDS[:3]]].mean(axis=1)
        e3["rank"] = e3.groupby("trade_date")["mean"].rank(ascending=False)
        e3_t5 = e3[e3["rank"] <= TOPN]
        e5 = m.copy(); e5["mean"] = e5[[f"raw_{s}" for s in SEEDS]].mean(axis=1)
        e5["rank"] = e5.groupby("trade_date")["mean"].rank(ascending=False)
        e5_t5 = e5[e5["rank"] <= TOPN]

        def stats(t5: pd.DataFrame) -> dict:
            med_label = t5["label_value"].median()
            wcount = t5.groupby("trade_date")["winner"].sum()  # 0..5 per day
            return {
                "med_label": float(med_label),
                "exp_winners": float(wcount.mean()),
                "hit_rate": float((wcount >= 1).mean()),
                "p2plus": float((wcount >= 2).mean()),
            }

        def winner_overlap(A: pd.DataFrame, B: pd.DataFrame) -> float:
            """Of winners in A's Top5, how many also in B's Top5 (per-day avg)."""
            a_w = A[A["winner"]][["trade_date", "instrument"]].drop_duplicates()
            b_w = B[B["winner"]][["trade_date", "instrument"]].drop_duplicates()
            b_sets = b_w.groupby("trade_date")["instrument"].agg(set).to_dict()
            fracs = []
            for day, g in a_w.groupby("trade_date"):
                bset = b_sets.get(day, set())
                fracs.append(len(set(g["instrument"]) & bset) / max(1, len(g)))
            return float(np.mean(fracs))

        st_s42, st_e3, st_e5 = stats(s42_t5), stats(e3_t5), stats(e5_t5)
        rows.append({
            "phase": phase,
            "days": n_days,
            "single_med_label": st_s42["med_label"],
            "ens3_med_label": st_e3["med_label"],
            "ens5_med_label": st_e5["med_label"],
            "single_expW": st_s42["exp_winners"],
            "ens3_expW": st_e3["exp_winners"],
            "ens5_expW": st_e5["exp_winners"],
            "single_hit": st_s42["hit_rate"],
            "ens3_hit": st_e3["hit_rate"],
            "ens5_hit": st_e5["hit_rate"],
            "single_2plus": st_s42["p2plus"],
            "ens5_2plus": st_e5["p2plus"],
            "winners_s42->ens5": winner_overlap(s42_t5, e5_t5),
            "winners_ens5->s42": winner_overlap(e5_t5, s42_t5),
        })

    df = pd.DataFrame(rows)
    print("### Task 5 — right-tail winner capture (180d fwd label, top-decile winners) ###")
    print(df.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print()
    print("med_label   : median 180d fwd ret of the Top5 portfolio (realised labels only)")
    print("expW        : expected #top-decile winners inside Top5 (0..5)")
    print("hit         : P(Top5 contains >=1 winner)")
    print("2plus       : P(Top5 contains >=2 winners)")
    print("winners_A->B: of the winners selector A picks, fraction B also picks")


if __name__ == "__main__":
    main()
