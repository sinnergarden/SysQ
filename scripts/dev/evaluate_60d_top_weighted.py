#!/usr/bin/env python3
"""Evaluate 60d top-weighted experiments — research artifact-level evaluation.

Usage:
    python scripts/dev/evaluate_60d_top_weighted.py              # full
    python scripts/dev/evaluate_60d_top_weighted.py --smoke      # smoke

Reads predictions from artifacts/diagnostics/60d_top_weighted/predictions[_smoke]/
Outputs to artifacts/diagnostics/60d_top_weighted/ or .../smoke/
"""
from __future__ import annotations

import argparse, sys, warnings
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from qsys.label.store import LabelStore

P = Path(__file__).resolve().parents[2]
BASE = P / "artifacts/diagnostics/60d_top_weighted"
LABEL_ID = "fwd_ret_60d_raw"

parser = argparse.ArgumentParser()
parser.add_argument("--smoke", action="store_true")
args = parser.parse_args()

SUFFIX = "smoke" if args.smoke else ""
OUT = BASE / SUFFIX if args.smoke else BASE
PRED_DIR = BASE / (f"predictions_{SUFFIX}" if args.smoke else "predictions")
OUT.mkdir(parents=True, exist_ok=True)

al = LabelStore(root=str(P / "data/research")).load_labels(LABEL_ID)
al["trade_date"] = al["trade_date"].astype(str).str[:10]
al["ts_code"] = al["instrument"]

SCHEMES = ["baseline_no_weight", "top10pct_weight_3x", "top20pct_weight_2x", "top10pct_3x_top20pct_2x"]


def load_scheme(scheme: str) -> pd.DataFrame:
    path = PRED_DIR / f"{scheme}.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    df = df.merge(al[["trade_date", "ts_code", "label_value"]], on=["trade_date", "ts_code"], how="left")
    df = df.dropna(subset=["label_value"])
    # Cross-sectional z-score per trade_date
    df["score_z"] = df.groupby("trade_date")["score"].transform(lambda s: (s - s.mean()) / s.std())
    return df


def daily_ic(df):
    return df.groupby("trade_date").apply(
        lambda g: pd.Series({
            "ic": g["score_z"].corr(g["label_value"]),
            "rank_ic": g["score_z"].rank().corr(g["label_value"].rank()),
        }), include_groups=False
    ).dropna()


def per_date_topk(df, k):
    ds = sorted(df["trade_date"].unique())
    vals = pd.concat([
        df[df["trade_date"] == dt].sort_values("score_z", ascending=False).head(k)["label_value"]
        for dt in ds
    ]).dropna()
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
    """Per-date score percentile buckets, then aggregate.

    Uses ascending=False rank so top1% = highest scoring stocks.
    """
    rows = []
    for dt in sorted(df["trade_date"].unique()):
        sub = df[df["trade_date"] == dt].copy()
        sub["_pct_desc"] = sub["score_z"].rank(pct=True, ascending=False)
        sub["bucket"] = pd.cut(
            sub["_pct_desc"],
            bins=[0, 0.01, 0.05, 0.10, 0.20, 0.80, 1.0],
            labels=["top1%", "top5%", "top10%", "top20%", "middle60%", "bottom20%"],
            include_lowest=True,
        )
        for b in sub["bucket"].unique():
            bd = sub[sub["bucket"] == b]
            rows.append({
                "date": dt, "bucket": b,
                "mean_ret": bd["label_value"].mean(),
                "hit": (bd["label_value"] > 0).mean(),
                "bad": (bd["label_value"] < 0).mean(),
                "gt30": (bd["label_value"] > 0.3).mean(),
                "n": len(bd),
            })
    return pd.DataFrame(rows)


