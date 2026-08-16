#!/usr/bin/env python3
"""Build point-in-time exposure-gate schedules for the G0-G3 runs.

Definitions (fixed, no threshold search):

  market_risk_bad(t) = CSI800 trailing 120 trading-day close return <= 0
                       AND breadth_20d(t) <= strictly-prior median of
                       breadth_20d.
    breadth_20d(t) = fraction of the scored universe with positive trailing
    20d close return (NaN before the 20th trading row).  The median threshold
    is an EXPANDING median over breadth values realized STRICTLY BEFORE t
    (positions < pos) — fully point-in-time, no full-sample leakage.
    (Fix from the earlier full-sample-median definition, which used future
    breadth to set today's threshold.)

  model_health_bad(t) = trailing mean of realized cohort Top5-<H>d excess <= 0
    where a cohort is an ACTUAL rebalance date d of the gate's own backtest
    (daily_summary is_rebalance=True — execution truth, not an entry-count
    proxy), and its realized excess is
        mean(forward <H>d close return of that day's top-5)
      - mean(forward <H>d close return of the scored universe),
    with <H> = the model's label horizon (60d for the 60/180 blend, 180d for
    the S180 signal), which is strictly-prior usable at decision date t only
    when d + H < t (the forward window is fully realized before t's open).
    Trailing window = the last 12 realized cohorts; at least 4 must be
    realized or the flag is off (insufficient evidence -> do not de-risk).
    Carried forward between cohort dates.  Threshold is 0.

Schedules are written as JSON maps {"YYYY-MM-DD": bool} for each gate mode:
  g0            : all False (baseline, 100% exposure)
  g1_market_risk: market_risk_bad   (signal-common: scored universe = blend)
  g2_model_health: model_health_bad (cohort + signal configurable per model)
  g3_either     : market_risk_bad OR model_health_bad

The S180 model-health schedule MUST be built from the S180 signal's own
cohorts (the run that gates it) with horizon 180 — the blend-derived G2 is
not a valid model-health readout for S180:
    python build_gate_schedule.py --cohort-run A_S180_60d \
        --signal-id fwd_ret_180d_raw__daily_zscore \
        --signal-run-id rolling__financial_rc_180d_..._2021-01-01_2026-07-31 \
        --horizon 180 --g2-only --g2-out g2_model_health_s180.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scratch.ablation.diag_common import (  # noqa: E402
    EXEC_ROOT,
    forward_return,
    load_benchmark,
    load_close_matrix,
    load_score_panel,
)

MODEL_HEALTH_TRAILING = 12     # last N realized cohorts in the mean
MODEL_HEALTH_MIN_REALIZED = 4  # fewer -> not bad
OUT_DIR = EXEC_ROOT / "gate_schedules"


def _trading_days(run_dir: Path) -> list[pd.Timestamp]:
    daily = pd.read_csv(run_dir / "daily_summary.csv")
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    return [pd.Timestamp(d) for d in daily["trade_date"]]


def _cohort_dates(run_dir: Path) -> list[pd.Timestamp]:
    """Actual rebalance dates of *run_dir* (is_rebalance, execution truth)."""
    daily = pd.read_csv(run_dir / "daily_summary.csv")
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    if "is_rebalance" in daily.columns:
        flag = daily["is_rebalance"].fillna(False).astype(bool)
    else:
        # Pre-flag artifacts: fall back to the entry-count proxy (documented
        # as an approximation; new artifacts always carry the flag).
        flag = daily["policy_entry_count"].fillna(0) > 0
    return [pd.Timestamp(d) for d in daily.loc[flag, "trade_date"]]


def build_market_risk_bad(
    bench_close: pd.Series,
    close_mat: pd.DataFrame,
    score_panel: pd.DataFrame,
    trading_days: list[pd.Timestamp],
) -> dict[pd.Timestamp, bool]:
    """PIT regime flags per trading day (strictly-prior breadth median)."""
    scored = set(score_panel["instrument"].unique())
    close_cols = [c for c in close_mat.columns if c in scored]
    mat = close_mat[close_cols]
    idx = mat.index
    # Bench reindexed onto the close-matrix calendar for row-offset lookups
    # (mirrors weekly_snapshots); protects against bench/trading-day mismatches.
    bench = bench_close.reindex(idx)

    breadth: dict[pd.Timestamp, float] = {}
    for pos, t in enumerate(idx):
        if pos < 20:
            breadth[t] = np.nan
            continue
        trailing = mat.iloc[pos] / mat.iloc[pos - 20] - 1.0
        breadth[t] = float((trailing > 0).sum() / trailing.notna().sum())

    out: dict[pd.Timestamp, bool] = {}
    for t in trading_days:
        if t not in idx:
            out[t] = False
            continue
        pos = int(idx.get_loc(t))  # int() guards against pandas scalar types
        if pos < 120:
            out[t] = False
            continue
        bench_120 = float(bench.iloc[pos] / bench.iloc[pos - 120] - 1.0)
        b = breadth.get(t)
        if b is None or not np.isfinite(b):
            out[t] = False
            continue
        # Strictly-prior threshold: expanding median of breadth realized at
        # positions 20..pos-1 (all before today's open).  Never includes
        # today's or future breadth.
        prior = [breadth[idx[q]] for q in range(20, pos)
                 if breadth[idx[q]] is not None and np.isfinite(breadth[idx[q]])]
        if not prior:
            out[t] = False
            continue
        median = float(np.median(prior))
        out[t] = bool(bench_120 <= 0.0 and b <= median)
    return out


def build_model_health_bad(
    close_mat: pd.DataFrame,
    cohort_score_panel: pd.DataFrame,
    cohort_dates: list[pd.Timestamp],
    trading_days: list[pd.Timestamp],
    horizon: int,
) -> dict[pd.Timestamp, bool]:
    """PIT realized cohort Top5-excess flags, carried forward.

    A cohort d is realized at the close of trading row pos + H (the last close
    inside its forward window).  It is strictly-prior usable at a decision date
    t's open only when that final close date < t, so no future bar leaks in.
    """
    idx = close_mat.index
    scores_by_date = {
        t: g.set_index("instrument")["score"]
        for t, g in cohort_score_panel.groupby("trade_date")
    }
    univ_insts = sorted(cohort_score_panel["instrument"].unique())

    # cohort date -> (realization date, realized excess) if computable.
    cohort_realized: dict[pd.Timestamp, tuple[pd.Timestamp, float]] = {}
    for d in cohort_dates:
        if d not in idx:
            continue
        pos = int(idx.get_loc(d))  # int() guards against pandas scalar types
        j = pos + horizon
        if j >= len(idx):
            continue  # forward window extends past the sample: never realized here
        sc = scores_by_date.get(d)
        if sc is None or len(sc) < 5:
            continue
        top5 = list(sc.sort_values(ascending=False).head(5).index)
        top5_fwd = forward_return(close_mat, d, horizon, top5)
        univ_fwd = forward_return(close_mat, d, horizon, univ_insts)
        if top5_fwd.notna().any() and univ_fwd.notna().any():
            cohort_realized[d] = (idx[j], float(top5_fwd.mean() - univ_fwd.mean()))

    out: dict[pd.Timestamp, bool] = {}
    # Strictly increasing by cohort date, which is increasing in realization date.
    cohort_order = sorted(cohort_realized)
    pending: list[float] = []
    di = 0
    for t in trading_days:
        # Admit every cohort whose forward window's final close is < t.
        while di < len(cohort_order):
            d = cohort_order[di]
            rdate, value = cohort_realized[d]
            if rdate < t:
                pending.append(value)
                di += 1
            else:
                break
        # Keep only the last MODEL_HEALTH_TRAILING realized values.
        if len(pending) > MODEL_HEALTH_TRAILING:
            pending = pending[-MODEL_HEALTH_TRAILING:]
        if len(pending) >= MODEL_HEALTH_MIN_REALIZED:
            out[t] = float(np.mean(pending)) <= 0.0
        else:
            out[t] = False
    return out


def load_signal_panel(signal_id: str, signal_run_id: str) -> pd.DataFrame:
    if signal_id == "financial_rc_60d_180d_50_50__daily_zscore" and signal_run_id == "blend__":
        return load_score_panel()
    path = (
        Path("/home/liuming/.openclaw/workspace/SysQ/data/research/signals")
        / signal_id / signal_run_id / "predictions.parquet"
    )
    df = pd.read_parquet(path)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df[["trade_date", "instrument", "score"]].dropna()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort-run", default="E1_refresh_60d",
                    help="run dir (under execution_policy/) whose is_rebalance dates are the cohorts")
    ap.add_argument("--signal-id", default="financial_rc_60d_180d_50_50__daily_zscore")
    ap.add_argument("--signal-run-id", default="blend__007a93600f45de00")
    ap.add_argument("--horizon", type=int, default=60,
                    help="forward trading days per cohort (the model's label horizon)")
    ap.add_argument("--g2-only", action="store_true",
                    help="write only the model-health schedule (S180-specific build)")
    ap.add_argument("--g2-out", default="g2_model_health.json",
                    help="output filename for the model-health schedule")
    args = ap.parse_args()

    cohort_dir = EXEC_ROOT / args.cohort_run
    if not (cohort_dir / "daily_summary.csv").exists():
        raise SystemExit(f"cohort run missing daily_summary.csv: {cohort_dir}")

    print("loading inputs...", flush=True)
    # Market-risk breadth uses the blend scored universe (signal-common,
    # matches the pre-existing regime definition reused for all gates).
    score_panel = load_score_panel()
    cohort_scores = load_signal_panel(args.signal_id, args.signal_run_id)
    close_mat = load_close_matrix()
    bench_close = load_benchmark("000906.SH")
    trading_days = _trading_days(cohort_dir)
    cohort_dates = _cohort_dates(cohort_dir)
    print(f"cohort run: {args.cohort_run} | trading days: {len(trading_days)}, "
          f"cohorts (is_rebalance): {len(cohort_dates)} | "
          f"model-health horizon: {args.horizon}d", flush=True)

    mr = build_market_risk_bad(bench_close, close_mat, score_panel, trading_days)
    mh = build_model_health_bad(
        close_mat, cohort_scores, cohort_dates, trading_days, args.horizon
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Schedules are serialized with YYYY-MM-DD string keys — the engine's
    # exposure_gate_schedule contract (strategy_runner validates this format).
    key = lambda t: t.strftime("%Y-%m-%d")
    writes = [("g0", "g0.json", {key(t): False for t in trading_days})]
    if args.g2_only:
        writes.append(("g2_model_health", args.g2_out,
                       {key(t): bool(mh[t]) for t in trading_days}))
    else:
        writes += [
            ("g1_market_risk", "g1_market_risk.json",
             {key(t): bool(mr[t]) for t in trading_days}),
            ("g2_model_health", "g2_model_health.json",
             {key(t): bool(mh[t]) for t in trading_days}),
            ("g3_either", "g3_either.json",
             {key(t): bool(mr[t] or mh[t]) for t in trading_days}),
        ]
    for label, fname, sched in writes:
        path = OUT_DIR / fname
        path.write_text(json.dumps(sched, indent=0, sort_keys=True))
        n_on = sum(1 for v in sched.values() if v)
        print(f"{label}: {n_on}/{len(sched)} days gated "
              f"({n_on / len(sched):.1%}) -> {path}", flush=True)

    # Per-year gating summary for the report.
    df = pd.DataFrame({
        "t": [t for t in trading_days],
        "g1": [bool(mr[t]) for t in trading_days],
        "g2": [bool(mh[t]) for t in trading_days],
    })
    df["year"] = df["t"].dt.year
    print("\n=== gating days by year ===", flush=True)
    print(df.groupby("year")[["g1", "g2"]].sum().to_string(), flush=True)


if __name__ == "__main__":
    main()
