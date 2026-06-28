#!/usr/bin/env python3
"""Case mining: v3a+liquidity bad false positives and missed big winners.

Usage:
    python scripts/research/case_mining_liquidity_bad_cases.py

Reads:
    - Rolling predictions (180d v3a+liq, plus v3a_full for diff)
    - FeatureStore cached features (amount_log, rps_60d, ret_60d, ...)
    - LabelStore labels (fwd_ret_180d_raw, fwd_ret_60d_raw)
    - meta.db for stock name/industry

Outputs (to reports/research/):
    - liquidity_bad_case_mining_YYYYMMDD.md
    - liquidity_bad_case_samples_YYYYMMDD.csv
    - liquidity_bad_case_group_stats_YYYYMMDD.csv
"""

import sys, json, hashlib, warnings
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
warnings.filterwarnings("ignore")

# ═══════════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════════

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "reports" / "research"
OUT_DIR.mkdir(parents=True, exist_ok=True)
TODAY = datetime.now().strftime("%Y%m%d")

PRED_180D_LIQ = (
    "data/research/signals/fwd_ret_180d_raw__daily_zscore/"
    "rolling__180d_v3a_plus_liquidity__v3a_plus_liquidity_180d__"
    "fwd_ret_180d_raw__daily_zscore__2020-01-01_2025-12-31/predictions.parquet"
)
PRED_180D_FULL = (
    "data/research/signals/fwd_ret_180d_raw__daily_zscore/"
    "rolling__180d_v3a_full__v3a_full_180d__"
    "fwd_ret_180d_raw__daily_zscore__2020-01-01_2025-12-31/predictions.parquet"
)
PRED_60D_LIQ = (
    "data/research/signals/fwd_ret_60d_raw__daily_zscore/"
    "rolling__60d_v3a_plus_liquidity_indadj__v3a_liq_indadj_60d__"
    "fwd_ret_60d_raw__daily_zscore__2020-01-01_2025-12-31/predictions.parquet"
)
LABEL_60D = "fwd_ret_60d_raw"
LABEL_180D = "fwd_ret_180d_raw"

# Feature fields we try to load from FeatureStore
WANTED_FEATURES = [
    "amount_log", "amount_zscore_20", "illiquidity",
    "rps_60d", "ret_60d", "ret_120d", "price_percentile_252d",
    "pe_rank_252d", "pb_rank_252d", "trend_smoothness_60d",
    "amount_log_ind_zscore",
]

RET_BUCKET_LABELS = ["bad", "flat", "weak_pos", "good", "big_win", "super_win"]


def bucket_ret(r: float) -> str:
    if r < 0:
        return "bad"
    elif r < 0.10:
        return "flat"
    elif r < 0.30:
        return "weak_pos"
    elif r < 0.60:
        return "good"
    elif r < 1.00:
        return "big_win"
    else:
        return "super_win"


# ═══════════════════════════════════════════════════════════════════
# Data loading
# ═══════════════════════════════════════════════════════════════════


def load_predictions(path: str, model_label: str) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df["model"] = model_label
    return df[["trade_date", "instrument", "score", "model"]]


def load_label(label_id: str) -> pd.DataFrame:
    from qsys.label.store import LabelStore
    return LabelStore().load_labels(label_id)[["trade_date", "instrument", "label_value"]]


def load_features() -> dict[str, pd.DataFrame]:
    """Load features from FeatureStore, return dict {feature_id: df}."""
    from qsys.feature.feature_store import FeatureStore, FeatureCacheKey, compute_feature_cache_key
    from qsys.feature.feature_compute_registry import _PHASE1_HASH
    import json as _json

    # Read source hash from any cached feature
    meta_files = list(Path("data/feature_cache/features/amount_log").glob("*.meta.json"))
    source_hash = ""
    if meta_files:
        meta = _json.loads(meta_files[0].read_text())
        source_hash = meta["source_manifest_hash"]

    store = FeatureStore()
    result = {}
    missing = []
    for feat in WANTED_FEATURES:
        fk = FeatureCacheKey(
            feature_id=feat, universe="csi800",
            source_manifest_hash=source_hash,
            compute_fn_hash=_PHASE1_HASH, pit_policy="rolling_past",
        )
        ck = compute_feature_cache_key(fk)
        if store.exists(feat, ck):
            df = store.read_feature(feat, expected_cache_key=ck, strict_source_hash=source_hash)
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
            result[feat] = df[["trade_date", "ts_code", feat]].rename(columns={"ts_code": "instrument"})
        else:
            missing.append(feat)
    if missing:
        print(f"[WARN] Features not in cache: {missing}")
    return result


