#!/usr/bin/env python3
"""Evaluate 60d top-weighted experiments.

Reads predictions from artifacts/diagnostics/60d_top_weighted/predictions/
Outputs full report to artifacts/diagnostics/60d_top_weighted/
"""
from __future__ import annotations

import sys, warnings
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from qsys.label.store import LabelStore

P = Path(__file__).resolve().parents[2]
OUT = P / "artifacts/diagnostics/60d_top_weighted"
PRED_DIR = OUT / "predictions"
OUT.mkdir(parents=True, exist_ok=True)

LABEL_ID = "fwd_ret_60d_raw"

al = LabelStore(root=str(P / "data/research")).load_labels(LABEL_ID)
al["trade_date"] = al["trade_date"].astype(str).str[:10]
al["ts_code"] = al["instrument"]

SCHEMES = ["baseline_no_weight", "top10pct_weight_3x", "top20pct_weight_2x", "top10pct_3x_top20pct_2x"]


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

def load_scheme(scheme: str) -> pd.DataFrame:
    path = PRED_DIR / f"{scheme}.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    df = df.merge(al[["trade_date", "ts_code", "label_value"]], on=["trade_date", "ts_code"], how="left")
    df = df.dropna(subset=["label_value"])
    df["score_z"] = df.groupby("trade_date")["score"].transform(lambda s: (s - s.mean()) / s.std())
    return df


def daily_ic(df):
    d = df.groupby("trade_date").apply(
        lambda g: pd.Series({
            "ic": g["score_z"].corr(g["label_value"]),
            "rank_ic": g["score_z"].rank().corr(g["label_value"].rank()),
        }), include_groups=False
    ).dropna()
    return d


def per_date_topk(df, k):
    ds = sorted(df["trade_date"].unique())
    vals = pd.concat([df[df["trade_date"] == dt].sort_values("score_z", ascending=False).head(k)["label_value"] for dt in ds]).dropna()
    return vals


def topk_inner_rankic(df, k):
    ds = sorted(df["trade_date"].unique())
    ics = []
    for dt in ds:
        sub = df[df["trade_date"] == dt].sort_values("score_z", ascending=False).head(k)
        if len(sub) > 3:
            ics.append(sub["score_z"].rank().corr(sub["label_value"].rank()))
    return pd.Series(ics).dropna()


def bucket_lift(df):
    res = []
    for dt in sorted(df["trade_date"].unique()):
        sub = df[df["trade_date"] == dt].copy()
        sub["bucket"] = pd.qcut(sub["score_z"].rank(pct=True), [0, 0.01, 0.05, 0.10, 0.20, 0.80, 1.0],
                                 labels=["top1%","top5%","top10%","top20%","rest80%","bottom20%"], duplicates="drop")
        for b in sub["bucket"].unique():
            bd = sub[sub["bucket"] == b]
            res.append({"date": dt, "bucket": b, "mean_ret": bd["label_value"].mean(),
                        "hit": (bd["label_value"] > 0).mean(), "bad": (bd["label_value"] < 0).mean(),
                        "gt30": (bd["label_value"] > 0.3).mean(), "n": len(bd)})
    return pd.DataFrame(res)


# ═══════════════════════════════════════════════════════════════════
# Evaluate all schemes
# ═══════════════════════════════════════════════════════════════════

all_ic = []
all_yearly_ic = []
all_topk_overall = []
all_topk_yearly = []
all_inner_rankic = []
all_lift = []

