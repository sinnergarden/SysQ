#!/usr/bin/env python3
"""A/B structural-comparison metrics for the stable-alpha skeleton search.

A (horizon decomposition): S60 / S180 / Blend at the winning 60d cadence.
B (exposure gate):         G0 / G1 / G2 / G3 at the winning 60d cadence.

Outputs, full-sample AND yearly (P0.1):
  CAGR, MaxDD, active return vs CSI800, RankIC (Spearman score vs fwd ret at
  the label horizon), Top5 forward excess (mean fwd of top-5 minus universe),
  turnover, orders, and right-tail concentration (max daily, p99 daily, top-5
  day contribution share, days > +5%).

B additionally reports per-year right-tail RETENTION of the gated variants vs
the G0 baseline: for each year, the ratio of yearly returns and the share of
the baseline's five best calendar days that the variant still captures.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import skew as sp_skew

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scratch.ablation.diag_common import (  # noqa: E402
    EXEC_ROOT,
    PRED_PATH,
    forward_return,
    load_benchmark,
    load_close_matrix,
    load_nav,
    load_rebalance_dates,
)

RUNS_A = ["A_S60_60d", "A_S180_60d", "E1_refresh_60d"]  # S60 / S180 / Blend
RUNS_B = ["E1_refresh_60d", "G1_market_risk", "G2_model_health", "G3_either"]
HORIZONS = (60, 180)


def _cagr(nav: pd.Series) -> float:
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    return nav.iloc[-1] ** (1.0 / years) - 1.0 if years > 0 else np.nan


def _maxdd(nav: pd.Series) -> float:
    return float((nav / nav.cummax() - 1.0).min())


def _yearly(nav: pd.Series) -> pd.Series:
    years = sorted({d.year for d in nav.index})
    out = {}
    for y in years:
        end = nav[nav.index.year == y].iloc[-1]
        base = 1.0 if y == years[0] else nav[nav.index.year == y - 1].iloc[-1]
        out[y] = end / base - 1.0
    return pd.Series(out)


def _right_tail(nav: pd.Series) -> dict:
    rets = nav.pct_change().dropna()
    total = float(nav.iloc[-1] - 1.0)
    top5 = rets.nlargest(5).sum()
    return {
        "max_daily": float(rets.max()),
        "p99_daily": float(rets.quantile(0.99)),
        "skew": float(sp_skew(rets)),
        "n_days_gt5pct": int((rets > 0.05).sum()),
        "top5_days_contrib": float(top5),
        "top5_days_share_of_total": float(top5 / total) if total > 0 else np.nan,
    }


def _signal_panel(signal_id: str, signal_run_id: str) -> pd.DataFrame:
    path = (
        Path("/home/liuming/.openclaw/workspace/SysQ/data/research/signals")
        / signal_id / signal_run_id / "predictions.parquet"
    )
    df = pd.read_parquet(path)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df[["trade_date", "instrument", "score"]].dropna()


def _rankic_top5(
    panel: pd.DataFrame, close_mat: pd.DataFrame, reb_dates: list, horizon: int
) -> tuple[float, float]:
    """Mean Spearman RankIC and mean Top5-forward-excess across rebalance
    dates at the given horizon (NaN rows dropped)."""
    ics, exs = [], []
    for t in reb_dates:
        sc = panel.loc[panel["trade_date"] == t, ["instrument", "score"]]
        if len(sc) < 5:
            continue
        fwd = forward_return(close_mat, t, horizon, sc["instrument"].tolist())
        if fwd.notna().sum() < 5:
            continue
        m = sc.set_index("instrument")["score"].rename("score").to_frame()
        m["fwd"] = fwd
        m = m.dropna(subset=["fwd"])
        if len(m) < 5:
            continue
        ic = m["score"].corr(m["fwd"], method="spearman")
        if np.isfinite(ic):
            ics.append(float(ic))
        m2 = m.sort_values("score", ascending=False).head(5)  # top-5 BY SCORE
        ex = float(m2["fwd"].mean() - m["fwd"].mean())
        exs.append(ex)
    return float(np.mean(ics)), float(np.mean(exs))


def _yearly_rankic_top5(panel, close_mat, reb_dates, horizon) -> pd.DataFrame:
    rows = []
    for t in reb_dates:
        sc = panel.loc[panel["trade_date"] == t, ["instrument", "score"]]
        if len(sc) < 5:
            continue
        fwd = forward_return(close_mat, t, horizon, sc["instrument"].tolist())
        if fwd.notna().sum() < 5:
            continue
        m = sc.set_index("instrument")["score"].rename("score").to_frame()
        m["fwd"] = fwd
        m = m.dropna(subset=["fwd"])
        if len(m) < 5:
            continue
        ic = m["score"].corr(m["fwd"], method="spearman")
        if not np.isfinite(ic):
            ic = np.nan
        m2 = m.sort_values("score", ascending=False).head(5)
        ex = float(m2["fwd"].mean() - m["fwd"].mean()) if len(m2) else np.nan
        rows.append({"year": t.year, "rankic": ic, "top5_excess": ex})
    df = pd.DataFrame(rows)
    return df.groupby("year")[["rankic", "top5_excess"]].mean()


def _run_summary(run_dir: Path) -> dict:
    manifest = json.loads((run_dir / "manifest.json").read_text())
    metrics = json.loads((run_dir / "metrics.json").read_text())
    nav = load_nav(run_dir)
    bench = load_benchmark("000906.SH")
    bench_rebased = bench / bench.iloc[0]
    span_bench = bench_rebased[bench_rebased.index.isin(nav.index)]
    total = float(nav.iloc[-1] - 1.0)
    active = total - float(span_bench.iloc[-1] - 1.0)
    return {
        "name": run_dir.name,
        "signal_id": manifest.get("signal_id"),
        "signal_run_id": manifest.get("signal_run_id"),
        "gate": (manifest.get("exposure_gate") or {}).get("mode", "none"),
        "total_ret": total,
        "cagr": _cagr(nav),
        "maxdd": _maxdd(nav),
        "active_vs_csi800": active,
        "turnover": metrics.get("turnover_total"),
        "orders": metrics.get("order_count_total"),
        "trading_days": metrics.get("trading_day_count"),
        "yearly": _yearly(nav),
        "right_tail": _right_tail(nav),
        "nav": nav,
    }


def print_full_table(rows: list[dict]) -> None:
    print("\n=== Full-sample ===")
    print(f"{'run':<16}{'gate':<14}{'total':>8}{'cagr':>8}{'maxdd':>8}"
          f"{'active':>8}{'turnover':>10}{'orders':>7}{'days':>6}")
    for r in rows:
        print(f"{r['name']:<16}{str(r['gate']):<14}{r['total_ret']:>8.2f}"
              f"{r['cagr']:>8.3f}{r['maxdd']:>8.1%}{r['active_vs_csi800']:>8.2f}"
              f"{r['turnover'] / 1e9 if r['turnover'] else np.nan:>9.2f}B"
              f"{r['orders']:>7}{r['trading_days']:>6}")

    print("\n=== Yearly returns (P0.1) ===")
    years = sorted({y for r in rows for y in r["yearly"].index})
    print(f"{'run':<16}" + "".join(f"{y:>8}" for y in years))
    for r in rows:
        print(f"{r['name']:<16}" + "".join(
            f"{r['yearly'].get(y, np.nan):>8.1%}" for y in years))

    print("\n=== Right-tail concentration (full-sample daily) ===")
    print(f"{'run':<16}{'max_daily':>10}{'p99':>8}{'skew':>7}{'>5% days':>9}"
          f"{'top5_contrib':>13}{'top5_share':>11}")
    for r in rows:
        rt = r["right_tail"]
        print(f"{r['name']:<16}{rt['max_daily']:>10.2%}{rt['p99_daily']:>8.2%}"
              f"{rt['skew']:>7.2f}{rt['n_days_gt5pct']:>9}"
              f"{rt['top5_days_contrib']:>13.2%}"
              f"{rt['top5_days_share_of_total']:>10.0%}")


def print_yearly_signal_table(run_rows: list[dict], close_mat: pd.DataFrame) -> None:
    """RankIC + Top5 forward excess at each run's label horizon, per year."""
    print("\n=== RankIC / Top5 forward excess (mean over rebalance cohorts) ===")
    for r in run_rows:
        panel = _signal_panel(r["signal_id"], r["signal_run_id"])
        reb = load_rebalance_dates(EXEC_ROOT / r["name"])
        print(f"\n--- {r['name']} ({r['signal_id']}) ---")
        for h in HORIZONS:
            yr = _yearly_rankic_top5(panel, close_mat, reb, h)
            ic_fs, ex_fs = _rankic_top5(panel, close_mat, reb, h)
            print(f"  horizon {h:>3}d: RankIC {ic_fs:+.4f} | top5_excess {ex_fs:+.3f}"
                  f" | yearly mean RankIC:")
            for y, row in yr.iterrows():
                print(f"    {y}: {row['rankic']:+.4f} | top5_excess {row['top5_excess']:+.3f}")


