#!/usr/bin/env python3
"""Portfolio validation: test v3a+liquidity topK signal in portfolio context.

Usage:
    python scripts/research/portfolio_validation_v3a_liquidity.py

Reads: OOS rolling predictions (180d v3a+liq, 60d v3a+liq), labels, stock info.
Output reports to reports/research/portfolio_validation_*
"""

import sys, warnings
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
warnings.filterwarnings("ignore")

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "reports" / "research"
OUT_DIR.mkdir(parents=True, exist_ok=True)
TODAY = datetime.now().strftime("%Y%m%d")
MISSING_COLUMNS: list[str] = []

# ── Config ──

TOPKS = [10, 20, 50, 100]
HORIZONS = ["fwd_ret_60d_raw", "fwd_ret_180d_raw"]
INDUSTRY_CAPS = [None, 0.20, 0.30]  # None = no cap

PRED_PATHS = {
    "fwd_ret_60d_raw": (
        "data/research/signals/fwd_ret_60d_raw__daily_zscore/"
        "rolling__60d_v3a_plus_liquidity_indadj__v3a_liq_indadj_60d__"
        "fwd_ret_60d_raw__daily_zscore__2020-01-01_2025-12-31/predictions.parquet"
    ),
    "fwd_ret_180d_raw": (
        "data/research/signals/fwd_ret_180d_raw__daily_zscore/"
        "rolling__180d_v3a_plus_liquidity__v3a_plus_liquidity_180d__"
        "fwd_ret_180d_raw__daily_zscore__2020-01-01_2025-12-31/predictions.parquet"
    ),
}


def load_label(label_id: str) -> pd.DataFrame:
    from qsys.label.store import LabelStore
    return LabelStore().load_labels(label_id)[["trade_date", "instrument", "label_value"]]


def load_stock_info() -> pd.DataFrame:
    import sqlite3
    conn = sqlite3.connect(str(REPO / "data" / "meta.db"))
    df = pd.read_sql("select ts_code, name, industry from stock_basic", conn)
    conn.close()
    return df.rename(columns={"ts_code": "instrument"})


def run_portfolio(predictions: pd.DataFrame, label: pd.DataFrame,
                  stock_info: pd.DataFrame, horizon: str) -> list[dict]:
    """Run portfolio simulation for one horizon."""
    from itertools import product

    df = predictions.merge(label, on=["trade_date", "instrument"], how="inner")
    df = df.merge(stock_info, on="instrument", how="left")
    df["ym"] = df["trade_date"].str[:7]

    results = []

    for k in TOPKS:
        for cap in INDUSTRY_CAPS:
            for weight_mode in ["equal", "rank"]:
                monthly_returns = []
                monthly_industries = []
                monthly_n = []
                rebalance_dates = sorted(df["ym"].unique())

                for ym in rebalance_dates:
                    month_data = df[df["ym"] == ym].copy()

                    if month_data.empty:
                        continue

                    # Select top K by score
                    top = month_data.nlargest(max(k, 1), "score")
                    actual_k = min(len(top), k)

                    if actual_k == 0:
                        continue

                    # Industry exposure
                    ind_counts = top["industry"].value_counts()
                    max_ind_pct = (ind_counts / actual_k).max() if actual_k > 0 else 0

                    # Apply industry cap if needed
                    if cap is not None:
                        capped = top.copy()
                        # Ensure no industry > cap
                        for ind in capped["industry"].unique():
                            ind_mask = capped["industry"] == ind
                            if ind_mask.sum() > int(cap * k):
                                # Drop lowest-score stocks from this industry
                                ind_idx = capped[ind_mask].index
                                excess = ind_idx.sort_values()[:int(ind_mask.sum() - cap * k)]
                                capped = capped.drop(excess)
                        top = capped
                        actual_k = len(top)
                        if actual_k == 0:
                            continue

                    # Weights
                    if weight_mode == "rank":
                        ranks = top["score"].rank(ascending=True)
                        weights = ranks / ranks.sum()
                    else:
                        weights = pd.Series(1.0 / actual_k, index=top.index)

                    # Weighted return
                    port_ret = (top["label_value"] * weights).sum()
                    monthly_returns.append(port_ret)
                    monthly_industries.append(top["industry"].nunique())
                    monthly_n.append(actual_k)

                if not monthly_returns:
                    continue

                ret_series = pd.Series(monthly_returns)
                results.append({
                    "horizon": horizon,
                    "top_k": k,
                    "industry_cap": cap if cap is not None else 1.0,
                    "weight": weight_mode,
                    "n_rebalances": len(monthly_returns),
                    "mean_ret": ret_series.mean(),
                    "median_ret": ret_series.median(),
                    "hit_rate": (ret_series > 0).mean(),
                    "good_rate": (ret_series > 0.3).mean(),
                    "big_win_rate": (ret_series > 0.6).mean(),
                    "bad_rate": (ret_series < 0).mean(),
                    "worst_cohort": ret_series.min(),
                    "best_cohort": ret_series.max(),
                    "volatility": ret_series.std(),
                    "avg_n_stocks": np.mean(monthly_n),
                    "avg_n_industries": np.mean(monthly_industries),
                    "annualized_return": ret_series.mean() * 12,
                    "sharpe_approx": (ret_series.mean() / ret_series.std()) * np.sqrt(12)
                    if ret_series.std() > 0 else 0,
                })

    return results


