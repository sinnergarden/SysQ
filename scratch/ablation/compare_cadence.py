#!/usr/bin/env python3
"""Compare the E1 refresh-cadence variants (weekly / 5d / 20d / 60d).

Full-sample + yearly stats using the P0.1 conventions:
  2021  = NAV(year_end) / initial_capital - 1
  2022+ = NAV(year_end) / NAV(prev_year_end) - 1
CAGR uses actual elapsed trading span (first..last nav date).
MaxDD from the daily NAV.  Active return = strategy cumulative return minus
CSI800 cumulative return over the same span.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scratch.ablation.diag_common import (  # noqa: E402
    EXEC_ROOT,
    INIT_CAPITAL,
    load_benchmark,
    load_nav,
)

RUNS = ["E1_rank_exit", "E1_refresh_5d", "E1_refresh_20d", "E1_refresh_60d"]


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
        # nav is normalised to initial capital; the initial base is 1.0.
        base = 1.0 if y == years[0] else nav[nav.index.year == y - 1].iloc[-1]
        out[y] = end / base - 1.0
    return pd.Series(out)


def main() -> None:
    bench = load_benchmark("000906.SH")
    bench_rebased = bench / bench.iloc[0]
    rows = []
    yearly_rows = []
    for name in RUNS:
        run_dir = EXEC_ROOT / name
        nav = load_nav(run_dir)
        span_bench = bench_rebased[bench_rebased.index.isin(nav.index)]
        cagr = _cagr(nav)
        mdd = _maxdd(nav)
        tot = nav.iloc[-1] - 1.0
        active = float((nav.iloc[-1] - 1.0) - (span_bench.iloc[-1] - 1.0))
        metrics = {}
        mpath = run_dir / "metrics.json"
        if mpath.exists():
            import json

            m = json.loads(mpath.read_text())
            metrics = m
        rows.append({
            "run": name,
            "total_ret": tot,
            "cagr": cagr,
            "maxdd": mdd,
            "active_vs_csi800": active,
            "turnover": metrics.get("turnover_total"),
            "orders": metrics.get("order_count_total"),
            "trading_days": metrics.get("trading_day_count"),
        })
        y = _yearly(nav)
        y["run"] = name
        yearly_rows.append(y)
    tab = pd.DataFrame(rows).set_index("run")
    print("=== Full-sample ===")
    print(tab.round(4).to_string())
    print("\n=== Yearly returns (P0.1) ===")
    yearly = pd.DataFrame(yearly_rows).set_index("run")
    print(yearly.T.round(4).to_string())
    bench_y = {}
    for y in sorted({d.year for d in bench.index}):
        if y < 2021 or y > 2026:
            continue
        sub = bench[bench.index.year == y]
        base = bench[bench.index.year == y - 1].iloc[-1] if y > 2021 else bench.iloc[0]
        bench_y[y] = sub.iloc[-1] / base - 1.0
    print("\n=== CSI800 yearly ===")
    print(pd.Series(bench_y).round(4).to_string())


if __name__ == "__main__":
    main()
