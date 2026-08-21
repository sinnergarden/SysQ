#!/usr/bin/env python3
"""PIT universe ladder selection diagnostic — U0 vs U2 (Phase 1).

DIAGNOSTIC experiment (non-production, no deployment path).  Answers:
does the PIT-corrected S180 alpha reappear when the universe constraint is
CSI1800 instead of CSI800 (U0 14.5% -> U2?), or is the feature-set alpha
ceiling the real limit?

Pipeline per universe (train universe == predict universe, no future info):
  U0 : per-window cache-hit on the existing 68-window v3 caches (exact
       replication of the stored PIT rolling run).
  U2 : materialize-once full-range frame -> slice per sampled window.

Frozen: label=fwd_ret_180d_raw (adjusted close, raw); LightGBM 300 trees seed
42; raw-ranking (per-trade_date unclipped zscore); Top5/Top20;
feature_list_id=v3a_plus_liquidity_financial_rc.

Usage (from MAIN SysQ cwd):
  # Gate 1 — U0 replication checkpoint (STOPs on mismatch):
  python scratch/ablation/pit_universe_ladder_selection_diag.py --checkpoint

  # Gate 2 — coverage report:
  python scratch/ablation/pit_ladder_coverage.py --universes U0,U2

  # Phase 1 — full diagnostic on U0,U2:
  python scratch/ablation/pit_universe_ladder_selection_diag.py --diag --universes U0,U2

Outputs (scratch/ablation/pit_ladder/):
  sampled_dates.json            deterministic sampled-window list
  random_seeds.json             100 fixed seeds for the random Top5 baseline
  checkpoint_report.json        U0 top5 identity + RankICIR vs stored signal
  selection_diag.csv            per (universe, retrain_date) metrics
  selection_daily.csv           per (universe, trade_date) rank IC (aggregate)
  selection_summary.csv         per-universe aggregates incl. uplift
  selection_detail.parquet      per (universe, retrain_date, trade_date, inst)
  universe_profile.csv          per universe × sampled date size/mcap/industry
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/home/liuming/.openclaw/workspace/SysQ")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scratch.ablation.pit_ladder_common import (  # noqa: E402
    OUT_DIR,
    UNIVERSES,
    U0_STORED_PREDS,
    build_gen,
    eval_metrics,
    extended_end,
    load_label,
    load_materialized_frame,
    load_registry,
    pit_daily_size,
    random_top5_baseline,
    sample_windows,
    train_predict_window,
    write_json,
)
from qsys.data.calendar import get_trading_calendar  # noqa: E402

SEEDS = list(range(100))
RANDOM_SEEDS_PATH = OUT_DIR / "random_seeds.json"
SAMPLED_PATH = OUT_DIR / "sampled_dates.json"


# ── U0 checkpoint ─────────────────────────────────────────────────────


def is_cap_tie_day(day_scores: pd.Series, n_top: int = 5) -> bool:
    """True if >=5 names sit AT the +3.0 score_raw cap (cap-day top tie)."""
    s = day_scores.sort_values(ascending=False)
    if len(s) < n_top:
        return False
    return float(s.iloc[n_top - 1]) >= 2.999 and float(s.iloc[0]) >= 2.999


def validate_u0_checkpoint(stored: pd.DataFrame, mine: pd.DataFrame) -> dict:
    """Per-day top5 identity (my unclipped zscore vs stored score_raw).

    Differences are permitted ONLY on cap-tie days (>=5 names at +3.0), exactly
    as the raw-ranking fix documented.  A non-capped mismatch => STOP.
    """
    stored = stored[["trade_date", "instrument", "score_raw"]].copy()
    mine = mine[["trade_date", "instrument", "score"]].copy()
    stored["trade_date"] = stored["trade_date"].astype(str).str[:10]
    mine["trade_date"] = mine["trade_date"].astype(str).str[:10]

    days = sorted(set(stored["trade_date"]) & set(mine["trade_date"]))
    matches, capped_mism, clean_mism, no_common = 0, 0, 0, 0
    examples = []
    for t in days:
        sd = stored[stored["trade_date"] == t]
        md = mine[mine["trade_date"] == t]
        common = set(sd["instrument"]) & set(md["instrument"])
        if len(common) < 30:
            no_common += 1
            continue
        s5 = set(sd[sd["instrument"].isin(common)].sort_values("score_raw", ascending=False).head(5)["instrument"])
        m5 = set(md[md["instrument"].isin(common)].sort_values("score", ascending=False).head(5)["instrument"])
        capped = is_cap_tie_day(sd.set_index("instrument")["score_raw"])
        if s5 == m5:
            matches += 1
        elif capped:
            capped_mism += 1
        else:
            clean_mism += 1
            if len(examples) < 5:
                examples.append({"date": t, "stored": sorted(s5), "mine": sorted(m5)})
    result = {
        "n_common_days": len(days),
        "top5_identical": matches,
        "top5_diff_capped_days": capped_mism,
        "top5_diff_clean_days": clean_mism,
        "days_no_common_insts": no_common,
        "examples": examples,
        "pass": clean_mism == 0,
    }
    return result


def rank_ic_aggregate(detail: pd.DataFrame, label_df: pd.DataFrame) -> dict:
    """Daily Spearman rank IC over all sampled days (signal_analytics 口径)."""
    lab = label_df[["trade_date", "instrument", "label_value"]].astype(
        {"trade_date": str}
    )
    lab["trade_date"] = lab["trade_date"].str[:10]
    j = detail[["trade_date", "instrument", "score"]].merge(
        lab, on=["trade_date", "instrument"], how="inner"
    )
    j = j.dropna(subset=["score", "label_value"])
    if j.empty:
        return {"rank_ic_mean": None, "rank_ic_std": None, "rank_icir": None, "n_days": 0}
    rows = []
    for t, g in j.groupby("trade_date"):
        if len(g) < 5:
            continue
        r = g["score"].corr(g["label_value"], method="spearman")
        if np.isfinite(r):
            rows.append(r)
    arr = np.array(rows)
    if len(arr) == 0:
        return {"rank_ic_mean": None, "rank_ic_std": None, "rank_icir": None, "n_days": 0}
    mean = float(arr.mean())
    std = float(arr.std(ddof=1))
    return {
        "rank_ic_mean": mean,
        "rank_ic_std": std,
        "rank_icir": mean / std if std > 0 else None,
        "n_days": int(len(arr)),
    }


# ── Universe runner ───────────────────────────────────────────────────


def run_universe(key: str, sampled: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Run the 20-window diagnostic for one universe.

    Returns (diag_rows, daily_rows, detail_rows).
    """
    u = UNIVERSES[key]
    gen = build_gen(key)
    label_df = load_label(key)

    if u["mode"] == "per_window":
        materialized = None
        clean = None
    else:
        materialized, clean, _ = load_materialized_frame(key)

    diag, daily, detail = [], [], []
    for _, win in sampled.iterrows():
        w = win.to_dict()
        retrain_date = w["predict_start"]
        if materialized is not None:
            frame = materialized
        else:
            frame, clean = gen._load_data(w["train_start"], extended_end(w["predict_end"]))
            frame = gen._apply_pit_membership(frame)

        preds = train_predict_window(frame, clean, gen, w, label_df)
        if preds.empty:
            print(f"[{key}] {win['window_id']}: no predictions, skip", flush=True)
            continue
        preds["trade_date"] = preds["trade_date"].astype(str).str[:10]

        # n_train_rows for this window (PIT-filtered train subset).
        n_train = int(
            ((frame["trade_date"] >= w["train_start"]) & (frame["trade_date"] <= w["train_end"])).sum()
        )
        n_filtered = int(len(frame))

        for t in sorted(preds["trade_date"].unique()):
            day = preds[preds["trade_date"] == t].copy()
            label_at_t = label_df[label_df["trade_date"].astype(str).str[:10] == t].set_index("instrument")["label_value"]
            m = eval_metrics(day, label_at_t)
            rb = random_top5_baseline(day, label_at_t, SEEDS)
            if not m:
                continue
            base = {
                "universe": key,
                "retrain_date": retrain_date,
                "window_id": win["window_id"],
                "trade_date": t,
                "n_train_rows": n_train,
                "n_filtered_rows": n_filtered,
                "n_predict_rows": int(len(day)),
            }
            row = {**base, **m, **rb}
            if t == retrain_date:
                diag.append(row)
            daily.append(row)

            # selection detail
            joined = day.set_index("instrument").join(label_at_t, how="inner").dropna(subset=["score", "label_value"])
            if not joined.empty:
                joined = joined.sort_values("score", ascending=False)
                joined["rank"] = range(1, len(joined) + 1)
                joined["score_percentile"] = joined["score"].rank(pct=True)
                for code, r in joined.iterrows():
                    detail.append({
                        "universe": key,
                        "retrain_date": retrain_date,
                        "window_id": win["window_id"],
                        "trade_date": t,
                        "instrument": code,
                        "score": float(r["score"]),
                        "rank": int(r["rank"]),
                        "score_percentile": float(r["score_percentile"]),
                        "fwd180": float(r["label_value"]),
                        "is_top5": int(r["rank"] <= 5),
                        "is_top20": int(r["rank"] <= 20),
                        "is_winner50": int(r["label_value"] > 0.50),
                        "is_winner100": int(r["label_value"] > 1.00),
                    })
        print(f"[{key}] {win['window_id']} retrain={retrain_date} "
              f"preds={len(preds)} days={preds['trade_date'].nunique()} "
              f"train_rows={n_train}", flush=True)

    diag_df = pd.DataFrame(diag)
    daily_df = pd.DataFrame(daily)
    detail_df = pd.DataFrame(detail)
    if diag_df.empty:
        raise RuntimeError(f"{key}: no metrics produced — check label store + data")
    return diag_df, daily_df, detail_df