def print_retention(rows: list[dict], baseline_name: str = "E1_refresh_60d") -> None:
    """B: per-year right-tail retention of gated variants vs baseline."""
    base = next(r for r in rows if r["name"] == baseline_name)
    others = [r for r in rows if r["name"] != baseline_name]
    if not others:
        return
    years = sorted({y for r in rows for y in r["yearly"].index})
    print("\n=== Right-tail retention vs baseline (2023/2024/2025 focus) ===")
    for r in others:
        print(f"\n--- {r['name']} vs {baseline_name} ---")
        for y in years:
            base_y = float(base["yearly"].get(y, np.nan))
            r_y = float(r["yearly"].get(y, np.nan))
            if not np.isfinite(base_y) or not np.isfinite(r_y):
                continue
            ratio = r_y / base_y if base_y != 0 else np.nan
            # Baseline's five best days that year vs what the variant kept.
            base_daily = base["nav"].pct_change().dropna()
            var_daily = r["nav"].pct_change().dropna()
            bseg = base_daily[base_daily.index.year == y]
            vseg = var_daily[var_daily.index.year == y]
            top5_dates = bseg.nlargest(5).index
            kept = float(vseg.loc[top5_dates].sum()) if len(top5_dates) else np.nan
            base5 = float(bseg.nlargest(5).sum())
            keep_share = kept / base5 if base5 != 0 else np.nan
            print(f"  {y}: yearly {base_y:+.1%} -> {r_y:+.1%} "
                  f"(retain {ratio:+.0%}) | top-5 baseline days captured {keep_share:+.0%}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=",".join(RUNS_A),
                    help="comma-separated run names (relative to EXEC_ROOT)")
    ap.add_argument("--label", default="A", help="report label: A or B")
    ap.add_argument("--baseline", default="E1_refresh_60d")
    ap.add_argument("--no-signal-metrics", action="store_true")
    args = ap.parse_args()

    names = [s.strip() for s in args.runs.split(",") if s.strip()]
    close_mat = load_close_matrix()
    rows = []
    for name in names:
        run_dir = EXEC_ROOT / name
        if not (run_dir / "manifest.json").exists():
            print(f"[skip] {name}: manifest missing", file=sys.stderr)
            continue
        rows.append(_run_summary(run_dir))

    print(f"\n################## Experiment {args.label} ##################")
    print_full_table(rows)
    if not args.no_signal_metrics:
        print_yearly_signal_table(rows, close_mat)
    if args.label == "B":
        print_retention(rows, baseline_name=args.baseline)

    # CSI800 yearly for context.
    bench = load_benchmark("000906.SH")
    by = {}
    for y in sorted({d.year for d in bench.index}):
        if y < 2021 or y > 2026:
            continue
        sub = bench[bench.index.year == y]
        base = bench[bench.index.year == y - 1].iloc[-1] if y > 2021 else bench.iloc[0]
        by[y] = sub.iloc[-1] / base - 1.0
    print("\n=== CSI800 yearly ===")
    print(pd.Series(by).round(4).to_string())


if __name__ == "__main__":
    main()
