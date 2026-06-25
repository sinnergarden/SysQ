#!/usr/bin/env python3
"""Overheat rule validation — Report 4 of liquidity bad case mining.

Usage:
    python scripts/research/case_mining_overheat_rules.py

Validates candidate overheat rules in score top 5% of:
    180d v3a+liquidity rolling predictions, monthly-unique samples.

Output:
    reports/research/liquidity_overheat_rule_validation_YYYYMMDD.csv
    Updates reports/research/liquidity_bad_case_mining_YYYYMMDD.md
"""

import sys, warnings
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
warnings.filterwarnings("ignore")

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "reports" / "research"
OUT_DIR.mkdir(parents=True, exist_ok=True)
TODAY = datetime.now().strftime("%Y%m%d")

# ── Load predictions (180d v3a+liq) ──
PRED = (
    "data/research/signals/fwd_ret_180d_raw__daily_zscore/"
    "rolling__180d_v3a_plus_liquidity__v3a_plus_liquidity_180d__"
    "fwd_ret_180d_raw__daily_zscore__2020-01-01_2025-12-31/predictions.parquet"
)

print("Loading predictions...")
p = pd.read_parquet(str(REPO / PRED))

# ── Load label ──
from qsys.label.store import LabelStore
print("Loading label...")
label = LabelStore().load_labels("fwd_ret_180d_raw")[["trade_date", "instrument", "label_value"]]

# ── Load stock info ──
import sqlite3
print("Loading stock info...")
conn = sqlite3.connect(str(REPO / "data" / "meta.db"))
stock_info = pd.read_sql("select ts_code, name, industry from stock_basic", conn)
conn.close()
stock_info = stock_info.rename(columns={"ts_code": "instrument"})

# ── Load features from FeatureStore ──
from qsys.feature.feature_store import FeatureStore, FeatureCacheKey, compute_feature_cache_key
from qsys.feature.feature_compute_registry import _PHASE1_HASH
import json

print("Loading features from cache...")
meta_files = list((REPO / "data/feature_cache/features/amount_log").glob("*.meta.json"))
source_hash = json.loads(meta_files[0].read_text())["source_manifest_hash"] if meta_files else ""

WANTED = ["amount_log", "rps_60d", "price_percentile_252d", "pe_rank_252d", "pb_rank_252d",
          "ret_60d", "ret_120d", "trend_smoothness_60d", "amount_zscore_20", "illiquidity"]

store = FeatureStore(root="data/feature_cache/features")
feat_dfs = {}
for feat in WANTED:
    fk = FeatureCacheKey(feature_id=feat, universe="csi800",
                         source_manifest_hash=source_hash,
                         compute_fn_hash=_PHASE1_HASH, pit_policy="rolling_past")
    ck = compute_feature_cache_key(fk)
    if store.exists(feat, ck):
        df = store.read_feature(feat, expected_cache_key=ck, strict_source_hash=source_hash)
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
        feat_dfs[feat] = df[["trade_date", "ts_code", feat]].rename(columns={"ts_code": "instrument"})
    else:
        print(f"  [WARN] {feat} not cached")

# ── Build analysis dataset ──
print("Building dataset...")
adf = p.merge(label, on=["trade_date", "instrument"], how="inner")
adf = adf.merge(stock_info, on="instrument", how="left")
for feat_name, fdf in feat_dfs.items():
    adf = adf.merge(fdf, on=["trade_date", "instrument"], how="left")

# Monthly unique: keep highest score per stock per month
adf["ym"] = adf["trade_date"].str[:7]
idx = adf.groupby(["instrument", "ym"])["score"].idxmax()
mdf = adf.loc[idx].reset_index(drop=True)

# Score rank pct within each date
mdf["score_rank_pct"] = mdf.groupby("trade_date")["score"].rank(pct=True)

# Top 5%
top5 = mdf[mdf["score_rank_pct"] >= 0.95].copy()
print(f"Monthly unique total: {len(mdf)}, top5: {len(top5)}")

# Amount log rank within each date
top5["al_rank_pct"] = top5.groupby("trade_date")["amount_log"].rank(pct=True)

# Ret buckets
def bucket_ret(r):
    if r < 0: return "bad"
    elif r < 0.10: return "flat"
    elif r < 0.30: return "weak_pos"
    elif r < 0.60: return "good"
    elif r < 1.00: return "big_win"
    else: return "super_win"

top5["ret_bucket"] = top5["label_value"].apply(bucket_ret)
top5["bad_fp"] = (top5["label_value"] < 0.1).astype(int)
top5["big_win"] = (top5["label_value"] > 0.6).astype(int)

# ── Define rules ──
# Ensure feature columns exist and compute binary flags
def has(field):
    return field in top5.columns and top5[field].notna().sum() > 100

RULES = []

# Single variable rules
if has("pe_rank_252d"):
    RULES.append(("R1: pe_rank>0.7", top5["pe_rank_252d"] > 0.7))
