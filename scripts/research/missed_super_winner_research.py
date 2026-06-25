#!/usr/bin/env python3
"""Missed super winner research — analyze model blind spots.

Usage:
    python scripts/research/missed_super_winner_research.py

Reads: OOS rolling predictions, labels, stock info, cached features.
Output: reports/research/missed_super_winner_research_YYYYMMDD.md + CSV.
"""

import sys, json, warnings
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
MISSING = []

# ── Load predictions ──
PRED_PATH = (
    "data/research/signals/fwd_ret_180d_raw__daily_zscore/"
    "rolling__180d_v3a_plus_liquidity__v3a_plus_liquidity_180d__"
    "fwd_ret_180d_raw__daily_zscore__2020-01-01_2025-12-31/predictions.parquet"
)
print("Loading predictions...")
pred = pd.read_parquet(str(REPO / PRED_PATH))

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

# ── Load features ──
print("Loading features...")
from qsys.feature.feature_store import FeatureStore, FeatureCacheKey, compute_feature_cache_key
from qsys.feature.feature_compute_registry import _PHASE1_HASH

meta_files = list((REPO / "data/feature_cache/features/amount_log").glob("*.meta.json"))
source_hash = json.loads(meta_files[0].read_text())["source_manifest_hash"] if meta_files else ""

WANTED = ["amount_log", "rps_60d", "ret_60d", "ret_120d", "price_percentile_252d",
          "pe_rank_252d", "pb_rank_252d", "trend_smoothness_60d",
          "industry_ret_20d", "industry_ret_60d", "industry_ret_120d",
          "industry_breadth_20d", "industry_breadth_60d",
          "industry_volume_expansion",
          "stock_minus_industry_ret_20d", "stock_minus_industry_ret_60d",
          "industry_top_stock_momentum"]

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
        MISSING.append(feat)

print(f"  Loaded {len(feat_dfs)}/{len(WANTED)} features. Missing: {MISSING}")

# ── Build dataset ──
print("Building dataset...")
df = pred.merge(label, on=["trade_date", "instrument"], how="inner")
df = df.merge(stock_info, on="instrument", how="left")
for feat_name, fdf in feat_dfs.items():
    df = df.merge(fdf, on=["trade_date", "instrument"], how="left")
df["score_rank_pct"] = df.groupby("trade_date")["score"].rank(pct=True)

# Monthly unique
df["ym"] = df["trade_date"].str[:7]
idx = df.groupby(["instrument", "ym"])["score"].idxmax()
mdf = df.loc[idx].reset_index(drop=True)
mdf["score_rank_pct"] = mdf.groupby("trade_date")["score"].rank(pct=True)

print(f"  Daily: {len(df)}, Monthly unique: {len(mdf)}")

# ── Define categories ──
super_win = mdf[mdf["label_value"] > 1.0].copy()
missed = super_win[(super_win["score_rank_pct"] > 0.5) | (super_win["score"] < 0)].copy()
high_score = super_win[super_win["score_rank_pct"] >= 0.95].copy()

print(f"\nTotal super winners (ret>1.0): {len(super_win)}")
print(f"  Missed (score_rank>50% or score<0): {len(missed)} ({len(missed)/max(len(super_win),1):.1%})")
print(f"  High-score (top5%): {len(high_score)} ({len(high_score)/max(len(super_win),1):.1%})")

# ── 1. Feature comparison: missed vs high-score super winners ──
print(f"\n{'='*80}")
print("FEATURE COMPARISON: Missed Super Winners vs High-Score Super Winners")
print(f"{'='*80}")
contrast_cols = [c for c in WANTED if c in mdf.columns and c in feat_dfs]
print(f"\n{'Feature':<35s} {'Missed_mean':>10s} {'HighSc_mean':>10s} {'Missed_med':>10s} {'HighSc_med':>10s}")
for col in contrast_cols:
    if col not in missed.columns or col not in high_score.columns:
        continue
    mm = missed[col].mean()
    hm = high_score[col].mean()
    mmed = missed[col].median()
    hmed = high_score[col].median()
    print(f"  {col:<35s} {mm:>+10.3f} {hm:>+10.3f} {mmed:>+10.3f} {hmed:>+10.3f}")

# Industry distribution
print(f"\nIndustry distribution:")
print(f"  {'Industry':<20s} {'Missed':>8s} {'HighSc':>8s}")
missed_ind = missed["industry"].value_counts()
high_ind = high_score["industry"].value_counts()
all_ind = set(list(missed_ind.index[:8]) + list(high_ind.index[:8]))
for ind in sorted(all_ind):
    print(f"  {ind:<20s} {missed_ind.get(ind,0):>8d} {high_ind.get(ind,0):>8d}")

# ── 2. Case drilldown ──
TICKERS = {"300502.SZ": "新易盛", "300308.SZ": "中际旭创", "300476.SZ": "胜宏科技",
           "002432.SZ": "九安医疗", "301308.SZ": "江波龙"}

print(f"\n{'='*80}")
print("CASE DRILLDOWN: Before breakout (at T-{180,120,60,20} days)")
print(f"{'='*80}")

