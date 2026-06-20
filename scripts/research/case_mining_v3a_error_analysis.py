#!/usr/bin/env python3
"""v3a_full case mining and error analysis.

Usage:
    python scripts/research/case_mining_v3a_error_analysis.py --horizon 60
    python scripts/research/case_mining_v3a_error_analysis.py --horizon 180
"""
import argparse, sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pandas as pd, numpy as np
from qsys.data.adapter import QlibAdapter

def get_signal_path(horizon):
    if horizon == 60:
        runs = sorted(Path("data/research/signals/fwd_ret_60d_raw__daily_zscore").glob("rolling__v3a_full_60d_delayed__*"))
        if not runs: runs = sorted(Path("data/research/signals/fwd_ret_60d_raw__daily_zscore").glob("rolling__v3a_alpha_60d_delayed__*"))
    else:
        runs = sorted(Path("data/research/signals/fwd_ret_180d_raw__daily_zscore").glob("rolling__v3a_fl_delayed__*"))
    return runs[0] / "predictions.parquet" if runs else None

def label_path(horizon):
    p = Path(f"data/research/labels/fwd_ret_{horizon}d_raw/labels.parquet")
    return p if p.exists() else None

def load_feature_matrix(horizon):
    QlibAdapter().init_qlib()
    from qlib.data import D
    fields = [
        "$close", "ret_20d", "ret_60d", "ret_120d", "rps_60d", "rps_120d",
        "roe", "revenue_yoy", "profit_yoy", "grossprofit_margin", "net_margin",
        "debt_to_assets", "op_cashflow", "operating_cf_to_profit", "ocf_margin", "inventory_yoy",
        "margin_eligible", "margin_balance_to_float_mv", "margin_balance_chg_20d", "margin_crowding_score",
        "holder_num_chg_qoq", "top10_holder_ratio_chg_qoq", "holder_concentration_score",
        "holder_squeeze_score", "holder_num_stale_days", "top10_holder_stale_days",
    ]
    try:
        raw = D.features(D.instruments("csi800")[:100], ["$close"], start_time="2025-01-01", end_time="2025-01-10")
        return None
    except:
        return None

def compute_quantiles(s):
    """Return percentile rank (0-100) per group."""
    return s.groupby(pd.to_datetime(s.name).index if hasattr(s.name, 'index') else s.index).rank(pct=True) * 100

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--horizon", type=int, choices=[60, 180], required=True)
    p.add_argument("--output-dir", default="artifacts/case_mining/v3a_error_analysis")
    args = p.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    sig_path = get_signal_path(args.horizon)
    lab_path = label_path(args.horizon)
    if not sig_path or not sig_path.exists():
        print(f"Signal not found for {args.horizon}d, checking alternatives...")
        return

    print(f"Loading signal: {sig_path}")
    sig = pd.read_parquet(sig_path)
    sig["trade_date"] = pd.to_datetime(sig["trade_date"])
    print(f"  {len(sig)} rows, {sig['trade_date'].nunique()} dates")

    print(f"Loading label: {lab_path}")
    lbl = pd.read_parquet(lab_path)
    lbl["trade_date"] = pd.to_datetime(lbl["trade_date"])
    lbl = lbl[["trade_date", "instrument", "label_value"]].rename(columns={"label_value": "label"})
    print(f"  {len(lbl)} rows")

    # Merge
    m = sig.merge(lbl, on=["trade_date", "instrument"], how="inner").dropna(subset=["label", "score"])
    print(f"Merged: {len(m)} rows")

    # Per-date quantiles
    m["score_pct"] = m.groupby("trade_date")["score"].rank(pct=True)
    m["label_pct"] = m.groupby("trade_date")["label"].rank(pct=True)

    # Case definitions
    tp = m[(m["score_pct"] >= 0.90) & (m["label_pct"] >= 0.80)].copy()
    fp = m[(m["score_pct"] >= 0.90) & (m["label_pct"] <= 0.30)].copy()
    fn = m[(m["score_pct"] <= 0.50) & (m["label_pct"] >= 0.90)].copy()
    tn = m[(m["score_pct"] <= 0.30) & (m["label_pct"] <= 0.30)].copy()

    tp["case"] = "true_positive"
    fp["case"] = "false_positive"
    fn["case"] = "false_negative"
    tn["case"] = "true_negative"

    cases = {"true_positive": tp, "false_positive": fp, "false_negative": fn, "true_negative": tn}

    print(f"\nCase counts ({args.horizon}d):")
    total = len(m)
    for name, df in cases.items():
        print(f"  {name}: {len(df)} ({len(df)/total*100:.1f}%)")
        f = out_dir / f"{args.horizon}d_{name}.csv"
        df.to_csv(f, index=False)
        print(f"    → {f}")

    # Segment IC
    print("\nSegment IC...")
    segments = []
    # By year
    m["year"] = m["trade_date"].dt.year
    for yr in sorted(m["year"].unique()):
        sub = m[m["year"] == yr]
        ic = sub["score"].corr(sub["label"], method="spearman")
        segments.append({"horizon": args.horizon, "segment": "year", "value": str(yr),
                         "n": len(sub), "ic": f"{ic:.4f}"})
    # Overall
    ic = m["score"].corr(m["label"], method="spearman")
    segments.append({"horizon": args.horizon, "segment": "overall", "value": "all",
                     "n": len(m), "ic": f"{ic:.4f}"})

    seg_df = pd.DataFrame(segments)
    seg_f = out_dir / f"segment_ic_{args.horizon}d.csv"
    seg_df.to_csv(seg_f, index=False)
    print(f"  → {seg_f}")

    # False positive tagging
    print("\nTagging false positives...")
    fp_tags = []
    # Load feature values for FP
    fp_label = lbl.merge(fp[["trade_date", "instrument", "score"]], on=["trade_date", "instrument"], how="inner")
    for _, row in fp.iterrows():
        tags = []
        # Simple heuristic tags based on available signal data
        tags.append("high_score_low_return")
        fp_tags.append({"trade_date": row["trade_date"], "ts_code": row["instrument"],
                        "score": row["score"], "label": row["label"], "tags": "|".join(tags),
                        "primary_reason": "high_score_but_low_return", "supporting": ""})

    tag_df = pd.DataFrame(fp_tags)
    tag_f = out_dir / f"false_positive_tags_{args.horizon}d.csv"
    tag_df.to_csv(tag_f, index=False)

    # False negative tagging
    print("Tagging false negatives...")
    fn_tags = []
    for _, row in fn.iterrows():
        fn_tags.append({"trade_date": row["trade_date"], "ts_code": row["instrument"],
                        "score": row["score"], "label": row["label"], "tags": "low_score_high_return",
                        "primary_reason": "low_score_but_high_return", "supporting": ""})
    fn_tag_df = pd.DataFrame(fn_tags)
    fn_tag_f = out_dir / f"false_negative_tags_{args.horizon}d.csv"
    fn_tag_df.to_csv(fn_tag_f, index=False)

    print(f"\nArtifacts in {out_dir}:")
    for f in sorted(out_dir.glob(f"*{args.horizon}d*")):
        print(f"  {f} ({f.stat().st_size/1024:.0f} KB)")

if __name__ == "__main__":
    main()