def summarize(diag_df: pd.DataFrame, key: str) -> dict:
    sub = diag_df[diag_df["universe"] == key]
    sub = sub.dropna(subset=["rank_ic_180"])
    if sub.empty:
        return {"universe": key, "n_dates": 0}
    return {
        "universe": key,
        "n_dates": int(len(sub)),
        "mean_rank_ic": float(sub["rank_ic_180"].mean()),
        "rank_icir": (lambda a: None if a.std(ddof=1) == 0 else a.mean() / a.std(ddof=1))(sub["rank_ic_180"]),
        "ndcg5_mean": float(sub["ndcg_at5"].mean()),
        "top5_ret_mean": float(sub["top5_fwd180"].mean()),
        "top5_ret_median": float(sub["top5_fwd180"].median()),
        "top20_ret_mean": float(sub["top20_fwd180"].mean()),
        "univ_ew_mean": float(sub["univ_ew_fwd180"].mean()),
        "top5_excess_ew_mean": float(sub["top5_excess_ew"].mean()),
        "top20_excess_ew_mean": float(sub["top20_excess_ew"].mean()),
        "top5_capture50": float(sub["top5_capture50"].mean()),
        "top5_capture100": float(sub["top5_capture100"].mean()),
        "top5_precision50": float(sub["top5_precision50"].mean()),
        "top5_precision100": float(sub["top5_precision100"].mean()),
        "random_top5_ret": float(sub["random_top5_fwd180"].mean()),
        "model_uplift_over_random": float((sub["top5_fwd180"] - sub["random_top5_fwd180"]).mean()),
        "random_capture100": float(sub["random_capture100"].mean()),
        "capture100_uplift": float((sub["top5_capture100"] - sub["random_capture100"]).mean()),
    }


