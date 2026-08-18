#!/usr/bin/env python3
"""Task 5 (strict) — selection-alpha preservation on CORRECT baselines.

Per phase, per retrain-day cohort: Top5 by score for
  single = correct single (p0 stored==seed42; p5/p10/p15 rawrank_correct)
  ens3 / ens5 = mean-raw ensemble signals
Then forward 60/180d edgeA (Top5 − scored-universe EW) and edgeB (− CSI800),
plus right-tail bucket counts (@180). Reuses analyze_phase_cohorts panel logic.

Evidence that the ensemble keeps the selection alpha while taming the lottery.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/home/liuming/.openclaw/workspace/SysQ")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scratch/ablation"))

from analyze_phase_cohorts import (  # noqa: E402
    EXPERIMENT,
    HORIZONS,
    SIG_ID,
    load_shifted_predict_starts,
)
from diag_common import load_benchmark, load_close_matrix  # noqa: E402

OUT_DIR = ROOT / "scratch/ablation/ens_tmp/analysis"
SIGNS = ROOT / "data/research/signals" / SIG_ID


def run_parquet(phase: str, tag: str) -> Path:
    if tag == "single":
        rid = (f"{SIG_ID}__rr_{phase}__rawrank__{EXPERIMENT}" if phase == "p0"
               else f"{SIG_ID}__rr_{phase}__rawrank_correct__{EXPERIMENT}")
    else:
        rid = f"{SIG_ID}__rr_{phase}__{tag}__{EXPERIMENT}"
    return SIGNS / rid / "predictions.parquet"


def cohort_edge(fwd: pd.Series, top5: list[str], n_top_min: int = 3):
    f5 = fwd.reindex(top5).dropna()
    if len(f5) < n_top_min:
        return np.nan, np.nan
    univ = fwd.dropna()
    if len(univ) < 30:
        return np.nan, np.nan
    return float(f5.mean()), float(univ.mean())


def build_panel(phase: str, tag: str, cm: pd.DataFrame,
                bench_close: pd.Series, retrain_days: list[str]) -> pd.DataFrame:
    df = pd.read_parquet(run_parquet(phase, tag))
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.dropna(subset=["score"])
    idx = cm.index
    rows = []
    for t_str in retrain_days:
        t = pd.Timestamp(t_str)
        if t not in idx:
            continue
        pos = idx.get_loc(t)
        day = df[df["trade_date"] == t]
        if day.empty:
            continue
        sc = day.set_index("instrument")["score"]
        sc = sc[sc.notna()]
        if len(sc) < 30:
            continue
        top5 = sc.sort_values(ascending=False).head(5).index.tolist()
        row = {"trade_date": t, "year": int(t.year), "n_scored": len(sc), "top5": top5}
        for h in HORIZONS:
            j = pos + h
            if j >= len(idx):
                row[f"edgeA_{h}"], row[f"edgeB_{h}"] = np.nan, np.nan
                continue
            t0 = cm.iloc[pos]
            t1 = cm.iloc[j]
            fwd = (t1 / t0 - 1.0).reindex(sc.index)
            f5m, univm = cohort_edge(fwd, top5)
            b0, b1 = bench_close.iloc[pos], bench_close.iloc[j]
            bfwd = float(b1 / b0 - 1.0) if (np.isfinite(b0) and np.isfinite(b1)) else np.nan
            row[f"edgeA_{h}"] = (f5m - univm) * 100 if np.isfinite(f5m) else np.nan
            row[f"edgeB_{h}"] = (f5m - bfwd) * 100 if np.isfinite(f5m) and np.isfinite(bfwd) else np.nan
            if h == 180:
                f180 = fwd.reindex(top5).dropna()
                row["n_fwd180"] = len(f180)
                row["n_gt20"] = float((f180 > 0.20).sum())
                row["n_gt50"] = float((f180 > 0.50).sum())
                row["n_gt100"] = float((f180 > 1.00).sum())
        rows.append(row)
    return pd.DataFrame(rows)


def summarize(panel: pd.DataFrame, h: int, which: str) -> dict:
    col = f"{which}_{h}"
    s = panel[col].dropna()
    return {"n": len(s), "mean": s.mean(), "median": s.median(),
            "pos_rate": (s > 0).mean(), "q25": s.quantile(0.25), "q75": s.quantile(0.75),
            "worst": s.min(), "p90": s.quantile(0.90), "max": s.max()}


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--phases", default="p0,p5,p10,p15")
    ap.add_argument("--tags", default="single,ens3,ens5")
    args = ap.parse_args()
    phases = args.phases.split(",")
    tags = args.tags.split(",")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cm = load_close_matrix()
    bench = load_benchmark("000906.SH", window=False).reindex(cm.index)
    PHASE_SHIFT = {"p0": 0, "p5": 5, "p10": 10, "p15": 15}

    out = []
    for ph in phases:
        retrain_days = load_shifted_predict_starts(PHASE_SHIFT[ph])
        for tag in tags:
            panel = build_panel(ph, tag, cm, bench, retrain_days)
            out.append({"phase": ph, "tag": tag, "n_cohorts": len(panel),
                        "n_gt50_180": int(panel["n_gt50"].sum()),
                        "n_gt100_180": int(panel["n_gt100"].sum()),
                        "top5fwd180_med": panel["top5fwd_180"].median() if "top5fwd_180" in panel else np.nan})
            for h in (60, 180):
                for which in ("edgeA", "edgeB"):
                    s = summarize(panel, h, which)
                    out[-1][f"{which}{h}_med"] = s["median"]
                    out[-1][f"{which}{h}_pos"] = s["pos_rate"]
            panel.to_csv(OUT_DIR / f"cohort_panel_{ph}_{tag}.csv", index=False)

    df = pd.DataFrame(out)
    print("### Task 5 — selection alpha preservation (correct signals) ###")
    for ph in phases:
        print(f"\n-- {ph.upper()} --")
        sub = df[df.phase == ph].set_index("tag")
        print(sub.round(3).to_string())
    df.to_csv(OUT_DIR / "selection_table_correct.csv", index=False)
    print(f"\nsaved -> {OUT_DIR}/selection_table_correct.csv")


if __name__ == "__main__":
    main()