def main():
    global MISSING_COLUMNS
    print("=" * 70)
    print("Portfolio Validation: v3a+liquidity TopK")
    print("=" * 70)

    stock_info = load_stock_info()
    all_results = []

    for horizon, pred_path in PRED_PATHS.items():
        path = REPO / pred_path
        if not path.exists():
            print(f"[SKIP] {horizon}: predictions not found at {path}")
            MISSING_COLUMNS.append(f"predictions_{horizon}")
            continue

        print(f"\n[{horizon}] Loading predictions...")
        pred = pd.read_parquet(path)
        print(f"  {len(pred)} rows, {pred['trade_date'].nunique()} dates")

        label = load_label(horizon)
        print(f"  Label loaded: {len(label)} rows")

        results = run_portfolio(pred, label, stock_info, horizon)
        all_results.extend(results)
        print(f"  {len(results)} portfolio configs evaluated")

    # ── Summary table ──
    print("\n" + "=" * 100)
    print("SUMMARY: Portfolio Validation Results")
    print("=" * 100)
    df_r = pd.DataFrame(all_results)
    df_r = df_r.sort_values(["horizon", "top_k", "weight", "industry_cap"])
    df_r.to_csv(OUT_DIR / f"portfolio_validation_v3a_liquidity_summary_{TODAY}.csv",
                index=False, float_format="%.4f")
    print(f"\n✅ Saved: {OUT_DIR / f'portfolio_validation_v3a_liquidity_summary_{TODAY}.csv'}")

    # Print key table
    print(f"\n{'horizon':<10s} {'k':>4s} {'cap':>5s} {'wt':>5s} {'mean_ret':>8s} {'hit':>5s} "
          f"{'bad':>5s} {'big_win':>6s} {'worst':>7s} {'ann_ret':>7s} {'sharpe':>7s}")
    print("-" * 80)
    for _, r in df_r.iterrows():
        cap_str = f"{r['industry_cap']:.0%}" if r['industry_cap'] < 1 else "none"
        print(f"{r['horizon']:<10s} {int(r['top_k']):>4d} {cap_str:>5s} {r['weight']:>5s} "
              f"{r['mean_ret']:>+8.4f} {r['hit_rate']:>5.1%} {r['bad_rate']:>5.1%} "
              f"{r['big_win_rate']:>6.1%} {r['worst_cohort']:>+7.3f} "
              f"{r['annualized_return']:>+7.1%} {r['sharpe_approx']:>7.2f}")

    # ── Key questions ──
    print("\n" + "=" * 70)
    print("KEY QUESTIONS")
    print("=" * 70)

    for horizon in df_r["horizon"].unique():
        sub = df_r[df_r["horizon"] == horizon]
        print(f"\n--- {horizon} ---")

        # Best risk-adjusted by sharpe
        best_sharpe = sub.loc[sub["sharpe_approx"].idxmax()]
        print(f"Best Sharpe:    Top{int(best_sharpe['top_k'])} "
              f"cap={best_sharpe['industry_cap']:.0%} wt={best_sharpe['weight']} "
              f"sharpe={best_sharpe['sharpe_approx']:.2f} mean_ret={best_sharpe['mean_ret']:+.4f}")

        # Does industry cap help?
        for k in TOPKS:
            eq = sub[(sub["top_k"] == k) & (sub["weight"] == "equal")]
            no_cap = eq[eq["industry_cap"] == 1.0]
            cap20 = eq[eq["industry_cap"] == 0.20]
            cap30 = eq[eq["industry_cap"] == 0.30]
            if len(no_cap) > 0 and len(cap20) > 0:
                print(f"  Top{k} equal: no_cap hit={no_cap.iloc[0]['hit_rate']:.1%} bad={no_cap.iloc[0]['bad_rate']:.1%} "
                      f"| cap20 hit={cap20.iloc[0]['hit_rate']:.1%} bad={cap20.iloc[0]['bad_rate']:.1%} "
                      f"| cap30 hit={cap30.iloc[0]['hit_rate']:.1%} bad={cap30.iloc[0]['bad_rate']:.1%}")

        # Does value come from few big winners?
        for k in TOPKS:
            eq = sub[(sub["top_k"] == k) & (sub["weight"] == "equal") & (sub["industry_cap"] == 1.0)]
            if len(eq) > 0:
                r = eq.iloc[0]
                print(f"  Top{k} equal: mean={r['mean_ret']:+.4f} big_win={r['big_win_rate']:.1%} "
                      f"good={r['good_rate']:.1%} bad={r['bad_rate']:.1%} "
                      f"worst={r['worst_cohort']:+.3f}")

    # ── Missing columns report ──
    if MISSING_COLUMNS:
        print(f"\n  [INFO] Missing columns/inputs: {MISSING_COLUMNS}")

    print(f"\n✅ All reports saved to {OUT_DIR}/")


if __name__ == "__main__":
    main()
