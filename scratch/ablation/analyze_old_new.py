#!/usr/bin/env python3
"""Phase 2 — BOTH / NEW_IN / DROPPED forward-excess analysis.

Consumes reinfer_retrain_days.parquet (score_old/score_new per instrument per
retrain day, produced by reinfer_old_new.py).  For every retrain day t:

  new Top5  = top-5 by score_new within that day's cross-section
  old Top5  = top-5 by score_old within the SAME cross-section
  BOTH      = old Top5 ∩ new Top5
  NEW_IN    = new Top5 − old Top5
  DROPPED   = old Top5 − new Top5

Forward returns @20/60/180d: close-to-close over strict trading rows from the
close matrix; null when the right-end row is missing (no stale fallback).
Excess = basket-EW minus same-day scored-universe EW (mean of forward returns
over the instruments scored that day that have a valid fwd).

Sections:
  B: basket forward excess + replacement_edge per horizon
  C: full-sample / yearly / model-version-cohort stats
  D: model-version stability (rank corr, overlaps, |rank_delta|)
  E: right-tail attribution (winners from BOTH vs NEW_IN)

Run from the MAIN SysQ cwd (qsys + data resolve).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/liuming/.openclaw/workspace/SysQ")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scratch.ablation.diag_common import (  # noqa: E402
    load_close_matrix,
)

PARQUET = Path(
    "/home/liuming/.openclaw/workspace/SysQ/scratch/ablation/reinfer_retrain_days.parquet"
)
HORIZONS = (20, 60, 180)
UNIV_EXCESS_PCT = 100.0  # report in %


def load_panel() -> pd.DataFrame:
    df = pd.read_parquet(PARQUET)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


def add_forward_excess(df: pd.DataFrame, cm: pd.DataFrame) -> pd.DataFrame:
    """Attach fwd_H and fwd_H_excess_pct per (retrain_day, instrument)."""
    rows = []
    for t, g in df.groupby("trade_date"):
        sc = g[["instrument", "score_old", "score_new"]].copy()
        for h in HORIZONS:
            fwd = forward_return_strict(cm, t, h, sc["instrument"].tolist())
            sc[f"fwd_{h}"] = fwd.values
        univ_mean = {}
        for h in HORIZONS:
            univ_mean[h] = float(sc[f"fwd_{h}"].mean())
        for h in HORIZONS:
            sc[f"ex_{h}"] = (sc[f"fwd_{h}"] - univ_mean[h]) * UNIV_EXCESS_PCT
        sc["trade_date"] = t
        rows.append(sc.reset_index())
    out = pd.concat(rows, ignore_index=True)
    for c in ["trade_date", "instrument", "score_old", "score_new",
              *[f"fwd_{h}" for h in HORIZONS],
              *[f"ex_{h}" for h in HORIZONS]]:
        assert c in out.columns, c
    return df.merge(
        out.drop(columns=["score_old", "score_new"]),
        on=["trade_date", "instrument"], how="left",
    )


def forward_return_strict(cm: pd.DataFrame, at: pd.Timestamp, h: int, insts: list[str]) -> pd.Series:
    idx = cm.index
    if at not in idx:
        return pd.Series(np.nan, index=pd.Index(insts))
    pos = idx.get_loc(at)
    j = pos + h
    if j >= len(idx):
        return pd.Series(np.nan, index=pd.Index(insts))
    return (cm.iloc[j] / cm.iloc[pos] - 1.0).reindex(insts).astype(float)


def assign_buckets(df: pd.DataFrame) -> pd.DataFrame:
    """Add old_top5/new_top5 membership + BOTH/NEW_IN/DROPPED bucket per retrain day."""
    recs = []
    for t, g in df.groupby("trade_date"):
        g = g.sort_values("score_new", ascending=False)
        new5 = set(g.head(5)["instrument"])
        g = g.sort_values("score_old", ascending=False)
        old5 = set(g.head(5)["instrument"])
        both = old5 & new5
        new_in = new5 - old5
        dropped = old5 - new5
        g = g.assign(
            old_top5=g["instrument"].isin(old5),
            new_top5=g["instrument"].isin(new5),
            both=g["instrument"].isin(both),
            new_in=g["instrument"].isin(new_in),
            dropped=g["instrument"].isin(dropped),
        )
        recs.append(g)
    return pd.concat(recs, ignore_index=True)


def _topk_names(g: pd.DataFrame, col: str, k: int) -> set:
    return set(g.sort_values(col, ascending=False).head(k)["instrument"])


def assign_stability(df: pd.DataFrame) -> pd.DataFrame:
    """Per-day stability metrics merged onto every row."""
    rows = []
    for t, g in df.groupby("trade_date"):
        g = g.sort_values("trade_date")
        n = len(g)
        rho = g["score_old"].corr(g["score_new"], method="spearman")
        old5 = _topk_names(g, "score_old", 5)
        new5 = _topk_names(g, "score_new", 5)
        old10 = _topk_names(g, "score_old", 10)
        new10 = _topk_names(g, "score_new", 10)
        old20 = _topk_names(g, "score_old", 20)
        jac5 = len(old5 & new5) / len(old5 | new5) if old5 | new5 else np.nan
        ov10 = len(old10 & new10) / len(old10) if old10 else np.nan
        new5_from_old5 = len(new5 & old5) / 5
        new5_from_old10 = len(new5 & old10) / 5
        new5_from_old20 = len(new5 & old20) / 5
        rank_old = g["score_old"].rank(ascending=False, method="first")
        rank_new = g["score_new"].rank(ascending=False, method="first")
        rdelta = (rank_old - rank_new).abs()
        meta = {
            "trade_date": t,
            "n": n,
            "rho": rho,
            "jac5": jac5,
            "ov10": ov10,
            "new5_from_old5": new5_from_old5,
            "new5_from_old10": new5_from_old10,
            "new5_from_old20": new5_from_old20,
            "rdelta_median": float(rdelta.median()),
            "rdelta_p90": float(rdelta.quantile(0.90)),
            "rdelta_mean": float(rdelta.mean()),
        }
        g = g.assign(**{k: v for k, v in meta.items() if k != "trade_date"})
        g["rdelta"] = rdelta.values
        rows.append(g)
    return pd.concat(rows, ignore_index=True)


def year_col(df: pd.DataFrame) -> pd.Series:
    return df["trade_date"].dt.year


def basket_stats(b: pd.DataFrame, mask: pd.Series, label: str) -> dict:
    """Per-basket stats over a given horizon (mask filters to valid fwd rows)."""
    sub = b[mask]
    n = int(sub["instrument"].nunique())
    out = {"bucket": label, "n_entries": int(len(sub)), "n_names": n}
    for h in HORIZONS:
        e = sub[f"ex_{h}"].dropna()
        f = sub[f"fwd_{h}"].dropna()
        if len(e) == 0:
            out[f"ex_{h}_n"], out[f"ex_{h}_med"], out[f"ex_{h}_mean"] = 0, np.nan, np.nan
            out[f"ex_{h}_pos"], out[f"q25_{h}"], out[f"q75_{h}"] = np.nan, np.nan, np.nan
            out[f"p90_{h}"], out[f"max_{h}"] = np.nan, np.nan
            continue
        out[f"ex_{h}_n"] = int(len(e))
        out[f"ex_{h}_med"] = float(e.median())
        out[f"ex_{h}_mean"] = float(e.mean())
        out[f"ex_{h}_pos"] = float((e > 0).mean())
        out[f"q25_{h}"] = float(e.quantile(0.25))
        out[f"q75_{h}"] = float(e.quantile(0.75))
        out[f"p90_{h}"] = float(e.quantile(0.90))
        out[f"max_{h}"] = float(e.max())
    return out


def _group_stats(df: pd.DataFrame, group_col: str) -> list[dict]:
    out = []
    if group_col == "__all__":
        grouped = [("__all__", df)]
    else:
        grouped = df.groupby(group_col)
    for gval, g in grouped:
        for bucket in ("both", "new_in", "dropped"):
            mask = g[f"ex_20"].notna() | g[f"ex_180"].notna()
            if g[bucket].any():
                s = basket_stats(g, g[bucket].astype(bool), bucket)
                s[group_col] = gval
                out.append(s)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-stability", action="store_true")
    args = ap.parse_args()

    df = load_panel()
    print(f"panel: {len(df)} rows, {df['trade_date'].nunique()} retrain days",
          file=sys.stderr)
    cm = load_close_matrix()
    df = add_forward_excess(df, cm)
    df = assign_buckets(df)
    df = assign_stability(df)
    df["year"] = year_col(df)

    report: dict = {}

    # ── Section B: basket forward excess + replacement_edge ──
    print("\n=== B. Basket forward excess (%-pts vs same-day scored-universe EW) ===")
    print(f"{'bucket':<8}" + "".join(
        f"{'ex20':>9}{'ex60':>9}{'ex180':>9}" for _ in [0]))
    for bucket in ("both", "new_in", "dropped"):
        m = df[bucket].astype(bool)
        row = []
        for h in HORIZONS:
            e = df.loc[m, f"ex_{h}"].dropna()
            row.append(f"{e.mean():>9.2f}" if len(e) else f"{'--':>9}")
        print(f"{bucket:<8}" + "".join(row))

    # replacement edge per horizon
    print("\nreplacement_edge_H = mean(R_NEW_IN,H) − mean(R_DROPPED,H)  (fwd excess) + (fwd raw):")
    for h in HORIZONS:
        ni = df.loc[df["new_in"], f"fwd_{h}"].dropna()
        dr = df.loc[df["dropped"], f"fwd_{h}"].dropna()
        ni_e = df.loc[df["new_in"], f"ex_{h}"].dropna()
        dr_e = df.loc[df["dropped"], f"ex_{h}"].dropna()
        raw = (ni.mean() - dr.mean()) * 100 if len(ni) and len(dr) else np.nan
        exc = (ni_e.mean() - dr_e.mean()) if len(ni_e) and len(dr_e) else np.nan
        print(f"  H={h:3d}: raw={raw:+7.2f}pp  excess={exc:+7.2f}pp  "
              f"(NEW_IN n={len(ni)}, DROPPED n={len(dr)})")

    # ── Section C: full-sample + yearly + cohort ──
    print("\n=== C1. Full-sample basket stats (forward excess, %-pts) ===")
    full = _group_stats(df, "__all__")
    if full:
        f0 = full[0]
        print(f"{'bucket':<8}{'n':>5}" + "".join(
            f"{f'med_{h}':>8}{f'mean_{h}':>8}{f'pos_{h}':>7}{f'p90_{h}':>8}" for h in HORIZONS))
        for s in full:
            line = f"{s['bucket']:<8}{s['n_entries']:>5}"
            for h in HORIZONS:
                line += f"{s.get(f'ex_{h}_med', np.nan):>8.2f}{s.get(f'ex_{h}_mean', np.nan):>8.2f}{s.get(f'ex_{h}_pos', np.nan):>7.0%}{s.get(f'p90_{h}', np.nan):>8.2f}"
            print(line)

    print("\n=== C2. Yearly basket stats (excess, %-pts), NEW_IN vs DROPPED edge ===")
    yrs = sorted(df["year"].unique())
    print(f"{'year':<6}{'bucket':<8}{'n':>5}" + "".join(
        f"{f'med_{h}':>8}{f'pos_{h}':>7}" for h in HORIZONS))
    for y in yrs:
        g = df[df["year"] == y]
        for bucket in ("both", "new_in", "dropped"):
            m = g[bucket].astype(bool)
            if not m.any():
                continue
            s = basket_stats(g, m, bucket)
            line = f"{y:<6}{bucket:<8}{s['n_entries']:>5}"
            for h in HORIZONS:
                line += f"{s.get(f'ex_{h}_med', np.nan):>8.2f}{s.get(f'ex_{h}_pos', np.nan):>7.0%}"
            print(line)
        # yearly edge direction
        edge = []
        for h in HORIZONS:
            ni = g.loc[g["new_in"], f"ex_{h}"].dropna()
            dr = g.loc[g["dropped"], f"ex_{h}"].dropna()
            edge.append(f"{ni.mean() - dr.mean():+.2f}" if len(ni) and len(dr) else "--")
        print(f"  {'edge(NEW_IN-DROPPED)':<30}" + "".join(f"{e:>15}" for e in edge))

    print("\n=== C3. Model-version cohort stats (excess 180d, %-pts) ===")
    coh = df.groupby("model_version_new").size().index.tolist()
    print(f"cohorts (model_version_new): {len(coh)}")
    for bucket in ("both", "new_in", "dropped"):
        rows = []
        for mv in coh:
            g = df[df["model_version_new"] == mv]
            m = g[bucket].astype(bool)
            if not m.any():
                continue
            s = basket_stats(g, m, bucket)
            rows.append((mv, s["n_entries"], s.get("ex_180_med", np.nan),
                         s.get("ex_180_pos", np.nan)))
        pos = [r[3] for r in rows if r[3] == r[3]]
        print(f"  {bucket:<8} cohorts={len(rows)}  "
              f"ex180_pos_rate median={np.median(pos):.0%}" if pos else f"  {bucket:<8} no data")

    # ── Section D: stability ──
    print("\n=== D. Model-version stability (per retrain day) ===")
    st = df.groupby("trade_date").agg(
        rho=("rho", "first"), jac5=("jac5", "first"), ov10=("ov10", "first"),
        new5_from_old5=("new5_from_old5", "first"),
        new5_from_old10=("new5_from_old10", "first"),
        new5_from_old20=("new5_from_old20", "first"),
        rdelta_med=("rdelta_median", "first"), rdelta_p90=("rdelta_p90", "first"),
    ).sort_index()
    print(f"{'stat':<22}{'mean':>8}{'med':>8}{'p10':>8}{'p90':>8}")
    for col, fmt in [("rho", ".3f"), ("jac5", ".2f"), ("ov10", ".2f"),
                     ("new5_from_old5", ".2f"), ("new5_from_old10", ".2f"),
                     ("new5_from_old20", ".2f"), ("rdelta_med", ".1f"),
                     ("rdelta_p90", ".1f")]:
        s = st[col]
        print(f"{col:<22}{s.mean():>8{fmt}}{s.median():>8{fmt}}{s.quantile(0.1):>8{fmt}}{s.quantile(0.9):>8{fmt}}")

    # ── Section E: right-tail attribution ──
    print("\n=== E. Right-tail attribution (180d fwd from entry day) ===")
    print("Fraction of NEW_IN / BOTH / DROPPED 180d forwards exceeding thresholds:")
    for thr in (0.20, 0.50, 1.00, -0.20, -0.40):
        for bucket in ("both", "new_in", "dropped"):
            m = df[bucket].astype(bool)
            f = df.loc[m, "fwd_180"].dropna()
            if len(f) == 0:
                continue
            if thr >= 0:
                print(f"  fwd_180 > {thr:+.0%}: {bucket:<8} {(f > thr).mean():>6.1%}  (n={len(f)})")
            else:
                print(f"  fwd_180 < {thr:+.0%}: {bucket:<8} {(f < thr).mean():>6.1%}  (n={len(f)})")
        print()

    # top PnL cross-ref (S180_20d executions) — optional, printed separately
    report["n_retrain_days"] = int(df["trade_date"].nunique())
    Path("scratch/ablation/old_new_report.json").write_text(
        json.dumps(report, indent=1))
    print("\n(report json partial -> scratch/ablation/old_new_report.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