if has("pb_rank_252d"):
    RULES.append(("R2: pb_rank>0.7", top5["pb_rank_252d"] > 0.7))
if has("price_percentile_252d"):
    RULES.append(("R3: pp>0.7", top5["price_percentile_252d"] > 0.7))
if has("rps_60d"):
    RULES.append(("R4: rps>0.7", top5["rps_60d"] > 0.7))
if has("al_rank_pct"):
    RULES.append(("R5: al_rank>0.8", top5["al_rank_pct"] > 0.8))

# Combo rules
if has("pe_rank_252d") and has("price_percentile_252d"):
    RULES.append(("R6: pe>0.7+pp>0.7", (top5["pe_rank_252d"] > 0.7) & (top5["price_percentile_252d"] > 0.7)))
if has("pe_rank_252d") and has("rps_60d"):
    RULES.append(("R7: pe>0.7+rps>0.7", (top5["pe_rank_252d"] > 0.7) & (top5["rps_60d"] > 0.7)))
if has("price_percentile_252d") and has("rps_60d"):
    RULES.append(("R8: pp>0.7+rps>0.7", (top5["price_percentile_252d"] > 0.7) & (top5["rps_60d"] > 0.7)))
if has("pe_rank_252d") and has("price_percentile_252d") and has("rps_60d"):
    cond = (top5["pe_rank_252d"] > 0.7) & (top5["price_percentile_252d"] > 0.7) & (top5["rps_60d"] > 0.7)
    RULES.append(("R9: pe>0.7+pp>0.7+rps>0.7", cond))
if has("al_rank_pct") and has("pe_rank_252d") and has("price_percentile_252d") and has("rps_60d"):
    cond = (top5["al_rank_pct"] > 0.8) & (top5["pe_rank_252d"] > 0.7) & (top5["price_percentile_252d"] > 0.7) & (top5["rps_60d"] > 0.7)
    RULES.append(("R10: al>0.8+pe>0.7+pp>0.7+rps>0.7", cond))
if has("al_rank_pct") and has("price_percentile_252d") and has("rps_60d"):
    cond = (top5["al_rank_pct"] > 0.8) & (top5["price_percentile_252d"] > 0.7) & (top5["rps_60d"] > 0.7)
    RULES.append(("R11: al>0.8+pp>0.7+rps>0.7", cond))
if has("al_rank_pct") and has("price_percentile_252d") and (has("pe_rank_252d") or has("pb_rank_252d")):
    max_val = top5[["pe_rank_252d", "pb_rank_252d"]].max(axis=1) if has("pb_rank_252d") else top5["pe_rank_252d"]
    cond = (top5["al_rank_pct"] > 0.8) & (max_val > 0.7) & (top5["price_percentile_252d"] > 0.7)
    RULES.append(("R12: al>0.8+max(pe,pb)>0.7+pp>0.7", cond))

# ── Baseline stats ──
baseline = {
    "n": len(top5),
    "mean_ret": top5["label_value"].mean(),
    "median_ret": top5["label_value"].median(),
    "ret<0": (top5["label_value"] < 0).mean(),
    "ret<0.1": (top5["label_value"] < 0.1).mean(),
    "ret>0.3": (top5["label_value"] > 0.3).mean(),
    "ret>0.6": (top5["label_value"] > 0.6).mean(),
    "ret>1.0": (top5["label_value"] > 1.0).mean(),
    "bad_fp_capture": 1.0,
    "big_win_loss": 1.0,
    "net_selectivity": 0.0,
}

n_bad_fp = (top5["label_value"] < 0.1).sum()
n_big_win = (top5["label_value"] > 0.6).sum()
print(f"\nBaseline top5: n={baseline['n']}, mean_ret={baseline['mean_ret']:.4f}, "
      f"bad_fp={n_bad_fp}, big_win={n_big_win}")

results = [baseline]
row_list = []

