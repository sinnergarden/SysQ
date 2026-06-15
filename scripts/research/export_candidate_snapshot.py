#!/usr/bin/env python3
"""Export candidate-level feature snapshot for research explanation.

Usage:
    python scripts/research/export_candidate_snapshot.py \\
        --experiment-id value_growth_v2_extended_validation \\
        --date 2025-12-08 \\
        --top-k 100 \\
        --lookback-dates 5 \\
        --output research_outputs/candidate_snapshot.csv

    python scripts/research/export_candidate_snapshot.py \\
        --signal-id fwd_ret_180d_raw__daily_zscore \\
        --run-id rolling__value_growth_v2_extended_validation__v2_ext__fwd_ret_180d_raw__daily_zscore__2013-01-01_2025-12-31 \\
        --feature-list-id value_growth_multibagger_v2_features \\
        --date 2025-12-08 --top-k 100 --lookback-dates 5 --output research_outputs/snapshot.csv

Output schema:
    trade_date, ts_code, name, industry, rank, raw_score, score, score_pct,
    prev_rank, rank_delta, abs_rank_delta,
    in_top20, in_top50, is_new_entry_top20, is_new_entry_top50,
    rank_stability_flag, rank_jump_reason,
    path_type, path_reason,
    <feature columns>, <path score columns>
"""

from __future__ import annotations

import argparse, json, sys, warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from qsys.data.adapter import QlibAdapter
from qsys.data.storage import StockDataStore
from qsys.feature.registry import FeatureListRegistry


def _resolve_run(signal_id: str, run_id: str | None, date: str) -> tuple[pd.DataFrame, str]:
    """Load predictions for *signal_id*, auto-resolve run if needed."""
    base = Path("data/research/signals") / signal_id
    if run_id:
        run_path = base / run_id / "predictions.parquet"
        if not run_path.exists():
            raise FileNotFoundError(f"{run_path}")
        df = pd.read_parquet(run_path)
        return df, run_id
    # pick most recent run with data on *date*
    for rdir in sorted(base.iterdir(), reverse=True):
        p = rdir / "predictions.parquet"
        if not p.exists():
            continue
        df = pd.read_parquet(p)
        if date in set(df["trade_date"].unique()):
            return df, rdir.name
    raise FileNotFoundError(f"No run with date {date} in {base}")


def _load_features(
    instruments: list[str], date: str, feature_list_id: str, horizon: int = 180
) -> pd.DataFrame:
    """Fetch v2 features via QlibAdapter semantic builder path.

    Returns DataFrame with trade_date, instrument + all requested features.
    """
    adapter = QlibAdapter()
    adapter.init_qlib()

    features = FeatureListRegistry.load(feature_list_id)
    lookback = str(pd.Timestamp(date) - pd.Timedelta(days=820))

    raw = adapter.get_features(
        instruments,
        features,
        start_time=lookback,
        end_time=date,
    )
    if raw is None or raw.empty:
        return pd.DataFrame()

    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    frame = frame.loc[:, ~frame.columns.duplicated()]
    frame["trade_date"] = frame["trade_date"].astype(str).str[:10]

    # Filter to target date only (features are backward-looking per trade_date)
    day = frame[frame["trade_date"] == date].copy()
    day["instrument"] = day["instrument"].str.upper()
    return day


def _get_prev_topk(
    signal_df: pd.DataFrame, prev_date: str, top_k: int
) -> dict[str, int]:
    """Return {instrument: rank} for top_k on *prev_date*."""
    sub = signal_df[signal_df["trade_date"] == prev_date].sort_values("score", ascending=False)
    return {r["instrument"]: i + 1 for i, (_, r) in enumerate(sub.head(top_k).iterrows())}


def _classify_path(row: pd.Series, high_q: float, extreme_q: float) -> tuple[str, str]:
    """Classify path using builder-derived path scores (percentile within snapshot)."""
    cs = row.get("continuation_candidate_score")
    rs = row.get("repair_candidate_score")
    oh = row.get("overheat_risk_score")
    vt = row.get("value_trap_risk_score")

    # We'll use the raw scores if present; thresholds are applied after percentile ranking
    # This function is called after percentile columns are computed
    return ("unclear", "path scores unavailable")