# ── Universe profile (market cap / industry, user constraint 2) ───────


def universe_profile(key: str, sampled: pd.DataFrame) -> pd.DataFrame:
    import sqlite3

    u = UNIVERSES[key]
    registry = load_registry(key)
    conn = sqlite3.connect(ROOT / "data/meta.db")
    ind = pd.read_sql("SELECT ts_code, industry FROM stock_basic", conn)
    conn.close()
    ind = ind.set_index("ts_code")["industry"]

    rows = []
    for t in sampled["predict_start"]:
        t_int = t.replace("-", "")
        st = registry["start"].astype(str).str.replace("-", "", regex=False).astype(int)
        en = registry["end"].astype(str).str.replace("-", "", regex=False).astype(int)
        members = registry.loc[(st <= int(t_int)) & (en >= int(t_int)), "instrument"].tolist()
        if not members:
            continue
        # market cap (total_mv, 万元) from canonical at date t
        mcs = []
        for code in members:
            f = ROOT / "data/canonical/daily" / f"{code}.feather"
            if not f.is_file():
                continue
            d = pd.read_feather(f, columns=["ts_code", "trade_date", "total_mv"])
            d["trade_date"] = d["trade_date"].astype(str)
            hit = d[d["trade_date"] == t_int.replace("-", "")]
            if not hit.empty:
                mcs.append(float(hit.iloc[0]["total_mv"]))
        mc = pd.Series(mcs)
        inds = ind.reindex(members).dropna()
        row = {
            "universe": key,
            "date": t,
            "n_members": len(members),
            "n_mcap_available": len(mc),
            "mcap_median_wan": float(mc.median()) if len(mc) else None,
            "mcap_p10_wan": float(mc.quantile(0.10)) if len(mc) else None,
            "mcap_p25_wan": float(mc.quantile(0.25)) if len(mc) else None,
            "mcap_p75_wan": float(mc.quantile(0.75)) if len(mc) else None,
            "mcap_p90_wan": float(mc.quantile(0.90)) if len(mc) else None,
            "n_industry_known": int(inds.notna().sum()),
            "top_industries": inds.value_counts().head(5).to_dict(),
        }
        rows.append(row)
    return pd.DataFrame(rows)