def load_industry() -> pd.DataFrame:
    import sqlite3
    conn = sqlite3.connect(str(REPO / "data" / "meta.db"))
    df = pd.read_sql("select ts_code, name, industry from stock_basic", conn)
    conn.close()
    return df.rename(columns={"ts_code": "instrument"})


# ═══════════════════════════════════════════════════════════════════
# Monthly unique stock (keep highest score per stock per month)
# ═══════════════════════════════════════════════════════════════════


def monthly_unique(df: pd.DataFrame) -> pd.DataFrame:
    """Per stock per month: keep row with highest score."""
    df = df.copy()
    df["ym"] = df["trade_date"].str[:7]
    idx = df.groupby(["instrument", "ym"])["score"].idxmax()
    return df.loc[idx].reset_index(drop=True)


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("Case Mining: v3a+liquidity bad false positives")
    print("=" * 70)

    # ── 1. Load data ──
    print("\n[1] Loading predictions...")
    p_liq_180d = load_predictions(str(REPO / PRED_180D_LIQ), "v3a+liq")
    p_full_180d = load_predictions(str(REPO / PRED_180D_FULL), "v3a_full")
    p_liq_60d = load_predictions(str(REPO / PRED_60D_LIQ), "v3a+liq")
    print(f"  180d v3a+liq: {len(p_liq_180d)} rows")
    print(f"  180d v3a_full: {len(p_full_180d)} rows")
    print(f"  60d v3a+liq:  {len(p_liq_60d)} rows")

    # Merge v3a_full into v3a+liq for liquidity diff
    p_180d = p_liq_180d.merge(
        p_full_180d[["trade_date", "instrument", "score"]].rename(columns={"score": "score_full"}),
        on=["trade_date", "instrument"], how="left"
    )
    p_180d["liq_diff"] = p_180d["score"] - p_180d["score_full"]

    print("\n[2] Loading labels...")
    label_180d = load_label(LABEL_180D)
    label_60d = load_label(LABEL_60D)
    print(f"  180d label: {len(label_180d)}")
    print(f"  60d label:  {len(label_60d)}")

    print("\n[3] Loading features...")
    features = load_features()
    print(f"  Loaded: {list(features.keys())}")

    print("\n[4] Loading industry info...")
    stock_info = load_industry()
    print(f"  Stocks: {len(stock_info)}")

    # ── 2. Build analysis dataset: 180d ──
    print("\n[5] Building analysis dataset...")
    adf = p_180d.merge(label_180d, on=["trade_date", "instrument"], how="inner")
    adf = adf.merge(stock_info, on="instrument", how="left")
    for feat_name, feat_df in features.items():
        adf = adf.merge(feat_df, on=["trade_date", "instrument"], how="left")
    adf["ret_bucket"] = adf["label_value"].apply(bucket_ret)
    adf["score_rank_pct"] = adf.groupby("trade_date")["score"].rank(pct=True)
    print(f"  Analysis dataset: {len(adf)} rows")

    # Monthly unique
    mdf = monthly_unique(adf)
    print(f"  Monthly unique: {len(mdf)} rows")

    # ── 3. Report 1: TopK quality ──
    print("\n" + "=" * 70)
    print("REPORT 1: TopK Overall Quality")
    print("=" * 70)
    results_1 = []
    for df, label_name in [(adf, "daily"), (mdf, "monthly_unique")]:
        for top_pct in [1, 5, 10]:
            cutoff = df["score"].quantile(1 - top_pct / 100)
            sub = df[df["score"] >= cutoff]
            r = {
                "dataset": label_name, "top": f"{top_pct}%", "n": len(sub),
                "mean_ret": sub["label_value"].mean(),
                "median_ret": sub["label_value"].median(),
                "ret>0": (sub["label_value"] > 0).mean(),
                "ret>0.1": (sub["label_value"] > 0.10).mean(),
                "ret>0.3": (sub["label_value"] > 0.30).mean(),
                "ret>0.6": (sub["label_value"] > 0.60).mean(),
                "ret>1.0": (sub["label_value"] > 1.00).mean(),
                "ret<0": (sub["label_value"] < 0).mean(),
            }
            results_1.append(r)
            print(f"  {label_name:>15s} top {top_pct:>2d}%: n={r['n']:>6d} mean_ret={r['mean_ret']:+.4f} "
                  f"ret>0={r['ret>0']:.1%} ret>0.3={r['ret>0.3']:.1%} ret<0={r['ret<0']:.1%}")
    df_r1 = pd.DataFrame(results_1)
    df_r1.to_csv(OUT_DIR / f"report1_topk_quality_{TODAY}.csv", index=False)

    # ── 4. Report 2: Bad false positive distribution ──
    print("\n" + "=" * 70)
    print("REPORT 2: Bad False Positive Distribution")
    print("=" * 70)
    top5 = adf[adf["score_rank_pct"] >= 0.95].copy()
    fp = top5[top5["label_value"] < 0.10].copy()
    print(f"  Top5 total: {len(top5)}, bad+flat (ret<0.1): {len(fp)} ({len(fp)/len(top5):.1%})")

    # Group by industry
    print("\n  --- By Industry ---")
    ind_fp = fp.groupby("industry").agg(
        n=("label_value", "size"), mean_ret=("label_value", "mean"),
        bad_rate=("ret_bucket", lambda s: (s == "bad").mean()),
    ).sort_values("n", ascending=False)
    top5_ind = top5.groupby("industry").agg(n_total=("label_value", "size"))
    ind_summary = ind_fp.join(top5_ind).sort_values("n", ascending=False)
    ind_summary["fp_rate"] = ind_summary["n"] / ind_summary["n_total"]
    for idx, row in ind_summary.head(15).iterrows():
        print(f"    {idx:<20s}: n={int(row['n']):4d}/{int(row['n_total']):4d} "
              f"fp_rate={row['fp_rate']:.1%} mean_ret={row['mean_ret']:+.3f} "
              f"bad_rate={row['bad_rate']:.1%}")

    # ── 5. Report 3: bad false positive vs big winner feature contrast ──
    print("\n" + "=" * 70)
    print("REPORT 3: Bad FP vs Big Winner Feature Contrast (score top5%)")
    print("=" * 70)
    bad = top5[top5["label_value"] < 0.1].copy()
    big = top5[top5["label_value"] > 0.6].copy()
    print(f"  Bad/Flat (ret<0.1): n={len(bad)}")
    print(f"  Big Win  (ret>0.6): n={len(big)}")

    contrast_cols = [c for c in WANTED_FEATURES if c in adf.columns]
    print(f"\n  {'Feature':<30s} {'Bad_mean':>10s} {'Big_mean':>10s} {'Diff':>10s} {'Bad_median':>10s} {'Big_median':>10s}")
    for col in contrast_cols:
        bm = bad[col].mean()
        gm = big[col].mean()
        bmed = bad[col].median()
        gmed = big[col].median()
        diff = bm - gm
        print(f"  {col:<30s} {bm:>+10.3f} {gm:>+10.3f} {diff:>+10.3f} {bmed:>+10.3f} {gmed:>+10.3f}")

    # Also check amount_log mean/median
    print(f"\n  --- amount_log distribution ---")
    for pct in [10, 25, 50, 75, 90]:
        print(f"    P{pct:>2d}: bad={bad['amount_log'].quantile(pct/100):.2f} "
              f"big={big['amount_log'].quantile(pct/100):.2f}")

    # ── 6. Report 4: Candidate diagnostic rules ──
    print("\n" + "=" * 70)
    print("REPORT 4: Candidate General Idea — Lightweight Validation")
    print("=" * 70)

    # Helper: test a rule
    def test_rule(rule_name: str, condition: pd.Series, top5_df: pd.DataFrame):
        hit = top5_df[condition].copy()
        if len(hit) < 5:
            print(f"  {rule_name:35s}: coverage={condition.mean():.1%} (<5 samples, skip)")
            return
        all_top5 = top5_df
        print(f"\n  --- {rule_name} ---")
        print(f"  coverage: {len(hit):5d}/{len(all_top5):5d} ({len(hit)/len(all_top5):.1%})")
        print(f"  mean_ret: {hit['label_value'].mean():+.4f} (vs top5 avg {all_top5['label_value'].mean():+.4f})")
        print(f"  ret<0:    { (hit['label_value']<0).mean():.1%} (vs top5 avg {(all_top5['label_value']<0).mean():.1%})")
        print(f"  ret<0.1:  { (hit['label_value']<0.10).mean():.1%} (vs top5 avg {(all_top5['label_value']<0.10).mean():.1%})")
        print(f"  ret>0.3:  { (hit['label_value']>0.30).mean():.1%} (vs top5 avg {(all_top5['label_value']>0.30).mean():.1%})")
        print(f"  ret>0.6:  { (hit['label_value']>0.60).mean():.1%} (vs top5 avg {(all_top5['label_value']>0.60).mean():.1%})")
        print(f"  ret>1.0:  { (hit['label_value']>1.00).mean():.1%} (vs top5 avg {(all_top5['label_value']>1.00).mean():.1%})")
        # Top bad cases
        bad_hits = hit[hit["label_value"] < 0].nsmallest(5, "label_value")
        print(f"  Worst bad cases:")
        for _, r in bad_hits.iterrows():
            print(f"    {r['trade_date']} {r.get('name',''):<10s} {r['instrument']}: ret={r['label_value']:+.3f}")
        good_hits = hit[hit["label_value"] > 0.6].nlargest(5, "label_value")
        print(f"  Best big wins:")
        for _, r in good_hits.iterrows():
            print(f"    {r['trade_date']} {r.get('name',''):<10s} {r['instrument']}: ret={r['label_value']:+.3f}")

    # Idea A: high amount + unconfirmed by trend
    if all(c in top5.columns for c in ["amount_log", "rps_60d", "pe_rank_252d"]):
        al_high = top5["amount_log"] >= top5["amount_log"].quantile(0.7)
        rps_low = top5["rps_60d"] <= top5["rps_60d"].quantile(0.3)
        pe_low = top5["pe_rank_252d"] <= top5["pe_rank_252d"].quantile(0.3)
        test_rule("A: high_amount + weak_rps", al_high & rps_low, top5)
        test_rule("A2: high_amount + weak_rps + cheap", al_high & rps_low & pe_low, top5)

    # Idea B: high amount overheated
    if all(c in top5.columns for c in ["amount_log", "price_percentile_252d"]):
        al_high = top5["amount_log"] >= top5["amount_log"].quantile(0.7)
        pp_high = top5["price_percentile_252d"] >= 0.8
        test_rule("B: high_amount + near_high", al_high & pp_high, top5)

    # Idea C: amount confirmed by trend
    if all(c in top5.columns for c in ["amount_log", "rps_60d"]):
        al_high = top5["amount_log"] >= top5["amount_log"].quantile(0.7)
        rps_high = top5["rps_60d"] >= 0.7
        test_rule("C: high_amount + strong_rps", al_high & rps_high, top5)

    # Idea D: amount confirmed by quality
    if "pe_rank_252d" in top5.columns:
        al_high = top5["amount_log"] >= top5["amount_log"].quantile(0.7)
        pe_high = top5["pe_rank_252d"] >= 0.7
        test_rule("D: high_amount + high_pe_rank", al_high & pe_high, top5)

    # Idea E: high amount defensive (low price_percentile)
    if all(c in top5.columns for c in ["amount_log", "price_percentile_252d", "ret_120d"]):
        al_high = top5["amount_log"] >= top5["amount_log"].quantile(0.7)
        pp_low = top5["price_percentile_252d"] <= 0.3
        # Also add ret_120d low
        r120_low = top5["ret_120d"] <= top5["ret_120d"].quantile(0.3)
        test_rule("E: high_amount + deep_value", al_high & pp_low & r120_low, top5)

    # ── 7. Report 5: Concrete case lists ──
    print("\n" + "=" * 70)
    print("REPORT 5: Concrete Case Lists")
    print("=" * 70)

    case_sets = {
        "high_score_bad_false_positive": adf[(adf["score_rank_pct"] >= 0.95) & (adf["label_value"] < 0)].nsmallest(20, "label_value"),
        "high_score_flat_weak": adf[(adf["score_rank_pct"] >= 0.95) & (adf["label_value"] >= 0) & (adf["label_value"] < 0.1)].nlargest(20, "score"),
        "high_score_big_win": adf[(adf["score_rank_pct"] >= 0.95) & (adf["label_value"] > 0.6)].nlargest(20, "label_value"),
        "liq_boosted_bad": adf[(adf["liq_diff"] > 0.5) & (adf["score_rank_pct"] >= 0.95) & (adf["label_value"] < 0)].nsmallest(20, "label_value"),
        "liq_boosted_big_win": adf[(adf["liq_diff"] > 0.5) & (adf["score_rank_pct"] >= 0.95) & (adf["label_value"] > 0.6)].nlargest(20, "label_value"),
        "missed_super_win": adf[(adf["label_value"] > 1.0) & (adf["score_rank_pct"] < 0.5)].nlargest(20, "label_value"),
    }

    case_rows = []
    for set_name, cases in case_sets.items():
        print(f"\n  --- {set_name} (n={len(cases)}) ---")
        for _, r in cases.iterrows():
            al_rank = ""
            if pd.notna(r.get("amount_log")):
                # Compute rank within this date
                date_al_rank = adf[adf["trade_date"] == r["trade_date"]]["amount_log"].rank(pct=True)
                al_rank_val = date_al_rank.loc[adf["trade_date"] == r["trade_date"]].iloc[0] if len(date_al_rank) > 0 else pd.NA
                al_rank = f"al_rank={al_rank_val:.0%}" if pd.notna(al_rank_val) else ""

            rps = f"rps={r.get('rps_60d', ''):.0%}" if pd.notna(r.get('rps_60d')) else ""
            pp = f"pp252={r.get('price_percentile_252d', ''):.0%}" if pd.notna(r.get('price_percentile_252d')) else ""
            ld = f"liq_diff={r.get('liq_diff', 0):+.1f}" if pd.notna(r.get('liq_diff')) else ""

            case_rows.append({
                "set": set_name, "trade_date": r["trade_date"],
                "name": r.get("name", ""), "instrument": r["instrument"],
                "industry": r.get("industry", ""),
                "score": round(r["score"], 2), "ret": round(r["label_value"], 3),
                "liq_diff": round(r.get("liq_diff", 0), 2) if pd.notna(r.get("liq_diff")) else 0,
                "amount_log": round(r.get("amount_log", 0), 2) if pd.notna(r.get("amount_log")) else pd.NA,
                "rps_60d": round(r.get("rps_60d", 0), 3) if pd.notna(r.get("rps_60d")) else pd.NA,
                "price_percentile_252d": round(r.get("price_percentile_252d", 0), 3) if pd.notna(r.get("price_percentile_252d")) else pd.NA,
                "ret_60d": round(r.get("ret_60d", 0), 3) if pd.notna(r.get("ret_60d")) else pd.NA,
            })
            al_rank_str = f", al_rank={r['amount_log_rank_pct']:.0%}" if 'amount_log_rank_pct' in r.index and pd.notna(r.get('amount_log_rank_pct')) else ""
            print(f"    {r['trade_date']} {str(r.get('name',''))[:8]:<8s} {r['instrument']}: "
                  f"score={r['score']:+.2f} ret={r['label_value']:+.3f} {al_rank_str}")

    df_cases = pd.DataFrame(case_rows)
    df_cases.to_csv(OUT_DIR / f"liquidity_bad_case_samples_{TODAY}.csv", index=False)
    print(f"\n✅ Case samples saved: {OUT_DIR / f'liquidity_bad_case_samples_{TODAY}.csv'}")

    # ── 8. Group stats CSV ──
    print("\n" + "=" * 70)
    print("Saving group stats...")
    group_rows = []
    for df, label_name in [(adf, "daily"), (mdf, "monthly")]:
        for col in ["industry"]:
            grp = df.groupby(col)
            for name, sub in grp:
                top5_sub = sub[sub["score_rank_pct"] >= 0.95]
                if len(top5_sub) < 10:
                    continue
                fp5 = top5_sub[top5_sub["label_value"] < 0.1]
                bw5 = top5_sub[top5_sub["label_value"] > 0.6]
                group_rows.append({
                    "dataset": label_name, "group": str(name),
                    "total_n": len(sub), "top5_n": len(top5_sub),
                    "top5_mean_ret": top5_sub["label_value"].mean(),
                    "top5_fp_rate": len(fp5) / max(len(top5_sub), 1),
                    "top5_bw_rate": len(bw5) / max(len(top5_sub), 1),
                })

    df_grp = pd.DataFrame(group_rows)
    df_grp.to_csv(OUT_DIR / f"liquidity_bad_case_group_stats_{TODAY}.csv", index=False)
    print(f"✅ Group stats saved: {OUT_DIR / f'liquidity_bad_case_group_stats_{TODAY}.csv'}")

    # ── 9. Print summary conclusions ──
    print("\n" + "=" * 70)
    print("RESEARCH CONCLUSION (DRAFT)")
    print("=" * 70)
    print("""
1. Current false positive pattern (to be filled after reviewing Report 2/3):
   - Industry concentration:
   - amount_log profile:
   - RPS profile:

2. Best diagnostic rule found (from Report 4):
   - Rule:
   - Effect on bad_rate:
   - Effect on big_win_rate:

3. Recommended features (0-5):
   -

4. If no stable correction found:
   -
""")

    print(f"\n✅ All reports saved to {OUT_DIR}/")
    print("Done.")


if __name__ == "__main__":
    main()