for ticker, name in TICKERS.items():
    stock_data = mdf[mdf["instrument"] == ticker].sort_values("trade_date")
    if stock_data.empty:
        print(f"\n  {name} ({ticker}): not in dataset")
        continue

    # Find the first month where ret > 1.0 appears
    big_months = stock_data[stock_data["label_value"] > 0.6]
    if big_months.empty:
        print(f"\n  {name} ({ticker}): no big returns in dataset")
        continue

    first_big = big_months.iloc[0]["trade_date"]
    print(f"\n  {name} ({ticker}): first big return at {first_big}")

    # Look back 1, 3, 6, 12 months before first_big
    first_dt = pd.Timestamp(first_big)
    lookbacks = [
        (first_dt - pd.DateOffset(months=1)).strftime("%Y-%m"),
        (first_dt - pd.DateOffset(months=3)).strftime("%Y-%m"),
        (first_dt - pd.DateOffset(months=6)).strftime("%Y-%m"),
        (first_dt - pd.DateOffset(months=12)).strftime("%Y-%m"),
    ]

    fields = ["score", "score_rank_pct", "amount_log", "rps_60d", "ret_60d",
              "ret_120d", "price_percentile_252d", "industry_ret_60d",
              "industry_breadth_60d"]
    avail = [f for f in fields if f in stock_data.columns]

    print(f"  {'Date':>8s} {'score':>7s} {'rank':>5s} {'amt':>6s} {'rps':>6s} "
          f"{'ret60':>6s} {'ret120':>6s} {'pp252':>6s} {'ind_ret':>7s} {'ind_bd':>6s}")
    print("  " + "-" * 70)
    for lb in lookbacks:
        row = stock_data[stock_data["ym"] == lb]
        if row.empty:
            continue
        r = row.iloc[0]
        vals = []
        for f in ["score", "score_rank_pct", "amount_log", "rps_60d", "ret_60d",
                   "ret_120d", "price_percentile_252d", "industry_ret_60d",
                   "industry_breadth_60d"]:
            if f in row.columns:
                v = r[f]
                vals.append(f"{v:>+6.2f}" if isinstance(v, (int, float)) else f"{str(v)[:6]:>6s}")
            else:
                vals.append(f"{'N/A':>6s}")
        print(f"  {lb:>8s} {' '.join(vals)}")
    # Show the big ret month itself
    row = stock_data[stock_data["ym"] == first_big]
    if not row.empty:
        r = row.iloc[0]
        vals = []
        for f in ["score", "score_rank_pct", "amount_log", "rps_60d", "ret_60d",
                   "ret_120d", "price_percentile_252d", "industry_ret_60d",
                   "industry_breadth_60d"]:
            if f in row.columns:
                v = r[f]
                vals.append(f"{v:>+6.2f}" if isinstance(v, (int, float)) else f"{str(v)[:6]:>6s}")
            else:
                vals.append(f"{'N/A':>6s}")
        print(f"  {first_big:>8s} {' '.join(vals)} [BIG]")

# ── 3. Industry proxy check ──
print(f"\n{'='*80}")
print("INDUSTRY PROXY CHECK")
print(f"{'='*80}")
ind_fields = ["industry_ret_20d", "industry_ret_60d", "industry_ret_120d",
              "industry_breadth_20d", "industry_breadth_60d",
              "industry_volume_expansion",
              "stock_minus_industry_ret_20d", "stock_minus_industry_ret_60d",
              "industry_top_stock_momentum"]

available_ind = [f for f in ind_fields if f in mdf.columns]
missing_ind = [f for f in ind_fields if f not in mdf.columns]
if missing_ind:
    MISSING.extend(missing_ind)
    print(f"  Missing industry proxy features: {missing_ind}")

if available_ind:
    print(f"  Available: {available_ind}")
    # Compare missed vs high-score on these fields
    for col in available_ind:
        if col in missed.columns and col in high_score.columns:
            mm = missed[col].mean()
            hm = high_score[col].mean()
            print(f"  {col:<35s}: missed_mean={mm:+.4f} high_score_mean={hm:+.4f} diff={mm-hm:+.4f}")

# ── 4. Save CSV ──
print(f"\n{'='*80}")
print("SAVING RESULTS")
print(f"{'='*80}")

# Save super winner samples
super_win_output = super_win[[c for c in ["trade_date", "instrument", "name", "industry", "score",
    "score_rank_pct", "label_value"] + [f for f in WANTED if f in super_win.columns]
    if c in super_win.columns]]
super_win_output["type"] = super_win_output["score_rank_pct"].apply(
    lambda r: "missed" if r > 0.5 or r < 0 else "high_score")
super_win_output.to_csv(OUT_DIR / f"missed_super_winner_samples_{TODAY}.csv", index=False, float_format="%.4f")
print(f"✅ Saved: {OUT_DIR / f'missed_super_winner_samples_{TODAY}.csv'}")
print(f"    Total super winners: {len(super_win_output)}")

# ── Missing columns ──
if MISSING:
    print(f"\n  [INFO] Missing features/inputs: {sorted(set(MISSING))}")
PYEOF