# ═══════════════════════════════════════════════════════════════════
# Evaluate
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
        print(f"  {scheme}: no data (skip)")
        continue
    print(f"\n{'=' * 60}")
    print(f"  {scheme} ({len(df)} obs)")
    print(f"{'=' * 60}")

    # 3.1 IC / RankIC
    ic_df = daily_ic(df)
    r = {
        "scheme": scheme,
        "ic": ic_df["ic"].mean(),
        "icir": ic_df["ic"].mean() / ic_df["ic"].std() if ic_df["ic"].std() > 0 else 0,
        "rank_ic": ic_df["rank_ic"].mean(),
        "rank_icir": ic_df["rank_ic"].mean() / ic_df["rank_ic"].std() if ic_df["rank_ic"].std() > 0 else 0,
    }
    all_ic.append(r)
    print(f"  IC={r['ic']:.4f} ICIR={r['icir']:.3f} RankIC={r['rank_ic']:.4f}")

    # Yearly
    ic_y = ic_df.reset_index()
    ic_y["year"] = ic_y["trade_date"].str[:4]
    for y in ["2020", "2021", "2022", "2023", "2024", "2025"]:
        yr = ic_y[ic_y["year"] == y]
        if len(yr) < 5:
            continue
        all_yearly_ic.append({
            "scheme": scheme, "year": y,
            "ic": yr["ic"].mean(),
            "icir": yr["ic"].mean() / yr["ic"].std() if yr["ic"].std() > 0 else 0,
            "rank_ic": yr["rank_ic"].mean(),
            "rank_icir": yr["rank_ic"].mean() / yr["rank_ic"].std() if yr["rank_ic"].std() > 0 else 0,
        })

    # 3.2 TopK quality
    for k in [20, 50, 100]:
        vals = per_date_topk(df, k)
        if len(vals) == 0:
            continue
        all_topk_overall.append({
            "scheme": scheme, "k": k,
            "mean_ret": vals.mean(),
            "median_ret": vals.median(),
            "hit_rate": (vals > 0).mean(),
            "bad_rate": (vals < 0).mean(),
            "gt10": (vals > 0.10).mean(),
            "gt20": (vals > 0.20).mean(),
            "gt30": (vals > 0.30).mean(),
            "worst": vals.min(),
            "best": vals.max(),
            "n": len(vals),
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
                "mean_ret": v.mean(),
                "hit_rate": (v > 0).mean(),
                "bad_rate": (v < 0).mean(),
                "gt10": (v > 0.10).mean(),
                "gt20": (v > 0.20).mean(),
                "gt30": (v > 0.30).mean(),
                "n": len(v),
            })

    # 3.3 TopK inner RankIC
    inner_row = {"scheme": scheme}
    for k in [20, 50, 100]:
        irk = topk_inner_rankic(df, k)
        if len(irk) > 0:
            inner_row[f"top{k}_inner_rankic"] = irk.mean()
            inner_row[f"top{k}_inner_rankicir"] = irk.mean() / irk.std() if irk.std() > 0 else 0
    if inner_row:
        all_inner_rankic.append(inner_row)
        for k in [20, 50, 100]:
            val = inner_row.get(f"top{k}_inner_rankic", None)
            if val is not None:
                print(f"  Top{k} inner RankIC={val:.4f}")

    # 3.4 Bucket lift
    bl = bucket_lift(df)
    bl_agg = bl.groupby("bucket", observed=False).agg(
        mean_ret=("mean_ret", "mean"),
        hit=("hit", "mean"),
        bad=("bad", "mean"),
        gt30=("gt30", "mean"),
        n=("n", "sum"),
    ).reset_index()
    bl_agg["scheme"] = scheme
    all_lift.append(bl_agg)

    # Spread metrics
    top10 = bl_agg.loc[bl_agg["bucket"] == "top10%", "mean_ret"].values
    top20 = bl_agg.loc[bl_agg["bucket"] == "top20%", "mean_ret"].values
    bot20 = bl_agg.loc[bl_agg["bucket"] == "bottom20%", "mean_ret"].values
    if len(top10) and len(bot20):
        print(f"  top10-bot20 spread={top10[0]-bot20[0]:.4f}")
    if len(top20) and len(bot20):
        print(f"  top20-bot20 spread={top20[0]-bot20[0]:.4f}")


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
if all_inner_rankic:
    wide = pd.DataFrame(all_inner_rankic)
    csv(wide, "topk_inner_rankic.csv")
if all_lift:
    csv(pd.concat(all_lift, ignore_index=True), "bucket_lift.csv")

# ═══════════════════════════════════════════════════════════════════
# Console summary
# ═══════════════════════════════════════════════════════════════════

print(f"\n{'=' * 72}")
print("FINAL COMPARISON")
print(f"{'=' * 72}")
# IC table
print(f"\n{'Scheme':<30s} {'IC':>7s} {'ICIR':>7s} {'RkIC':>7s} {'RkICIR':>7s}")
print("-" * 60)
for r in sorted(all_ic, key=lambda x: x["ic"], reverse=True):
    print(f"{r['scheme']:<30s} {r['ic']:>7.4f} {r['icir']:>7.3f} {r['rank_ic']:>7.4f} {r['rank_icir']:>7.3f}")

# TopK quality table
print(f"\n{'Scheme':<30s} {'T20mean':>8s} {'T20hit':>7s} {'T50mean':>8s} {'T100mean':>9s}")
print("-" * 65)
for r in all_ic:
    sk = r["scheme"]
    tops = {x["k"]: x for x in all_topk_overall if x["scheme"] == sk}
    if 20 in tops and 50 in tops and 100 in tops:
        print(f"{sk:<30s} {tops[20]['mean_ret']:>8.4f} {tops[20]['hit_rate']:>7.2%} "
              f"{tops[50]['mean_ret']:>8.4f} {tops[100]['mean_ret']:>9.4f}")

