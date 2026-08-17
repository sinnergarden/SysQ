#!/usr/bin/env python3
"""Phase 3 — build C1 (mean-rank consensus) + C2 (confirmed-first) SignalRuns.

The engine consumes the per-day `score` column and rebalances on the 68 retrain
days (20d grid == retrain activation).  Both counterfactuals keep the frozen
S180 signal everywhere EXCEPT the retrain days, where the score is replaced:

  C1 consensus_rank_score = mean(pct_rank_old, pct_rank_new)   (z-scored/day)
      -> engine Top5 = highest consensus (both models agree)
  C2 confirmed-first: BOTH names first (by new-model rank), then fill by
      new-model rank, ALWAYS the set C0 actually holds.

C2 correctness hinges on reproducing the baseline engine's SELECTED SET on each
retrain day — not a naive sort of the base signal, because on score-capped days
(70% of retrain days have >5 names tied at the raw cap) pandas quicksort makes
the engine's head-5 a tiebreak lottery that a fresh sort does not reproduce.

The ground-truth C0 selection on retrain day t is read from the baseline
S180_20d executions:
  forced_set(t) = held set AFTER t's rebalance  ∪  buy candidates C0 generated
                  on t (filled OR rejected — a rejected Limit-Up order must stay
                  in the banded set so C2's engine re-generates the same order
                  and hits the same rejection).

Banding forced_set(t) (BOTH=2.0+pct, NEW_IN fill=1.0+pct, everything else 0.0)
makes the C2 engine's rank_exit keep exactly the names C0 kept, exit exactly
the names C0 exited, and issue exactly the same refill orders — so C2 is
path-identical to C0 except for the within-set ordering (confirmed-first).
Under the equal-weight Top5 + rank_exit skeleton this ordering is the ONLY
free variable, and it should not change holdings.

Baseline C0 = the original S180 signal (backtest ba710797) — no override.

Run from the MAIN SysQ cwd (qsys + data resolve).  Writes two SignalRuns via
the canonical SignalStore.save_signal_run path (not ad-hoc files).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/home/liuming/.openclaw/workspace/SysQ")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qsys.signal.store import SignalStore  # noqa: E402

SRID = (
    "rolling__financial_rc_180d_rolling_5y_to_202607_v3__"
    "v3a_growth_financial_180d__fwd_ret_180d_raw__daily_zscore__"
    "2021-01-01_2026-07-31"
)
SIG_ID = "fwd_ret_180d_raw__daily_zscore"
PREDS = ROOT / "data/research/signals" / SIG_ID / SRID / "predictions.parquet"
REINFER = ROOT / "scratch/ablation/reinfer_retrain_days.parquet"
# baseline C0 executions (the engine's actual selection ground truth)
C0_EXEC = (
    "/home/liuming/.openclaw/workspace/SysQ-execution-ledger/data/research/ablation/"
    "execution_policy/S180_20d/executions.csv"
)

OUT_ID_PREFIX = "fwd_ret_180d_raw__daily_zscore__cf"
C1_RUN = f"{OUT_ID_PREFIX}__c1_consensus__{SRID[:64]}"
C2_RUN = f"{OUT_ID_PREFIX}__c2_confirmed__{SRID[:64]}"


def c0_forced_top5_from_executions(exec_csv: str, retrain_days: list) -> dict:
    """{retrain_day(Timestamp): forced_set} from the baseline S180_20d run.

    forced_set = held set after t's rebalance  ∪  buy candidates C0 generated
    on t (filled or rejected).  Held set carries forward across no-trade days
    (e.g. 2022-01-27 where C0's top5 was unchanged and no order was issued).
    """
    e = pd.read_csv(exec_csv)
    e = e.sort_values(["trade_date", "sequence"])
    qty: dict[str, float] = {}
    held_snap: dict[str, set] = {}
    buy_attempts: dict[str, set] = {}
    for r in e.itertuples(index=False):
        d = str(r.trade_date)[:10]
        inst = r.instrument
        q = float(r.filled_qty)
        if r.side == "buy":
            qty[inst] = qty.get(inst, 0.0) + q
            buy_attempts.setdefault(d, set()).add(inst)
        else:
            qty[inst] = qty.get(inst, 0.0) - q
            if abs(qty[inst]) < 1e-9:
                qty.pop(inst, None)
        held_snap[d] = {i for i, qq in qty.items() if qq > 0}
    all_td = sorted(held_snap)

    forced = {}
    for t in retrain_days:
        ts = t.strftime("%Y-%m-%d") if hasattr(t, "strftime") else str(t)[:10]
        snap = None
        for d in all_td:
            if d <= ts:
                snap = held_snap[d]
            else:
                break
        if snap is None:
            print(f"[warn] {ts}: no C0 holdings snapshot available", file=sys.stderr)
            continue
        forced[pd.Timestamp(t)] = snap | buy_attempts.get(ts, set())
    return forced


def build_c1_score(panel: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Return {trade_date: consensus score} for retrain days."""
    out = {}
    for t, g in panel.groupby("trade_date"):
        g = g.dropna(subset=["score_old", "score_new"])
        if len(g) < 10:
            continue
        po = g["score_old"].rank(pct=True)
        pn = g["score_new"].rank(pct=True)
        cons = (po + pn) / 2.0
        # z-score within the day so the scale matches the surrounding signal
        z = (cons - cons.mean()) / cons.std(ddof=0) if cons.std() > 0 else cons
        out[pd.Timestamp(t)] = pd.Series(z.values, index=g["instrument"].values)
    return out


def build_c2_score(panel: pd.DataFrame, forced_top5: dict) -> dict[str, pd.DataFrame]:
    """Confirmed-first scores on retrain days, forced to C0's actual held set.

    `forced_top5[t]` is C0's engine-selected set on retrain day t (held-after ∪
    buy attempts).  Because confirmed ∪ fill == forced_top5, C2's selection is
    SET-IDENTICAL to C0's by construction; the override only re-orders within
    the set (BOTH before NEW_IN) and pushes every other name strictly below.

    Rank series are built with an instrument index — ranking a g-subset Series
    directly keeps the DataFrame's integer row index, so `.reindex(instrument)`
    silently returns NaN (the bug that collapsed the first C2 build to zeros).
    """
    out = {}
    for t, g in panel.groupby("trade_date"):
        g = g.dropna(subset=["score_old", "score_new"])
        if len(g) < 10:
            continue
        fs = forced_top5.get(pd.Timestamp(t), set())
        g_set = set(g["instrument"])
        missing = fs - g_set
        fs = fs & g_set
        if missing:
            print(f"[warn] {pd.Timestamp(t).date()} forced names missing from "
                  f"panel cross-section: {sorted(missing)}", file=sys.stderr)
        old5 = set(g.sort_values("score_old", ascending=False).head(5)["instrument"])
        both = fs & old5
        fill = fs - both
        g2 = g.set_index("instrument")
        new_rank = g2["score_new"].rank(method="first", ascending=True, pct=True)
        score = pd.Series(0.0, index=g2.index)
        score.loc[list(fill)] = 1.0 + new_rank.loc[list(fill)].values
        score.loc[list(both)] = 2.0 + new_rank.loc[list(both)].values
        # z-score keeps band ordering strict (band1 min > band0 max), scales
        # like C1, and is monotonic so the engine's head() sees the same set.
        z = (score - score.mean()) / score.std(ddof=0) if score.std() > 0 else score
        out[pd.Timestamp(t)] = pd.Series(z.values, index=g2.index)
    return out


def write_run(signal_id: str, run_id: str, df: pd.DataFrame) -> Path:
    store = SignalStore(str(ROOT / "data/research"))
    p = store.save_signal_run(
        signal_id, run_id, df,
        manifest={
            "counterfactual_of": SRID,
            "artifact_type": "counterfactual_signal_run",
            "description": "C1/C2 score override on retrain days",
        },
        overwrite=True,
    )
    print(f"wrote {p}  ({len(df)} rows)")
    return p


def verify_stored(stored_parquet: Path, forced_top5: dict, label: str) -> int:
    """Check the STORED parquet (what the backtest reads) top5 == forced set."""
    s = pd.read_parquet(stored_parquet)
    s["trade_date"] = pd.to_datetime(s["trade_date"])
    bad = 0
    for t, fs in sorted(forced_top5.items()):
        day = s[s["trade_date"] == t]
        if day.empty:
            print(f"  MISMATCH {t.date()}: no stored rows")
            bad += 1
            continue
        st5 = set(day.sort_values("score", ascending=False).head(5)["instrument"])
        if st5 != fs:
            bad += 1
            print(f"  MISMATCH {t.date()}: stored5={sorted(st5)} forced={sorted(fs)}")
    print(f"[verify {label}] {len(forced_top5)} retrain days, "
          f"{len(forced_top5)-bad}/{len(forced_top5)} stored-top5 == forced set")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify-only", action="store_true",
                    help="re-read stored C1/C2 runs and check their top5 vs "
                         "forced/consensus sets without rewriting")
    args = ap.parse_args()

    preds = pd.read_parquet(PREDS)
    preds["trade_date"] = pd.to_datetime(preds["trade_date"])
    panel = pd.read_parquet(REINFER)
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    retrain_days = sorted(panel["trade_date"].unique())
    print(f"preds {len(preds)} rows, {preds['trade_date'].nunique()} days; "
          f"reinfer {len(panel)} rows, {len(retrain_days)} retrain days")

    forced = c0_forced_top5_from_executions(C0_EXEC, retrain_days)
    print(f"C0 forced_top5 sets: {len(forced)} retrain days "
          f"(sizes: {sorted({len(v) for v in forced.values()})})")

    c1 = build_c1_score(panel)
    c2 = build_c2_score(panel, forced)
    print(f"c1 override days: {len(c1)}, c2: {len(c2)}")

    if args.verify_only:
        c1_path = ROOT / "data/research/signals" / SIG_ID / C1_RUN / "predictions.parquet"
        c2_path = ROOT / "data/research/signals" / SIG_ID / C2_RUN / "predictions.parquet"
        # C1 has no forced set; check it is NOT degenerate (nonzero spread/day)
        s = pd.read_parquet(c1_path)
        s["trade_date"] = pd.to_datetime(s["trade_date"])
        zero_days = sum((s[s["trade_date"] == t]["score"].abs().max() < 1e-9)
                        for t in retrain_days)
        print(f"[verify C1] {zero_days}/{len(retrain_days)} retrain days are "
              f"all-zero (want 0)")
        verify_stored(c2_path, forced, "C2")
        return 0

    # override score (keep score_raw = same as original; only score drives the
    # engine ranking on rebalance days; score_raw is informational)
    for mode, scores, run_id in (("C1", c1, C1_RUN), ("C2", c2, C2_RUN)):
        df = preds.copy()
        df["signal_run_id"] = run_id
        override = pd.Series(np.nan, index=df.index, dtype=float)
        for t, s in scores.items():
            mask = (df["trade_date"] == t) & (df["instrument"].isin(s.index))
            override.loc[mask] = s.reindex(df.loc[mask, "instrument"]).values
        df["score"] = override.fillna(df["score"]).astype(float)
        # keep score_raw aligned to new score where overridden (informational)
        df["score_raw"] = override.fillna(df["score_raw"]).astype(float)
        # engine + store expect string dates (original parquet has str columns)
        df["trade_date"] = df["trade_date"].astype(str).str[:10]
        df["data_date"] = df["data_date"].astype(str).str[:10]
        write_run(SIG_ID, run_id, df)

    # verify what was just written (the artifact the backtest will consume)
    c2_path = ROOT / "data/research/signals" / SIG_ID / C2_RUN / "predictions.parquet"
    verify_stored(c2_path, forced, "C2")

    print(f"\nC1_RUN={C1_RUN}")
    print(f"C2_RUN={C2_RUN}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
