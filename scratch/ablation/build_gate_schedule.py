#!/usr/bin/env python3
"""Build the point-in-time exposure-gate schedules for the G0-G3 runs.

Definitions (fixed, no threshold search):

  market_risk_bad(t) = CSI800 trailing 120 trading-day close return <= 0
                       AND breadth_20d(t) <= full-sample median of breadth_20d.
    (The existing coarse-regime "down + bad-breadth" cell from diag_track5,
     reused as-is per instruction.  The median threshold is the existing
     full-sample constant — a known non-PIT element, kept to match the
     pre-existing regime definition.)

  model_health_bad(t) = trailing mean of realized cohort Top5-60d excess <= 0
    where a cohort is a 60d-cadence rebalance date d (from the winning cadence
    backtest), and its realized excess is
        mean(forward 60d close return of that day's top-5)
      - mean(forward 60d close return of the scored universe),
    which is strictly-prior usable at decision date t only when d + 60 < t
    (the forward window is fully realized before t's open).  Trailing window =
    the last 12 realized cohorts; at least 4 must be realized or the flag is
    off (insufficient evidence -> do not de-risk).  Carried forward between
    cohort dates.  Threshold is 0 (realized edge <= 0 -> model-health bad).

Schedules are written as JSON maps {"YYYY-MM-DD": bool} for each gate mode:
  g0            : all False (baseline, 100% exposure)
  g1_market_risk: market_risk_bad
  g2_model_health: model_health_bad
  g3_either     : market_risk_bad OR model_health_bad
"""
from __future__ import annotations

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

COHORT_RUN = EXEC_ROOT / "E1_refresh_60d"
MODEL_HEALTH_HORIZON = 60      # forward trading days per cohort
MODEL_HEALTH_TRAILING = 12     # last N realized cohorts in the mean
MODEL_HEALTH_MIN_REALIZED = 4  # fewer -> not bad
OUT_DIR = EXEC_ROOT / "gate_schedules"


def _trading_days() -> list[pd.Timestamp]:
    daily = pd.read_csv(COHORT_RUN / "daily_summary.csv")
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    return [pd.Timestamp(d) for d in daily["trade_date"]]


def _cohort_dates() -> list[pd.Timestamp]:
    """The 60d-cadence rebalance dates actually executed."""
    daily = pd.read_csv(COHORT_RUN / "daily_summary.csv")
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    return [pd.Timestamp(d) for d in daily.loc[daily["policy_entry_count"] > 0, "trade_date"]]


def build_market_risk_bad(
    bench_close: pd.Series,
    close_mat: pd.DataFrame,
    score_panel: pd.DataFrame,
    trading_days: list[pd.Timestamp],
) -> dict[pd.Timestamp, bool]:
    """PIT regime flags per trading day."""
    scored = set(score_panel["instrument"].unique())
    close_cols = [c for c in close_mat.columns if c in scored]
    mat = close_mat[close_cols]
    idx = mat.index
    # Bench reindexed onto the close-matrix calendar for row-offset lookups
    # (mirrors weekly_snapshots); protects against bench/trading-day mismatches.
    bench = bench_close.reindex(idx)
    breadth = {}
    for pos, t in enumerate(idx):
        if pos < 20:
            breadth[t] = np.nan
            continue
        trailing = mat.iloc[pos] / mat.iloc[pos - 20] - 1.0
        breadth[t] = float((trailing > 0).sum() / trailing.notna().sum())
    breadth_med = float(np.nanmedian(list(breadth.values())))

    out: dict[pd.Timestamp, bool] = {}
    for t in trading_days:
        if t not in idx:
            out[t] = False
            continue
        pos = idx.get_loc(t)
        if pos < 120:
            out[t] = False
            continue
        bench_120 = float(bench.iloc[pos] / bench.iloc[pos - 120] - 1.0)
        b = breadth.get(t)
        out[t] = bool(bench_120 <= 0.0 and b is not None and b <= breadth_med)
    return out


def build_model_health_bad(
    close_mat: pd.DataFrame,
    score_panel: pd.DataFrame,
    cohort_dates: list[pd.Timestamp],
    trading_days: list[pd.Timestamp],
) -> dict[pd.Timestamp, bool]:
    """PIT realized cohort Top5-excess flags, carried forward.

    A cohort d is realized at the close of trading row pos + H (the last close
    inside its forward window).  It is strictly-prior usable at a decision date
    t's open only when that final close date < t, so no future bar leaks in.
    """
    idx = close_mat.index
    scores_by_date = {
        t: g.set_index("instrument")["score"]
        for t, g in score_panel.groupby("trade_date")
    }
    univ_insts = sorted(score_panel["instrument"].unique())

    # cohort date -> (realization date, realized excess) if computable.
    cohort_realized: dict[pd.Timestamp, tuple[pd.Timestamp, float]] = {}
    for d in cohort_dates:
        if d not in idx:
            continue
        pos = int(idx.get_loc(d))  # int() guards against pandas scalar types
        j = pos + MODEL_HEALTH_HORIZON
        if j >= len(idx):
            continue  # forward window extends past the sample: never realized here
        sc = scores_by_date.get(d)
        if sc is None or len(sc) < 5:
            continue
        top5 = list(sc.sort_values(ascending=False).head(5).index)
        top5_fwd = forward_return(close_mat, d, MODEL_HEALTH_HORIZON, top5)
        univ_fwd = forward_return(close_mat, d, MODEL_HEALTH_HORIZON, univ_insts)
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


def main() -> None:
    print("loading inputs...", flush=True)
    score_panel = load_score_panel()
    close_mat = load_close_matrix()
    bench_close = load_benchmark("000906.SH")
    trading_days = _trading_days()
    cohort_dates = _cohort_dates()
    print(f"trading days: {len(trading_days)}, cohorts: {len(cohort_dates)}", flush=True)

    mr = build_market_risk_bad(bench_close, close_mat, score_panel, trading_days)
    mh = build_model_health_bad(close_mat, score_panel, cohort_dates, trading_days)

    g0 = {t.strftime("%Y-%m-%d"): False for t in trading_days}
    g1 = {t.strftime("%Y-%m-%d"): bool(mr[t]) for t in trading_days}
    g2 = {t.strftime("%Y-%m-%d"): bool(mh[t]) for t in trading_days}
    g3 = {
        t.strftime("%Y-%m-%d"): bool(mr[t] or mh[t]) for t in trading_days
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, sched in [("g0", g0), ("g1_market_risk", g1),
                        ("g2_model_health", g2), ("g3_either", g3)]:
        path = OUT_DIR / f"{name}.json"
        path.write_text(json.dumps(sched, indent=0, sort_keys=True))
        n_on = sum(1 for v in sched.values() if v)
        print(f"{name}: {n_on}/{len(sched)} days gated "
              f"({n_on / len(sched):.1%}) -> {path}", flush=True)

    # Per-year gating summary for the report.
    df = pd.DataFrame({
        "t": [t for t in trading_days],
        "g1": [bool(mr[t]) for t in trading_days],
        "g2": [bool(mh[t]) for t in trading_days],
        "g3": [bool(mr[t] or mh[t]) for t in trading_days],
    })
    df["year"] = df["t"].dt.year
    print("\n=== gating days by year ===", flush=True)
    print(df.groupby("year")[["g1", "g2", "g3"]].sum().to_string(), flush=True)
    print("\n=== model-health realized cohort excesses (last 12) ===", flush=True)


if __name__ == "__main__":
    main()