# Inner RankIC wide table
if all_inner_rankic:
    print(f"\n{'Scheme':<30s} {'T20inRk':>8s} {'T50inRk':>8s} {'T100inRk':>9s}")
    print("-" * 58)
    for row in all_inner_rankic:
        print(f"{row['scheme']:<30s} {row.get('top20_inner_rankic',0):>8.4f} "
              f"{row.get('top50_inner_rankic',0):>8.4f} {row.get('top100_inner_rankic',0):>9.4f}")

# ═══════════════════════════════════════════════════════════════════
# Report.md
# ═══════════════════════════════════════════════════════════════════

lines = []
lines.append("# 60d Top-Weighted LightGBM — Sample Weight Experiment")
lines.append("")
lines.append("## 1. 实验目的")
lines.append("")
lines.append("验证在 60d 相同 feature cache 和 baseline feature 组合下，仅通过对头部样本（高 label_value）")
lines.append("加 loss weight，是否能提升 Top20/Top50/Top100 的排序质量。")
lines.append("")
lines.append("**不新增 feature。不改 production pipeline。**")
lines.append("")
lines.append("## 2. 实验配置")
lines.append("")
lines.append("- **Feature list:** `v3a_plus_liquidity_financial_rc` (96 feats)")
lines.append("- **Label:** `fwd_ret_60d_raw`")
lines.append(f"- **Rolling windows:** 504d train / 20d step / 67 windows")
lines.append(f"- **Universe:** CSI800")
lines.append(f"- **Model:** LightGBM regression, 300 trees, early stopping 20")
lines.append(f"- **Signal transform:** daily_zscore")
lines.append("")
lines.append("## 3. Weight Scheme 定义")
lines.append("")
lines.append("每个 trade_date 横截面内按 `label_value` 降序 rank(pct=True)：")
lines.append("")
lines.append("| Scheme | top10% | top10-20% | rest |")
lines.append("|:---|:---:|:---:|:---:|")
lines.append("| baseline_no_weight | 1.0 | 1.0 | 1.0 |")
lines.append("| top10pct_weight_3x | **3.0** | 1.0 | 1.0 |")
lines.append("| top20pct_weight_2x | 2.0 | 2.0 | 1.0 |")
lines.append("| top10pct_3x_top20pct_2x | **3.0** | **2.0** | 1.0 |")
lines.append("")
lines.append("## 4. Storage Semantic")
lines.append("")
lines.append("- 本实验为 **research artifact-level signal**，不写入生产 SignalStore")
lines.append("- 每个 scheme 是一个 **独立 model idea**，存储独立 prediction parquet")
lines.append("- 同 scheme 重跑**覆盖**同一文件，不生成 timestamp run_id")
lines.append("- 不覆盖已有 60d baseline signal")
lines.append("- 后续如果某个 scheme 进入正式 candidate，再另开 PR 写入 SignalStore")
lines.append("")
lines.append("## 5. IC 对比")
lines.append("")

suffix = "（smoke test，仅 2 个窗口）" if args.smoke else ""
lines.append(f"### Overall IC{suffix}")
lines.append("")
# Build IC markdown table
lines.append(f"| {'Scheme':<30s} | {'IC':>7s} | {'ICIR':>7s} | {'RankIC':>7s} | {'RankICIR':>7s} |")
lines.append(f"| {'-'*30} | {'-'*7} | {'-'*7} | {'-'*7} | {'-'*7} |")
for r in sorted(all_ic, key=lambda x: x["ic"], reverse=True):
    lines.append(f"| {r['scheme']:<30s} | {r['ic']:>7.4f} | {r['icir']:>7.3f} | {r['rank_ic']:>7.4f} | {r['rank_icir']:>7.3f} |")

if not args.smoke:
    lines.append("")
    lines.append("### Yearly IC")
    lines.append("")
    years = ["2020", "2021", "2022", "2023", "2024", "2025"]
    hdr = f"| {'Scheme':<30s} |"
    for y in years:
        hdr += f" {y:>7s} |"
    lines.append(hdr)
    sep = f"| {'-'*30} |"
    for _ in years:
        sep += f" {'-'*7} |"
    lines.append(sep)
    for r in all_ic:
        line = f"| {r['scheme']:<30s} |"
        for y in years:
            match = [x for x in all_yearly_ic if x["scheme"] == r["scheme"] and x["year"] == y]
            if match:
                line += f" {match[0]['ic']:>7.4f} |"
            else:
                line += f" {'n/a':>7s} |"
        lines.append(line)