def main() -> None:
    p = argparse.ArgumentParser(description="Export candidate feature snapshot")
    p.add_argument("--experiment-id", default=None, help="Resolve run from experiment manifest")
    p.add_argument("--signal-id", default="fwd_ret_180d_raw__daily_zscore")
    p.add_argument("--run-id", default=None)
    p.add_argument("--feature-list-id", default="value_growth_multibagger_v2_features")
    p.add_argument("--date", default=None)
    p.add_argument("--top-k", type=int, default=100)
    p.add_argument("--lookback-dates", type=int, default=5)
    p.add_argument("--output", default=None)
    p.add_argument("--rank-alert-threshold", type=int, default=20)
    p.add_argument("--path-high-quantile", type=float, default=0.70)
    p.add_argument("--risk-extreme-quantile", type=float, default=0.80)
    args = p.parse_args()

    # Resolve run
    if args.experiment_id:
        manifest = Path("data/research/experiments") / args.experiment_id / "signal_research_manifest.json"
        if manifest.exists():
            m = json.loads(manifest.read_text())
            sr = m["signal_runs"][0]
            args.signal_id = sr["signal_id"]
            args.run_id = sr["signal_run_id"]
            if args.date is None:
                args.date = m["date_range"]["end"]
        else:
            print(f"Manifest not found: {manifest}")
            sys.exit(1)

    date = args.date or "2025-12-08"
    print(f"Loading signal: {args.signal_id} / {args.run_id}")
    signal_df, resolved_run = _resolve_run(args.signal_id, args.run_id, date)
    print(f"  Run resolved: {resolved_run}")
    print(f"  Signal rows: {len(signal_df)}, dates: {signal_df['trade_date'].nunique()}")

    # Latest day candidates
    day = signal_df[signal_df["trade_date"] == date].sort_values("score", ascending=False).head(args.top_k).copy()
    insts = day["instrument"].str.upper().tolist()
    print(f"  Candidates: {len(day)}")

    # Stock metadata
    sds = StockDataStore()
    stock_df = sds.get_stock_list()
    name_map = dict(zip(stock_df["ts_code"].str.upper(), stock_df["name"]))
    ind_map = dict(zip(stock_df["ts_code"].str.upper(), stock_df["industry"]))

    day["name"] = day["instrument"].str.upper().map(name_map)
    day["industry"] = day["instrument"].str.upper().map(ind_map)
    day["rank"] = range(1, len(day) + 1)
    day["raw_score"] = day.get("score_raw", day["score"])
    day["score_pct"] = day["score"].rank(pct=True)

    scores = {}
    all_dates = sorted(signal_df["trade_date"].unique())
    date_idx = all_dates.index(date) if date in all_dates else -1
    for li in range(1, min(args.lookback_dates + 1, date_idx + 1)):
        prev_date = all_dates[date_idx - li]
        scores[prev_date] = _get_prev_topk(signal_df, prev_date, args.top_k)

    # Rank stability
    prev_info = list(scores.items())
    if prev_info:
        prev_date, prev_ranks = prev_info[0]
        day["prev_date"] = prev_date
        day["prev_rank"] = day["instrument"].map(prev_ranks)
        day["rank_delta"] = day["prev_rank"] - day["rank"]
        day["abs_rank_delta"] = day["rank_delta"].abs()
        day["is_new_entry_top20"] = (day["rank"] <= 20) & (day["prev_rank"].isna() | (day["prev_rank"] > 20))
        day["is_new_entry_top50"] = (day["rank"] <= 50) & (day["prev_rank"].isna() | (day["prev_rank"] > 50))
    else:
        day[["prev_date", "prev_rank", "rank_delta", "abs_rank_delta"]] = None, None, None, None
        day["is_new_entry_top20"] = False
        day["is_new_entry_top50"] = False

    def stability_flag(r):
        if pd.isna(r.get("prev_rank")):
            return "new_entry"
        ard = r.get("abs_rank_delta", 999)
        if ard <= 10:
            return "stable"
        if ard <= 20:
            return "watch"
        return "alert"

    day["rank_stability_flag"] = day.apply(stability_flag, axis=1)

    # Feature snapshot
    print(f"Fetching features for {len(insts)} instruments...")
    feat_df = _load_features(insts, date, args.feature_list_id)
    if not feat_df.empty:
        feat_cols = [c for c in feat_df.columns if c not in ("trade_date", "instrument", "datetime")]
        day = day.merge(feat_df[["instrument"] + feat_cols], on="instrument", how="left")
        print(f"  Features merged: {len(feat_cols)} columns")
    else:
        print("  WARNING: No features fetched (features unavailable)")
        feat_cols = []

    # Path classification using builder-derived scores
    if all(c in day.columns for c in ["continuation_candidate_score", "repair_candidate_score",
                                        "overheat_risk_score", "value_trap_risk_score"]):
        for col in ["continuation_candidate_score", "repair_candidate_score",
                     "overheat_risk_score", "value_trap_risk_score"]:
            day[f"{col}_pct"] = day[col].rank(pct=True)

        def path_type(r):
            oh_pct = r.get("overheat_risk_score_pct", 0)
            vt_pct = r.get("value_trap_risk_score_pct", 0)
            cc_pct = r.get("continuation_candidate_score_pct", 0)
            rc_pct = r.get("repair_candidate_score_pct", 0)

            if oh_pct >= args.risk_extreme_quantile:
                return "overheat"
            if vt_pct >= args.risk_extreme_quantile:
                return "value_trap"
            if cc_pct >= args.path_high_quantile and rc_pct >= args.path_high_quantile:
                return "mixed"
            if cc_pct >= args.path_high_quantile:
                return "continuation"
            if rc_pct >= args.path_high_quantile:
                return "repair"
            return "unclear"

        day["path_type"] = day.apply(path_type, axis=1)
        print("  Path classification: using builder-derived scores")
    else:
        day["path_type"] = "unclear"
        print("  Path classification: builder-derived scores NOT available")

    # Output columns
    base_cols = ["trade_date", "instrument", "name", "industry", "rank",
                 "raw_score", "score", "score_pct",
                 "prev_rank", "rank_delta", "abs_rank_delta",
                 "is_new_entry_top20", "is_new_entry_top50",
                 "rank_stability_flag", "path_type"]

    path_score_cols = [c for c in day.columns if "candidate_score" in c or "risk_score" in c]
    out_cols = base_cols + feat_cols
    # deduplicate
    out_cols = list(dict.fromkeys(out_cols))

    result = day[out_cols].copy()

    # Coverage report
    exp_cols = len(FeatureListRegistry.load(args.feature_list_id))
    avail_cols = len([c for c in feat_cols if c in result.columns and result[c].notna().any()])
    print(f"\n=== COVERAGE SUMMARY ===")
    print(f"  Snapshot date: {date}")
    print(f"  Top-K: {args.top_k}")
    print(f"  Feature columns expected: {exp_cols}")
    print(f"  Feature columns available: {avail_cols}")
    print(f"  Feature coverage ratio: {avail_cols/exp_cols:.1%}")
    print(f"  raw_score available: {'score_raw' in signal_df.columns}")
    print(f"  Path scores available: {all(c in result.columns for c in ['continuation_candidate_score','repair_candidate_score'])}")
    print(f"  Prev date available: {prev_info[0][0] if prev_info else 'N/A'}")
    print(f"  Rank stability coverage: {result['rank_stability_flag'].notna().sum()}/{len(result)}")

    # Write
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(out_path, index=False, encoding="utf-8")
        print(f"\nWritten: {out_path}")
    else:
        print(f"\n=== TOP20 SAMPLE ===")
        sub = result.head(20)
        for _, r in sub.iterrows():
            r120 = r.get("ret_120d", None)
            r120s = f"{r120:+.0%}" if pd.notna(r120) else "N/A"
            cc = r.get("continuation_candidate_score", None)
            cc_s = f"{cc:.2f}" if pd.notna(cc) else "?"
            print(f"  r{r['rank']:3d} {r['instrument']:12s} score={r['score']:.2f} raw={r.get('raw_score',0):.3f} "
                  f"ret120={r120s} path={r.get('path_type','?')} "
                  f"cont={cc_s} st={r.get('rank_stability_flag','?')}")
        print(f"\nColumns: {list(result.columns)}")


if __name__ == "__main__":
    main()