for rule_name, condition in RULES:
    hit = top5[condition].copy()
    if len(hit) < 5:
        print(f"  {rule_name:45s}: coverage={condition.mean():.1%} (<5 samples, skip)")
        continue

    cov = condition.mean()
    bad_fp_captured = hit["bad_fp"].sum()
    big_win_captured = hit["big_win"].sum()

    r = {
        "rule": rule_name,
        "coverage": cov,
        "n": len(hit),
        "mean_ret": hit["label_value"].mean(),
        "median_ret": hit["label_value"].median(),
        "ret<0": (hit["label_value"] < 0).mean(),
        "ret<0.1": (hit["label_value"] < 0.1).mean(),
        "ret>0.3": (hit["label_value"] > 0.3).mean(),
        "ret>0.6": (hit["label_value"] > 0.6).mean(),
        "ret>1.0": (hit["label_value"] > 1.0).mean(),
        "bad_fp_capture": bad_fp_captured / max(n_bad_fp, 1),
        "big_win_loss": big_win_captured / max(n_big_win, 1),
        "net_selectivity": bad_fp_captured / max(n_bad_fp, 1) - big_win_captured / max(n_big_win, 1),
    }
    results.append(r)

    # Collect representative samples
    bad_hit = hit[hit["label_value"] < 0].nsmallest(5, "label_value")
    for _, rr in bad_hit.iterrows():
        row_list.append({"rule": rule_name, "type": "bad_case",
            "trade_date": rr["trade_date"], "name": rr.get("name", ""), "instrument": rr["instrument"],
            "industry": rr.get("industry", ""), "score": round(rr["score"], 2),
            "ret": round(rr["label_value"], 3),
            "al_rank": round(rr.get("al_rank_pct", 0), 3) if pd.notna(rr.get("al_rank_pct")) else pd.NA,
            "pe_rank": round(rr.get("pe_rank_252d", 0), 3) if pd.notna(rr.get("pe_rank_252d")) else pd.NA,
            "pp": round(rr.get("price_percentile_252d", 0), 3) if pd.notna(rr.get("price_percentile_252d")) else pd.NA,
            "rps": round(rr.get("rps_60d", 0), 3) if pd.notna(rr.get("rps_60d")) else pd.NA})
    big_hit = hit[hit["label_value"] > 0.6].nlargest(5, "label_value")
    for _, rr in big_hit.iterrows():
        row_list.append({"rule": rule_name, "type": "big_win",
            "trade_date": rr["trade_date"], "name": rr.get("name", ""), "instrument": rr["instrument"],
            "industry": rr.get("industry", ""), "score": round(rr["score"], 2),
            "ret": round(rr["label_value"], 3),
            "al_rank": round(rr.get("al_rank_pct", 0), 3) if pd.notna(rr.get("al_rank_pct")) else pd.NA,
            "pe_rank": round(rr.get("pe_rank_252d", 0), 3) if pd.notna(rr.get("pe_rank_252d")) else pd.NA,
            "pp": round(rr.get("price_percentile_252d", 0), 3) if pd.notna(rr.get("price_percentile_252d")) else pd.NA,
            "rps": round(rr.get("rps_60d", 0), 3) if pd.notna(rr.get("rps_60d")) else pd.NA})

    print(f"  {rule_name:45s}: cov={cov:.1%} n={len(hit):4d} mean_ret={r['mean_ret']:+.4f} "
          f"ret<0.1={r['ret<0.1']:.1%} bad_fp_cap={r['bad_fp_capture']:.1%} "
          f"bw_loss={r['big_win_loss']:.1%} net={r['net_selectivity']:+.1%}")

# ── Save CSV ──
df_r = pd.DataFrame(results)
df_r.to_csv(OUT_DIR / f"liquidity_overheat_rule_validation_{TODAY}.csv", index=False, float_format="%.4f")
print(f"\n✅ Saved: {OUT_DIR / f'liquidity_overheat_rule_validation_{TODAY}.csv'}")

df_samples = pd.DataFrame(row_list)
df_samples.to_csv(OUT_DIR / f"liquidity_overheat_rule_samples_{TODAY}.csv", index=False, float_format="%.4f")
print(f"✅ Saved: {OUT_DIR / f'liquidity_overheat_rule_samples_{TODAY}.csv'}")

# ── Find best rule ──
print("\n" + "=" * 70)
print("BEST RULES (by net_selectivity)")
print("=" * 70)
df_sorted = df_r[df_r["rule"] != "baseline"].sort_values("net_selectivity", ascending=False)
for _, r in df_sorted[df_sorted["net_selectivity"] > 0].head(10).iterrows():
    print(f"  {r['rule']:45s}: net={r['net_selectivity']:+.1%} "
          f"bad_fp_cap={r['bad_fp_capture']:.1%} bw_loss={r['big_win_loss']:.1%} "
          f"cov={r['coverage']:.1%} mean_ret={r['mean_ret']:+.4f}")

print("\n" + "=" * 70)
print("CONCLUSION")
print("=" * 70)
# Find the best rule
best = df_r[df_r["rule"] != "baseline"].sort_values("net_selectivity", ascending=False).iloc[0] if len(df_r) > 1 else None
if best is not None and best["net_selectivity"] > 0.03:
    print(f"  ✅ Best rule: {best['rule']}")
    print(f"     Net selectivity: {best['net_selectivity']:+.1%}")
    print(f"     Bad FP captured: {best['bad_fp_capture']:.1%}")
    print(f"     Big win lost:    {best['big_win_loss']:.1%}")
    print(f"     Coverage:        {best['coverage']:.1%}")
    print(f"     Mean ret of hit: {best['mean_ret']:+.4f}")
    if best["net_selectivity"] > 0.05:
        print("  ✅ Worth implementing as a feature")
    else:
        print("  ⚠️  Moderate selectivity — consider if combined")
else:
    print("  ❌ No rule achieves meaningful net selectivity")
    print("     All rules lose too many big winners relative to bad FP captured")
    print("     → Do not implement overheat feature at this stage")