# ── Main ──────────────────────────────────────────────────────────────


def run_checkpoint() -> int:
    sampled = sample_windows()
    diag_df, daily_df, detail_df = run_universe("U0", sampled)
    # Persist immediately — the 20-window compute is the expensive part; a crash
    # in the aggregation step below must not force a full re-run.
    detail_df.to_parquet(OUT_DIR / "checkpoint_detail.parquet", index=False)
    diag_df.to_csv(OUT_DIR / "checkpoint_diag.csv", index=False)
    mine = detail_df[detail_df["universe"] == "U0"]

    if not U0_STORED_PREDS.is_file():
        raise RuntimeError(f"U0 stored signal missing: {U0_STORED_PREDS}")
    stored = pd.read_parquet(U0_STORED_PREDS)
    v = validate_u0_checkpoint(stored, mine)
    label_df = load_label("U0")
    ic = rank_ic_aggregate(mine, label_df)

    # subset the stored signal to the same sampled dates for a direct compare.
    # Select ONLY the ranking column first — the stored predictions frame carries
    # both `score` (transform output) and `score_raw`; renaming in place would
    # create duplicate `score` columns and break the groupby corr below.
    sampled_days = set(mine["trade_date"].unique())
    stored_sub = stored[["trade_date", "instrument", "score_raw"]].copy()
    stored_sub["trade_date"] = stored_sub["trade_date"].astype(str).str[:10]
    stored_sub = stored_sub[stored_sub["trade_date"].isin(sampled_days)]
    stored_ic = rank_ic_aggregate(
        stored_sub.rename(columns={"score_raw": "score"}),
        label_df,
    )

    report = {
        "sampled_dates": sorted(sampled_days),
        "checkpoint": v,
        "my_rank_ic": ic,
        "stored_rank_ic_same_days": stored_ic,
        "audit_reference_rank_icir": 0.961,
    }
    write_json(OUT_DIR / "checkpoint_report.json", report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if not v["pass"]:
        print("\n!! U0 checkpoint FAILED: non-capped top5 mismatches -> STOP "
              "(U1/U2/U3 results would be untrustworthy)", flush=True)
        return 1
    print("\nU0 checkpoint PASS: top5 identical on all non-capped sampled days "
          f"({v['top5_identical']}/{v['n_common_days']}); "
          f"my RankICIR={ic['rank_icir']:.3f} "
          f"(stored same-days={stored_ic['rank_icir']:.3f}, audit=0.961)", flush=True)
    return 0


def run_diag(universes: list[str]) -> int:
    sampled = sample_windows()
    write_json(SAMPLED_PATH, sampled.to_dict("records"))
    write_json(RANDOM_SEEDS_PATH, SEEDS)

    all_diag, all_daily, all_detail = [], [], []
    for key in universes:
        diag_df, daily_df, detail_df = run_universe(key, sampled)
        all_diag.append(diag_df)
        all_daily.append(daily_df)
        all_detail.append(detail_df)
    diag_df = pd.concat(all_diag, ignore_index=True)
    daily_df = pd.concat(all_daily, ignore_index=True)
    detail_df = pd.concat(all_detail, ignore_index=True)

    diag_df.to_csv(OUT_DIR / "selection_diag.csv", index=False)
    daily_df.to_csv(OUT_DIR / "selection_daily.csv", index=False)
    detail_df.to_parquet(OUT_DIR / "selection_detail.parquet", index=False)

    summary_rows = [summarize(diag_df, k) for k in universes]
    pd.DataFrame(summary_rows).to_csv(OUT_DIR / "selection_summary.csv", index=False)

    profile_rows = [universe_profile(k, sampled) for k in universes]
    pd.DataFrame(profile_rows).to_csv(OUT_DIR / "universe_profile.csv", index=False)

    print("\n=== selection_summary ===")
    print(pd.DataFrame(summary_rows).to_string(index=False))
    print("\nwrote:", OUT_DIR)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint", action="store_true",
                    help="run U0 only and validate against the stored PIT signal")
    ap.add_argument("--diag", action="store_true",
                    help="run the full Phase 1 diagnostic")
    ap.add_argument("--universes", default="U0,U2",
                    help="comma-separated universe keys for --diag")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.checkpoint:
        return run_checkpoint()
    if args.diag:
        return run_diag([k.strip() for k in args.universes.split(",") if k.strip()])
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