for scheme in SCHEMES:
    df = load_scheme(scheme)
    if df.empty:
        print(f"  {scheme}: no data")
        continue
    print(f"\n{'=' * 60}")
    print(f"  {scheme} ({len(df)} obs)")
    print(f"{'=' * 60}")

    # 3.1 IC / RankIC
    ic_df = daily_ic(df)
    r = {"scheme": scheme, "ic": ic_df["ic"].mean(), "icir": ic_df["ic"].mean() / ic_df["ic"].std() if ic_df["ic"].std() > 0 else 0,
         "rank_ic": ic_df["rank_ic"].mean(), "rank_icir": ic_df["rank_ic"].mean() / ic_df["rank_ic"].std() if ic_df["rank_ic"].std() > 0 else 0}
    all_ic.append(r)
    print(f"  IC={r['ic']:.4f} ICIR={r['icir']:.3f} RankIC={r['rank_ic']:.4f}")

    # Yearly IC
    ic_y = ic_df.reset_index()
    ic_y["year"] = ic_y["trade_date"].str[:4]
    for y in ["2020", "2021", "2022", "2023", "2024", "2025"]:
        yr = ic_y[ic_y["year"] == y]
        if len(yr) < 5:
            continue
        all_yearly_ic.append({"scheme": scheme, "year": y, "ic": yr["ic"].mean(), "icir": yr["ic"].mean() / yr["ic"].std() if yr["ic"].std() > 0 else 0,
                              "rank_ic": yr["rank_ic"].mean(), "rank_icir": yr["rank_ic"].mean() / yr["rank_ic"].std() if yr["rank_ic"].std() > 0 else 0})

    # 3.2 TopK quality
    for k in [20, 50, 100]:
        vals = per_date_topk(df, k)
        if len(vals) == 0:
            continue
        all_topk_overall.append({
            "scheme": scheme, "k": k, "mean_ret": vals.mean(), "median_ret": vals.median(),
            "hit_rate": (vals > 0).mean(), "bad_rate": (vals < 0).mean(),
            "gt10": (vals > 0.10).mean(), "gt20": (vals > 0.20).mean(), "gt30": (vals > 0.30).mean(),
            "worst": vals.min(), "best": vals.max(), "n": len(vals),
        })

        # Yearly
        for y in ["2020", "2021", "2022", "2023", "2024", "2025"]:
            yr_df = df[df["trade_date"].str[:4] == y]
            if len(yr_df) < 200:
                continue
            v = per_date_topk(yr_df, k)
            if len(v) == 0:
                continue
            all_topk_yearly.append({
                "scheme": scheme, "k": k, "year": y,
                "mean_ret": v.mean(), "hit_rate": (v > 0).mean(), "bad_rate": (v < 0).mean(),
                "gt10": (v > 0.10).mean(), "gt20": (v > 0.20).mean(), "gt30": (v > 0.30).mean(),
                "n": len(v),
            })

    # 3.3 TopK inner RankIC
    for k in [20, 50, 100]:
        irk = topk_inner_rankic(df, k)
        if len(irk) > 0:
            all_inner_rankic.append({"scheme": scheme, "k": k, "inner_rankic": irk.mean(), "inner_rankicir": irk.mean() / irk.std() if irk.std() > 0 else 0})
            print(f"  Top{k} inner RankIC={irk.mean():.4f}")

    # 3.4 Bucket lift
    bl = bucket_lift(df)
    bl_agg = bl.groupby("bucket").agg(mean_ret=("mean_ret", "mean"), hit=("hit", "mean"), bad=("bad", "mean"), gt30=("gt30", "mean"), n=("n", "sum")).reset_index()
    bl_agg["scheme"] = scheme
    all_lift.append(bl_agg)
    print(f"  Top1% mean_ret={bl_agg.loc[bl_agg['bucket']=='top1%', 'mean_ret'].values[0]:.4f}" if 'top1%' in bl_agg['bucket'].values else "", flush=True)


# ═══════════════════════════════════════════════════════════════════
# Output
# ═══════════════════════════════════════════════════════════════════

def csv(df, name):
    path = OUT / name
    df.to_csv(path, index=False)
    print(f"  Saved: {path}")

csv(pd.DataFrame(all_ic), "summary_ic.csv")
csv(pd.DataFrame(all_yearly_ic), "yearly_ic.csv")
csv(pd.DataFrame(all_topk_overall), "topk_quality_overall.csv")
csv(pd.DataFrame(all_topk_yearly), "topk_quality_yearly.csv")
csv(pd.DataFrame(all_inner_rankic), "topk_inner_rankic.csv")
if all_lift:
    csv(pd.concat(all_lift, ignore_index=True), "bucket_lift.csv")

# Print final comparison table
print(f"\n{'=' * 72}")
print("FINAL COMPARISON")
print(f"{'=' * 72}")
print(f"{'Scheme':<30s} {'IC':>7s} {'ICIR':>7s} {'RkIC':>7s} {'T20mean':>8s} {'T20hit':>7s} {'T50mean':>8s} {'T100mean':>9s}")
print("-" * 80)
for r in sorted(all_ic, key=lambda x: x["ic"], reverse=True):
    sk = r["scheme"]
    t20 = {x["k"]: x for x in all_topk_overall if x["scheme"] == sk}
    print(f"{sk:<30s} {r['ic']:>7.4f} {r['icir']:>7.3f} {r['rank_ic']:>7.4f} "
          f"{t20[20]['mean_ret']:>8.4f} {t20[20]['hit_rate']:>7.2%} "
          f"{t20[50]['mean_ret']:>8.4f} {t20[100]['mean_ret']:>9.4f}")

print(f"\n{'─'*30} TopK inner RankIC ──")
print(f"{'Scheme':<30s}", end="")
for k in [20, 50, 100]:
    print(f" {'T'+str(k)+' innerRk':>14s}", end="")
print()
for r in all_inner_rankic:
    print(f"{r['scheme']:<30s} {r['inner_rankic']:>14.4f}", end="")
    print()

print(f"\nDone — reports saved to {OUT}")
