#!/usr/bin/env python3
"""Render the A0-A5 ablation report from the analysis JSONs.

Consumes:
  analysis.json   -> {layer1, layer2, layer3} from analyze_layers.py
  layer4.json     -> {run: {n_events, by_reason}} from analyze_layer4.py
  daily_summary.csv (runs-root) -> per-day score_delta_threshold

Prints the A0-A5 total table, rule-effect table, and the hard_stop /
score_delta / winner_trailing special checks with differential returns.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics as st
from pathlib import Path

import pandas as pd


def _pct(x: float | None, nd: int = 1) -> str:
    if x is None or not math.isfinite(x):
        return "—"
    return f"{x * 100:+.{nd}f}%"


def _num(x: float | None, nd: int = 1) -> str:
    if x is None or not math.isfinite(x):
        return "—"
    return f"{x:.{nd}f}"


def _median(xs: list[float | None]) -> float | None:
    xs = [x for x in xs if x is not None and math.isfinite(x)]
    return st.median(xs) if xs else None


def _mean(xs: list[float | None]) -> float | None:
    xs = [x for x in xs if x is not None and math.isfinite(x)]
    return sum(xs) / len(xs) if xs else None


def _pos(xs: list[float | None]) -> float | None:
    xs = [x for x in xs if x is not None and math.isfinite(x)]
    return sum(1 for x in xs if x > 0) / len(xs) if xs else None


# ---------------------------------------------------------------------------
def total_table(l1: dict, l2: dict) -> None:
    names = list(l1.keys())
    print("\n" + "=" * 100)
    print("A0-A5 TOTAL TABLE")
    print("=" * 100)
    rows = [
        ("Total return", lambda n: _pct(l1[n]["total_return"], 2)),
        ("CAGR", lambda n: _pct(l1[n]["cagr"], 2)),
        ("MaxDD", lambda n: _pct(l1[n]["maxdd"], 2)),
        ("Calmar", lambda n: _num(l1[n]["calmar"], 2)),
        ("Ann vol", lambda n: _pct(l1[n]["ann_vol"], 2)),
        ("Avg gross exposure", lambda n: _pct(l1[n]["avg_exposure"], 2)),
        ("Turnover (B)", lambda n: f"{l1[n]['turnover'] / 1e9:.2f}"),
        ("Fills", lambda n: str(l1[n]["fills"])),
        ("Episodes (closed)", lambda n: str(l2[n]["n_closed"])),
        ("Median holding (d)", lambda n: _num(l2[n]["median_holding"], 1)),
        ("Win rate", lambda n: _pct(l2[n]["win_rate"])),
        ("Avg episode ret", lambda n: _pct(l2[n]["avg_return"], 2)),
        ("Median episode ret", lambda n: _pct(l2[n]["median_return"], 2)),
        ("Median MFE", lambda n: _pct(l2[n]["median_MFE"])),
        ("Median MAE", lambda n: _pct(l2[n]["median_MAE"])),
        ("Median MAE p-tail (mean MAE)", lambda n: _pct(l2[n]["mean_MAE"])),
    ]
    hdr = "metric".ljust(26) + "".join(n.replace("A", "A").ljust(16) for n in names)
    print(hdr)
    print("-" * 100)
    for label, fn in rows:
        line = label.ljust(26)
        for n in names:
            line += str(fn(n)).ljust(16)
        print(line)

    # Yearly
    print("\nYearly return / MaxDD:")
    for n in names:
        y = l1[n]["yearly"]
        yrs = sorted(y.keys())
        line = f"  {n}: " + "  ".join(f"{yr}:{_pct(y[yr]['return'],0)}/{_pct(y[yr]['maxdd'],0)}" for yr in yrs)
        print(line)


def episode_bucket_table(l2: dict) -> None:
    names = list(l2.keys())
    print("\n" + "-" * 100)
    print("Layer 2 — holding buckets (count / median return / median MFE / median MAE)")
    for b in ["0-10d", "11-20d", "21-40d", "41-60d", "61-120d"]:
        line = f"  {b:9s}"
        for n in names:
            bb = l2[n]["holding_buckets"].get(b)
            if not bb or bb["n"] == 0:
                line += f"  {n.split('_')[0]:9s} n=0"
                continue
            line += f"  {n.split('_')[0]:9s} n={bb['n']:3d} m={_pct(bb['median_return'],0)} mfe={_pct(bb['median_MFE'],0)} mae={_pct(bb['median_MAE'],0)}"
        print(line)


def rule_effect_table(l3: dict) -> None:
    print("\n" + "=" * 100)
    print("RULE EFFECT TABLE (A1-A4 vs A0)")
    print("=" * 100)
    if not l3:
        print("  (A0 missing; cannot compute)")
        return
    hdr = "metric".ljust(24)
    for name in l3:
        hdr += name.replace("A1_", "A1 ").replace("A2_", "A2 ").replace("A3_", "A3 ").replace("A4_", "A4 ").ljust(14)
    hdr += "verdict".ljust(16)
    print(hdr)
    print("-" * 100)
    for metric in ["cagr", "maxdd", "calmar", "turnover", "median_return",
                   "top10pct_share", "survivor_41d_median", "avg_exposure"]:
        line = metric.ljust(24)
        for name in l3:
            v = l3[name].get(f"delta_{metric}")
            line += str(_pct(v, 2) if metric in ("cagr", "maxdd", "median_return", "survivor_41d_median") else _num(v, 2)).ljust(14)
        print(line)
    line = "rule_events".ljust(24)
    for name in l3:
        line += str(l3[name].get("n_rule_events", "—")).ljust(14)
    print(line)
    line = "verdict".ljust(24)
    for name in l3:
        line += str(l3[name]["verdict"]).ljust(14)
    print(line)


# ---------------------------------------------------------------------------
def hard_stop_check(l4_run: dict, run_dir: Path) -> None:
    events = l4_run.get("by_reason", {}).get("hard_stop", [])
    print("\n" + "=" * 100)
    print(f"HARD_STOP SPECIAL CHECK (n={len(events)})")
    print("=" * 100)
    if not events:
        print("  no events")
        return

    # score_delta_threshold per day from daily_summary
    daily = pd.read_csv(run_dir / "daily_summary.csv", dtype={"trade_date": str})
    thr = {}
    for _, r in daily.iterrows():
        v = r.get("score_delta_threshold")
        if v is not None and str(v).strip() not in ("", "nan"):
            thr[str(r["trade_date"])] = float(v)

    # Price-stopped-but-score-OK: score_delta20 above the day's pooled bottom-10% threshold.
    score_ok = 0
    score_bad = 0
    score_na = 0
    for e in events:
        d = e["exit_date"]
        t = thr.get(d)
        sd = e["old_score_delta20"]
        if t is None or sd is None:
            score_na += 1
        elif sd >= t:
            score_ok += 1
        else:
            score_bad += 1

    next60 = [e["old_return_next_60d"] for e in events]
    next20 = [e["old_return_next_20d"] for e in events]
    swap60 = [e["swap_edge_60d"] for e in events]
    swap20 = [e["swap_edge_20d"] for e in events]
    mfe = [e["old_MFE"] for e in events]
    mae = [e["old_MAE"] for e in events]
    realized = [e["old_return_at_exit"] for e in events]

    print(f"  score_delta20 above day threshold (score did NOT deteriorate): {score_ok}"
          f" | below (deteriorated): {score_bad} | n/a: {score_na}")
    print(f"  old next20d: mean={_pct(_mean(next20))} median={_pct(_median(next20))} pos={_pct(_pos(next20))}")
    print(f"  old next60d: mean={_pct(_mean(next60))} median={_pct(_median(next60))} pos={_pct(_pos(next60))}")
    print(f"  swap_edge20d: mean={_pct(_mean(swap20))} median={_pct(_median(swap20))} pos={_pct(_pos(swap20))}")
    print(f"  swap_edge60d: mean={_pct(_mean(swap60))} median={_pct(_median(swap60))} pos={_pct(_pos(swap60))}")
    print(f"  realized: mean={_pct(_mean(realized),1)}  MFE median={_pct(_median(mfe))}  MAE median={_pct(_median(mae))}")

    # A/B/C categorization on next60:
    a = [e for e in events if (e["old_return_next_60d"] or 0) < -0.05]
    c = [e for e in events if (e["old_return_next_60d"] or 0) > 0.05]
    b = [e for e in events if (e["old_return_next_60d"] is not None)
         and -0.05 <= e["old_return_next_60d"] <= 0.05]
    print(f"\n  next60d buckets: A(continues<-5%) n={len(a)} | B(sideways±5%) n={len(b)} | C(recovers>+5%) n={len(c)}")
    for lbl, grp in (("A falls", a), ("B sideways", b), ("C recovers", c)):
        if not grp:
            continue
        sw = [e["swap_edge_60d"] for e in grp]
        print(f"    {lbl}: n={len(grp)} swap60 mean={_pct(_mean(sw))} median={_pct(_median(sw))}")

    # The KEY question: among score-OK (price stopped but score fine), recovery rate.
    ok_grp = [e for e in events if thr.get(e["exit_date"]) is not None
              and e["old_score_delta20"] is not None and e["old_score_delta20"] >= thr.get(e["exit_date"])]
    bad_grp = [e for e in events if thr.get(e["exit_date"]) is not None
               and e["old_score_delta20"] is not None and e["old_score_delta20"] < thr.get(e["exit_date"])]
    for lbl, grp in (("score-OK stops", ok_grp), ("score-bad stops", bad_grp)):
        if not grp:
            continue
        n60 = [e["old_return_next_60d"] for e in grp]
        sw = [e["swap_edge_60d"] for e in grp]
        print(f"    {lbl}: n={len(grp)} next60 mean={_pct(_mean(n60))} median={_pct(_median(n60))} pos={_pct(_pos(n60))}"
              f" swap60 mean={_pct(_mean(sw))} median={_pct(_median(sw))}")


def score_delta_check(l4_run: dict, run_dir: Path) -> None:
    events = l4_run.get("by_reason", {}).get("score_delta", [])
    print("\n" + "=" * 100)
    print(f"SCORE_DELTA SPECIAL CHECK (n={len(events)})")
    print("=" * 100)
    if not events:
        print("  no events")
        return
    # profit / flat / loss buckets by realized return at exit
    profit = [e for e in events if (e["old_return_at_exit"] or 0) > 0.02]
    flat = [e for e in events if e["old_return_at_exit"] is not None and -0.02 <= e["old_return_at_exit"] <= 0.02]
    loss = [e for e in events if (e["old_return_at_exit"] or 0) < -0.02]
    for lbl, grp in (("profit (>+2%)", profit), ("flat (±2%)", flat), ("loss (<-2%)", loss)):
        if not grp:
            continue
        n20 = [e["old_return_next_20d"] for e in grp]
        n60 = [e["old_return_next_60d"] for e in grp]
        sw20 = [e["swap_edge_20d"] for e in grp]
        sw60 = [e["swap_edge_60d"] for e in grp]
        print(f"  {lbl}: n={len(grp)} next20 mean={_pct(_mean(n20))} median={_pct(_median(n20))} | "
              f"next60 mean={_pct(_mean(n60))} median={_pct(_median(n60))}")
        print(f"     swap20 mean={_pct(_mean(sw20))} median={_pct(_median(sw20))} pos={_pct(_pos(sw20))} | "
              f"swap60 mean={_pct(_mean(sw60))} median={_pct(_median(sw60))} pos={_pct(_pos(sw60))}")
    all_sw20 = [e["swap_edge_20d"] for e in events]
    all_sw60 = [e["swap_edge_60d"] for e in events]
    print(f"\n  all: swap20 mean={_pct(_mean(all_sw20))} median={_pct(_median(all_sw20))} pos={_pct(_pos(all_sw20))} | "
          f"swap60 mean={_pct(_mean(all_sw60))} median={_pct(_median(all_sw60))} pos={_pct(_pos(all_sw60))}")
    # Replacement quality: does the new symbol outperform the old over the swap window?
    print(f"  -> score_delta churn vs expectation deterioration: "
          f"swap_edge measures value added by replacement.")


def winner_trailing_check(l4_run: dict, run_dir: Path) -> None:
    events = l4_run.get("by_reason", {}).get("winner_trailing", [])
    print("\n" + "=" * 100)
    print(f"WINNER_TRAILING SPECIAL CHECK (exit n={len(events)})")
    print("=" * 100)
    if not events:
        print("  no events")
        return
    mfe = [e["old_MFE"] for e in events]
    realized = [e["old_return_at_exit"] for e in events]
    giveback = [m - r for m, r in zip(mfe, realized) if m is not None and r is not None]
    n20 = [e["old_return_next_20d"] for e in events]
    n60 = [e["old_return_next_60d"] for e in events]
    sw20 = [e["swap_edge_20d"] for e in events]
    sw60 = [e["swap_edge_60d"] for e in events]
    print(f"  MFE median={_pct(_median(mfe))} realized median={_pct(_median(realized))} "
          f"giveback mean={_pct(_mean(giveback))} median={_pct(_median(giveback))}")
    print(f"  old next20 mean={_pct(_mean(n20))} median={_pct(_median(n20))} pos={_pct(_pos(n20))}")
    print(f"  old next60 mean={_pct(_mean(n60))} median={_pct(_median(n60))} pos={_pct(_pos(n60))}")
    print(f"  swap20 mean={_pct(_mean(sw20))} median={_pct(_median(sw20))} pos={_pct(_pos(sw20))}")
    print(f"  swap60 mean={_pct(_mean(sw60))} median={_pct(_median(sw60))} pos={_pct(_pos(sw60))}")
    if len(events) < 30:
        print(f"  WARNING: n={len(events)} < 30 -> mark 'insufficient sample'")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--analysis", required=True)
    ap.add_argument("--layer4", required=True)
    ap.add_argument("--runs-root", required=True)
    ap.add_argument("--focus-run", default="A5_all", help="run for special checks")
    args = ap.parse_args()

    a = json.loads(Path(args.analysis).read_text())
    l4 = json.loads(Path(args.layer4).read_text())
    runs_root = Path(args.runs_root)

    total_table(a["layer1"], a["layer2"])
    episode_bucket_table(a["layer2"])
    rule_effect_table(a["layer3"])

    focus = l4.get(args.focus_run)
    if focus:
        run_dir = runs_root / args.focus_run
        hard_stop_check(focus, run_dir)
        score_delta_check(focus, run_dir)
        winner_trailing_check(focus, run_dir)
    else:
        print(f"\n  (no layer4 events for {args.focus_run})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