lines.append("")
lines.append("## 6. TopK 质量对比")
lines.append("")
lines.append("### Overall")
lines.append("")
lines.append(f"| {'Scheme':<30s} | {'T20mean':>8s} | {'T20hit':>7s} | {'T50mean':>8s} | {'T100mean':>9s} |")
lines.append(f"| {'-'*30} | {'-'*8} | {'-'*7} | {'-'*8} | {'-'*9} |")
for r in all_ic:
    sk = r["scheme"]
    tops = {x["k"]: x for x in all_topk_overall if x["scheme"] == sk}
    if 20 in tops:
        lines.append(f"| {sk:<30s} | {tops[20]['mean_ret']:>8.4f} | {tops[20]['hit_rate']:>7.2%} | "
                     f"{tops[50]['mean_ret']:>8.4f} | {tops[100]['mean_ret']:>9.4f} |")

lines.append("")
lines.append("## 7. TopK Inner RankIC")
lines.append("")
lines.append(f"| {'Scheme':<30s} | {'T20inner':>8s} | {'T50inner':>8s} | {'T100inner':>9s} |")
lines.append(f"| {'-'*30} | {'-'*8} | {'-'*8} | {'-'*9} |")
for row in all_inner_rankic:
    lines.append(f"| {row['scheme']:<30s} | {row.get('top20_inner_rankic', 0):>8.4f} | "
                 f"{row.get('top50_inner_rankic', 0):>8.4f} | {row.get('top100_inner_rankic', 0):>9.4f} |")

lines.append("")
lines.append("## 8. Bucket Lift")
lines.append("")
for r in all_ic:
    sk = r["scheme"]
    bl_data = [x for x in all_lift if x["scheme"].iloc[0] == sk]
    if not bl_data:
        continue
    bl_df = bl_data[0]
    lines.append(f"### {sk}")
    lines.append("")
    lines.append(f"| {'Bucket':<12s} | {'mean_ret':>8s} | {'hit':>7s} | {'bad':>7s} | {'gt30':>7s} | {'n':>8s} |")
    lines.append(f"| {'-'*12} | {'-'*8} | {'-'*7} | {'-'*7} | {'-'*7} | {'-'*8} |")
    for _, b in bl_df.iterrows():
        lines.append(f"| {b['bucket']:<12s} | {b['mean_ret']:>8.4f} | {b['hit']:>7.2%} | {b['bad']:>7.2%} | {b['gt30']:>7.2%} | {b['n']:>8.0f} |")
    # Spread
    top10 = bl_df.loc[bl_df["bucket"] == "top10%", "mean_ret"].values
    top20 = bl_df.loc[bl_df["bucket"] == "top20%", "mean_ret"].values
    bot20 = bl_df.loc[bl_df["bucket"] == "bottom20%", "mean_ret"].values
    if len(top10) and len(bot20):
        lines.append(f"\n  spread top10-bot20 = {top10[0]-bot20[0]:.4f}")
    if len(top20) and len(bot20):
        lines.append(f"  spread top20-bot20 = {top20[0]-bot20[0]:.4f}")
    lines.append("")

lines.append("")
lines.append("## 9. Weight Scheme Summary")
lines.append("")
ws_path = BASE / "weight_scheme_summary.csv"
if ws_path.exists():
    ws_df = pd.read_csv(ws_path)
    lines.append(f"| {'scheme':<30s} | {'n_rows':>7s} | {'w1':>7s} | {'w2':>7s} | {'w3':>7s} | {'mean_w':>7s} |")
    lines.append(f"| {'-'*30} | {'-'*7} | {'-'*7} | {'-'*7} | {'-'*7} | {'-'*7} |")
    for _, r in ws_df.iterrows():
        lines.append(f"| {r['scheme']:<30s} | {r['n_rows_total']:>7.0f} | {r['weight_1_count']:>7.0f} | "
                     f"{r['weight_2_count']:>7.0f} | {r['weight_3_count']:>7.0f} | {r['mean_weight']:>7.3f} |")
lines.append("")
lines.append(f"## 10. 结论{'（smoke test，等待全量）' if args.smoke else '（等待全量结果）'}")
lines.append("")
lines.append(f"{'*Pending full run result — this is a smoke test.*' if args.smoke else '*Pending full run result.*'}")

report_path = OUT / "report.md"
Path(report_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"\nReport → {report_path}")
print(f"\n{'=' * 60}")
print("  Done")
print(f"{'=' * 60}")
