#!/usr/bin/env python3
"""Cohort-level forward selection edge for the 4 raw-ranking phases (Sec 3-6).

For every retrain-day cohort (per phase), take the fresh model's raw-ranking
Top5 (score = zscore(raw), no cap) and measure two forward excesses at @60 and
@180:

  A  top5_mean_fwd - same-day scored-universe EW mean fwd
  B  top5_mean_fwd - CSI800 (000906.SH) fwd over the same horizon

Strict close-to-close over exact trading-calendar rows (no stale-price
fallback): a name's fwd is NaN when either endpoint close is missing; a cohort
is null at horizon h when <3 of 5 Top5 names are measured OR the universe EW
has <30 names OR the benchmark close is missing at t+h.

Core outputs (per phase per horizon): valid cohort count, mean/median edge,
q25/q75, positive-edge rate, worst cohort, p90/max, per-year mean/median +
positive ratio, and the phase-robustness judgment inputs (within-phase
dispersion, between-phase median diff, single-cohort dependence via
drop-largest-winner).

Sec 6 right tail: per cohort 180d buckets >+20/+50/+100% and <-20/-40%,
big-winner cross-phase presence, cohort median edge excluding the cohort's
largest 180d winner (top1) and excluding the top5 winners (top5 excl).

Run from the MAIN SysQ cwd (qsys + data resolve).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scratch.ablation.diag_common import (  # noqa: E402
    END_DATE,
    INIT_CAPITAL,
    START_DATE,
    load_benchmark,
    load_close_matrix,
)

ROOT = Path("/home/liuming/.openclaw/workspace/SysQ")
EXPERIMENT = "financial_rc_180d_rolling_5y_to_202607_v3"
SIG_ID = "fwd_ret_180d_raw__daily_zscore"
SRID = (
    "rolling__financial_rc_180d_rolling_5y_to_202607_v3__"
    "v3a_growth_financial_180d__fwd_ret_180d_raw__daily_zscore__"
    "2021-01-01_2026-07-31"
)
WINDOWS_CSV = ROOT / "data/research/experiments" / EXPERIMENT / "rolling_windows.csv"

PHASES = {"p0": 0, "p5": 5, "p10": 10, "p15": 15}
HORIZONS = (60, 180)
SIGNS_DIR = ROOT / "data/research/signals" / SIG_ID

# stored (capped) baseline run — for the cap-tie regression (Sec 1)
STORED_PARQUET = SIGNS_DIR / SRID / "predictions.parquet"


def run_parquet(phase: str) -> Path:
    rid = f"{SIG_ID}__rr_{phase}__rawrank__{EXPERIMENT}"
    return SIGNS_DIR / rid / "predictions.parquet"


def load_shifted_predict_starts(k: int) -> list[str]:
    """Shifted window predict_starts (the phase's retrain days)."""
    from qsys.data.calendar import get_trading_calendar

    cal = get_trading_calendar("2017-01-01", "2026-12-31")
    pos = {d: i for i, d in enumerate(cal)}
    wins = pd.read_csv(WINDOWS_CSV)
    out = []
    for ps in wins["predict_start"]:
        i = pos[ps]
        out.append(cal[i + k])
    return out


def load_bench_fwd(close_idx: pd.DatetimeIndex) -> pd.Series:
    """benchmark forward close-to-close over close-matrix rows (strict)."""
    bench = load_benchmark("000906.SH", window=False)
    b = bench.reindex(close_idx)
    return b


def top5_names(day_score: pd.Series) -> list[str]:
    return day_score.sort_values(ascending=False).head(5).index.tolist()


def cohort_edge(
    fwd: pd.Series, top5: list[str], n_top_min: int = 3
) -> tuple[float, float]:
    """top5 mean fwd and universe-EW mean fwd for one cohort.

    fwd: Series(instrument -> fwd ret), NaN where endpoint close missing.
    top5_mean requires >= n_top_min measured names else NaN.
    """
    f5 = fwd.reindex(top5).dropna()
    if len(f5) < n_top_min:
        return np.nan, np.nan
    univ = fwd.dropna()
    if len(univ) < 30:
        return np.nan, np.nan
    return float(f5.mean()), float(univ.mean())


def build_panel(
    phase: str, cm: pd.DataFrame, bench_close: pd.Series,
    retrain_days: list[str],
) -> pd.DataFrame:
    """Rows = retrain-day cohorts; forward metrics at each horizon."""
    df = pd.read_parquet(run_parquet(phase))
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.dropna(subset=["score"])

    idx = cm.index
    rows = []
    for t_str in retrain_days:
        t = pd.Timestamp(t_str)
        if t not in idx:
            continue
        pos = idx.get_loc(t)
        day = df[df["trade_date"] == t]
        if day.empty:
            continue
        sc = day.set_index("instrument")["score"]
        sc = sc[sc.notna()]
        if len(sc) < 30:
            continue
        top5 = top5_names(sc)
        fwd5s = {h: cm.iloc[pos] for h in HORIZONS}  # placeholder, overwritten
        row = {
            "trade_date": t,
            "year": int(t.year),
            "n_scored": int(len(sc)),
            "top5": top5,
        }
        ok = True
        for h in HORIZONS:
            j = pos + h
            if j >= len(idx):
                row[f"edgeA_{h}"], row[f"edgeB_{h}"] = np.nan, np.nan
                ok = False
                continue
            # fwd for all names scored today
            t0 = cm.iloc[pos]
            t1 = cm.iloc[j]
            fwd = (t1 / t0 - 1.0).reindex(sc.index)
            f5m, univm = cohort_edge(fwd, top5)
            # benchmark
            b0, b1 = bench_close.iloc[pos], bench_close.iloc[j]
            if np.isfinite(b0) and np.isfinite(b1):
                bfwd = float(b1 / b0 - 1.0)
            else:
                bfwd = np.nan
            row[f"edgeA_{h}"] = (f5m - univm) * 100 if np.isfinite(f5m) else np.nan
            row[f"edgeB_{h}"] = (f5m - bfwd) * 100 if np.isfinite(f5m) and np.isfinite(bfwd) else np.nan
            row[f"top5fwd_{h}"] = f5m * 100 if np.isfinite(f5m) else np.nan
            row[f"univfwd_{h}"] = univm * 100 if np.isfinite(univm) else np.nan
            # right-tail bucket counts over measured top5 names @180
            if h == 180:
                f180 = fwd.reindex(top5).dropna()
                row["n_fwd180"] = len(f180)
                row["n_gt20"] = float((f180 > 0.20).sum())
                row["n_gt50"] = float((f180 > 0.50).sum())
                row["n_gt100"] = float((f180 > 1.00).sum())
                row["n_ltm20"] = float((f180 < -0.20).sum())
                row["n_ltm40"] = float((f180 < -0.40).sum())
                row["top1_180"] = float(f180.max()) if len(f180) else np.nan
                row["top5excl_top1_180"] = (
                    float(f180.sort_values(ascending=False).iloc[1:].mean()) if len(f180) >= 2 else np.nan
                )
                # excl cohort's 5 biggest 180d winners entirely
                row["top5excl_all_180"] = (
                    float(f180.mean()) if len(f180) else np.nan
                )
        rows.append(row)
    return pd.DataFrame(rows)


def summarize(panel: pd.DataFrame, h: int, which: str) -> dict:
    col = f"{which}_{h}"
    s = panel[col].dropna()
    return {
        "h": h, "excess": which,
        "n": len(s),
        "mean": s.mean(), "median": s.median(),
        "q25": s.quantile(0.25), "q75": s.quantile(0.75),
        "pos_rate": (s > 0).mean(),
        "worst": s.min(), "p90": s.quantile(0.90), "max": s.max(),
    }


def yearly_summary(panel: pd.DataFrame, h: int, which: str) -> pd.DataFrame:
    col = f"{which}_{h}"
    d = panel.dropna(subset=[col])
    out = {}
    for y, g in d.groupby("year"):
        s = g[col]
        out[y] = {
            "n": len(s), "mean": s.mean(), "median": s.median(),
            "pos_rate": (s > 0).mean(),
        }
    return pd.DataFrame(out).T


def print_phase(phase: str, panel: pd.DataFrame) -> None:
    print(f"\n########## Phase {phase.upper()} ##########")
    print(f"retrain cohorts: {len(panel)}  "
          f"days {panel['trade_date'].min().date()}..{panel['trade_date'].max().date()}")
    for h in HORIZONS:
        for which in ("edgeA", "edgeB"):
            r = summarize(panel, h, which)
            print(f"  @{h} {which:>5}: n={r['n']:3d} mean={r['mean']:+6.2f}pp "
                  f"med={r['median']:+6.2f} q25={r['q25']:+6.2f} q75={r['q75']:+6.2f} "
                  f"pos_rate={r['pos_rate']:.2f} worst={r['worst']:+7.2f} "
                  f"p90={r['p90']:+7.2f} max={r['max']:+7.2f}")
        print("  --- yearly (median / pos_rate) ---")
        yr = yearly_summary(panel, h, "edgeA")
        for y, r in yr.iterrows():
            print(f"    {int(y)}: n={int(r['n'])} med={r['median']:+6.2f}pp "
                  f"pos={r['pos_rate']:.2f}")


def sec6_buckets(panels: dict) -> None:
    print("\n### Sec 6 right tail @180 (per-cohort mean over measured top5 names) ###")
    cols = [("n_gt20", ">+20%"), ("n_gt50", ">+50%"), ("n_gt100", ">+100%"),
            ("n_ltm20", "<-20%"), ("n_ltm40", "<-40%")]
    for ph, p in panels.items():
        print(f"  {ph.upper()}: ", end="")
        for col, label in cols:
            v = (p[col].sum() / p["n_fwd180"].sum() * 100)
            print(f"{label}={v:4.1f}% ", end="")
        print()


def sec6_bigwinner(panels: dict) -> None:
    print("\n### Sec 6 big-winner cross-phase presence ###")
    print(f"  {'phase':<6}{'cohorts':>8}{'any >+100%':>13}{'frac':>7}{'top1med':>9}"
          f"{'excl_top1_med':>14}{'excl_all_med':>13}")
    for ph, p in panels.items():
        d = p.dropna(subset=["n_gt100"])
        frac = (d["n_gt100"] > 0).mean()
        print(f"  {ph.upper():<6}{len(d):>8}{int((d['n_gt100']>0).sum()):>13}"
              f"{frac:>7.2f}{d['top1_180'].median():>9.1%}"
              f"{d['top5excl_top1_180'].median():>14.1%}"
              f"{d['top5excl_all_180'].median():>13.1%}")


def cap_tie_regression() -> None:
    """Sec 1: how much of the stored capped Top5 differs from raw-ranking Top5.

    For each ORIGINAL retrain day, compare stored(capped) top5 vs P0 raw top5:
    count of cap-tie days, and how many top5 slots flip.
    """
    stored = pd.read_parquet(STORED_PARQUET)
    stored["trade_date"] = pd.to_datetime(stored["trade_date"])
    raw = pd.read_parquet(run_parquet("p0"))
    raw["trade_date"] = pd.to_datetime(raw["trade_date"])
    wins = pd.read_csv(WINDOWS_CSV)

    n_cap, n_diff, slots_flipped, n_same = 0, 0, 0, 0
    for ps in wins["predict_start"]:
        t = pd.Timestamp(ps)
        sd = stored[stored["trade_date"] == t].set_index("instrument")
        rd = raw[raw["trade_date"] == t].set_index("instrument")
        common = sorted(set(sd.index) & set(rd.index))
        sd = sd.loc[common]
        rd = rd.loc[common]
        s5 = set(sd.sort_values("score_raw", ascending=False).head(5).index)
        r5 = set(rd.sort_values("score", ascending=False).head(5).index)
        capped = bool((sd["score_raw"].sort_values(ascending=False).iloc[4]) >= 2.999)
        n_cap += int(capped)
        if s5 == r5:
            n_same += 1
        else:
            n_diff += 1
            slots_flipped += len(s5 ^ r5)
    total_slots = 5 * len(wins)
    print(f"\n### Sec 1 cap-tie regression (stored capped vs P0 raw, "
          f"{len(wins)} retrain days) ###")
    print(f"  cap-tie days (>=5 names at +3.0): {n_cap}/{len(wins)}")
    print(f"  top5 identical: {n_same}/{len(wins)};  differ: {n_diff}/{len(wins)}")
    print(f"  top5 slots that flip vs tiebreak lottery: {slots_flipped}/{total_slots} "
          f"({slots_flipped/total_slots:.0%})")


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--phases", default="p0,p5,p10,p15",
                    help="comma-separated phases to analyze")
    args = ap.parse_args()
    want = [p.strip() for p in args.phases.split(",") if p.strip()]

    cm = load_close_matrix()
    bench_close = load_bench_fwd(cm.index)
    panels = {}
    for ph, k in PHASES.items():
        if ph not in want:
            continue
        if not run_parquet(ph).exists():
            print(f"[skip] {ph}: run parquet missing", file=sys.stderr)
            continue
        retrain_days = load_shifted_predict_starts(k)
        panel = build_panel(ph, cm, bench_close, retrain_days)
        panels[ph] = panel
        print_phase(ph, panel)

    cap_tie_regression()
    sec6_buckets(panels)
    sec6_bigwinner(panels)

    # phase-robustness read: between-phase median spread vs within-phase
    # dispersion at @60 and @180 (edgeA)
    print("\n### Phase-robustness read ###")
    for h in (60, 180):
        meds = {ph: panels[ph][f"edgeA_{h}"].dropna().median()
                for ph in panels}
        iqrs = {ph: panels[ph][f"edgeA_{h}"].dropna().quantile(0.75)
                - panels[ph][f"edgeA_{h}"].dropna().quantile(0.25)
                for ph in panels}
        med_vals = np.array(list(meds.values()))
        print(f"  @{h} median edgeA per phase: "
              + ", ".join(f"{ph}={meds[ph]:+.2f}" for ph in panels))
        print(f"      between-phase median spread = {med_vals.max()-med_vals.min():+.2f}pp; "
              f"within-phase IQR = " + ", ".join(f"{ph}={iqrs[ph]:.2f}" for ph in panels))


if __name__ == "__main__":
    main()
