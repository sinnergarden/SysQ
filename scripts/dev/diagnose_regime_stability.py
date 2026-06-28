#!/usr/bin/env python3
"""Diagnostic: regime stability analysis for 180d v3a+liq pure baseline.

Experiment 1 — Industry decomposition of IC:
  original IC/RankIC → within-industry IC/RankIC → industry-allocation IC
Experiment 2 — TopK quality in 2024/2025:
  monthly Top20/50/100, hit_rate, bad_rate, big_win_rate, industry distribution

Output:
  stdout + artifacts/diagnostics/v3a_liq_regime/*.csv
"""
import sys, json, warnings
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

from qsys.signal.store import SignalStore
from qsys.label.store import LabelStore

OUT = Path("artifacts/diagnostics/v3a_liq_regime")
OUT.mkdir(parents=True, exist_ok=True)

# ═══════════════════════════════════════════════════════════════════
# 1. Load data
# ═══════════════════════════════════════════════════════════════════
print("=" * 60)
print("Loading signal + label + industry...")
print("=" * 60)

s = SignalStore()
SIGNAL_RUN = "rolling__180d_v3a_plus_liquidity_pure__v3a_liq_pure_180d__fwd_ret_180d_raw__daily_zscore__2020-01-01_2025-12-31"
sig = s.load_signal_run("fwd_ret_180d_raw__daily_zscore", SIGNAL_RUN)

lab = LabelStore().load_labels("fwd_ret_180d_raw")
lab["trade_date"] = lab["trade_date"].astype(str).str[:10]

# Merge signal + label
sig["ts_code"] = sig["instrument"]
lab["ts_code"] = lab["instrument"]
df = sig[["trade_date", "ts_code", "score"]].merge(
    lab[["trade_date", "ts_code", "label_value"]],
    on=["trade_date", "ts_code"], how="left",
).dropna(subset=["label_value"])
print(f"  Joined: {len(df)} rows, {df.trade_date.nunique()} trading days")
print(f"  Range: {df.trade_date.min()} → {df.trade_date.max()}")

# Load industry from Qlib
try:
    from qlib.data import D
    from qsys.data.adapter import QlibAdapter
    QlibAdapter().init_qlib()
    insts = D.instruments("csi800")
    ind_raw = D.features(insts, ["$industry"], start_time="2024-01-01", end_time="2024-01-02", freq="day")
    ind_map = ind_raw.reset_index()[["instrument", "$industry"]].drop_duplicates()\
        .rename(columns={"instrument": "ts_code", "$industry": "industry"})
except Exception as e:
    print(f"  Qlib industry failed ({e}), trying Tushare...")
    try:
        import tushare as ts
        tb = ts.pro_api().stock_basic()
        tb["ts_code"] = tb["ts_code"].str.replace(".", "", regex=False)
        ind_map = tb[["ts_code", "industry"]].dropna()
    except Exception as e2:
        print(f"  Tushare also failed ({e2}), using placeholder")
        ind_map = pd.DataFrame({"ts_code": df["ts_code"].unique(), "industry": "unknown"})

df = df.merge(ind_map, on="ts_code", how="left")
print(f"  With industry: {df['industry'].notna().sum()}/{len(df)} ({df['industry'].nunique()} industries)")

# Year
df["year"] = pd.to_numeric(df["trade_date"].str[:4])
df["month"] = df["trade_date"].str[:7]


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

def daily_ic_stats(sub: pd.DataFrame, score_col: str, label_col: str,
                   by_industry: bool = False) -> dict:
    """Compute daily IC + RankIC. If by_industry, compute within-industry zscore first."""
    sub = sub.copy()
    if by_industry:
        sub["_zs"] = sub.groupby(["trade_date", "industry"], group_keys=True)[score_col].rank(pct=True)
        score_col = "_zs"

    daily = sub.groupby("trade_date").apply(
        lambda g: pd.Series({
            "ic": g[score_col].corr(g[label_col]),
            "rank_ic": g[score_col].rank().corr(g[label_col].rank()),
        }), include_groups=False
    ).reset_index()
    daily = daily.dropna(subset=["ic"])
    if daily.empty:
        return {"mean": 0, "std": 1, "ir": 0, "n_days": 0}
    ic_m = daily["ic"].mean()
    ic_s = daily["ic"].std()
    daily = daily.dropna(subset=["ic"])
    if daily.empty:
        return {"mean": 0, "std": 1, "ir": 0, "n_days": 0}
    ic_m = daily["ic"].mean()
    ic_s = daily["ic"].std()
    rk_m = daily["rank_ic"].mean()
    rk_s = daily["rank_ic"].std()
    return {
        "mean": ic_m, "std": ic_s, "ir": ic_m / ic_s if ic_s > 0 else 0,
        "rank_mean": rk_m, "rank_std": rk_s, "rank_ir": rk_m / rk_s if rk_s > 0 else 0,
        "n_days": len(daily),
    }


# ═══════════════════════════════════════════════════════════════════
# Experiment 1: Industry decomposition of IC
# ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("EXPERIMENT 1: IC Decomposition by Industry")
print("=" * 60)

rows = []
for method, label, by_ind in [
    ("original", "Original (raw score)", False),
    ("within_ind", "Within-industry (rank pct)", True),
]:
    overall = daily_ic_stats(df, "score", "label_value", by_industry=by_ind)
    rows.append({
        "method": label,
        "period": "Overall",
        "ic_mean": overall["mean"], "icir": overall["ir"],
        "rank_ic_mean": overall["rank_mean"], "rank_icir": overall["rank_ir"],
    })
    for year in sorted(df["year"].unique()):
        yr = df[df["year"] == year]
        if len(yr) < 100:
            continue
        s_yr = daily_ic_stats(yr, "score", "label_value", by_industry=by_ind)
        rows.append({
            "method": label,
            "period": str(year),
            "ic_mean": s_yr["mean"], "icir": s_yr["ir"],
            "rank_ic_mean": s_yr["rank_mean"], "rank_icir": s_yr["rank_ir"],
        })

# Industry-allocation IC
def ind_allocation_ic(sub: pd.DataFrame) -> dict:
    """Industry allocation IC: each trade_date, industry mean(score) vs mean(label)."""
    ind_daily = sub.groupby(["trade_date", "industry"]).agg(
        ind_score=("score", "mean"),
        ind_label=("label_value", "mean"),
    ).reset_index()
    daily = ind_daily.groupby("trade_date").apply(
        lambda g: pd.Series({
            "ic": g["ind_score"].corr(g["ind_label"]),
            "rank_ic": g["ind_score"].rank().corr(g["ind_label"].rank()),
        }), include_groups=False
    ).reset_index().dropna(subset=["ic"])
    if daily.empty:
        return {"mean": 0, "std": 1, "ir": 0, "rank_mean": 0, "rank_ir": 0, "n_days": 0}
    return {
        "mean": daily["ic"].mean(), "std": daily["ic"].std(),
        "ir": daily["ic"].mean() / daily["ic"].std() if daily["ic"].std() > 0 else 0,
        "rank_mean": daily["rank_ic"].mean(), "rank_std": daily["rank_ic"].std(),
        "rank_ir": daily["rank_ic"].mean() / daily["rank_ic"].std() if daily["rank_ic"].std() > 0 else 0,
        "n_days": len(daily),
    }

ia = ind_allocation_ic(df)
rows.append({
    "method": "Industry Allocation (ind mean)",
    "period": "Overall",
    "ic_mean": ia["mean"], "icir": ia["ir"],
    "rank_ic_mean": ia["rank_mean"], "rank_icir": ia["rank_ir"],
})
for year in sorted(df["year"].unique()):
    yr = df[df["year"] == year]
    if len(yr) < 100:
        continue
    s_yr = ind_allocation_ic(yr)
    rows.append({
        "method": "Industry Allocation (ind mean)",
        "period": str(year),
        "ic_mean": s_yr["mean"], "icir": s_yr["ir"],
        "rank_ic_mean": s_yr["rank_mean"], "rank_icir": s_yr["rank_ir"],
    })

e1 = pd.DataFrame(rows)
print(f"\n{'Method':<35s} {'Period':>7s} {'IC':>8s} {'ICIR':>8s} {'RankIC':>8s} {'RankICIR':>8s}")
print("-" * 75)
for _, r in e1.iterrows():
    print(f"{r['method']:<35s} {r['period']:>7s} {r['ic_mean']:>8.4f} {r['icir']:>8.3f} {r['rank_ic_mean']:>8.4f} {r['rank_icir']:>8.3f}")

e1.to_csv(OUT / "ic_decomposition.csv", index=False)
print(f"\n  Saved: {OUT}/ic_decomposition.csv")


# ═══════════════════════════════════════════════════════════════════
# Experiment 2: TopK quality in 2024/2025
# ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("EXPERIMENT 2: TopK Quality (2024/2025)")
print("=" * 60)

def topk_stats(sub: pd.DataFrame, k: int, label_col: str = "label_value") -> dict:
    """Per-date TopK then aggregate — 不能整年/整月混排后 head(k).

    每个 prediction date 分别取 TopK，再聚合 label_value 统计。
    """
    dates = sorted(sub["trade_date"].unique())
    all_vals = []
    for dt in dates:
        day_slice = sub[sub["trade_date"] == dt].sort_values("score", ascending=False).head(k)
        all_vals.extend(day_slice[label_col].tolist())
    vals = pd.Series(all_vals).dropna()
    return {
        "mean_ret": vals.mean() if len(vals) > 0 else np.nan,
        "median_ret": vals.median() if len(vals) > 0 else np.nan,
        "hit_rate": (vals > 0).mean() if len(vals) > 0 else np.nan,
        "bad_rate": (vals < 0).mean() if len(vals) > 0 else np.nan,
        "good_rate_30": (vals > 0.3).mean() if len(vals) > 0 else np.nan,
        "big_win_rate_60": (vals > 0.6).mean() if len(vals) > 0 else np.nan,
        "worst_ret": vals.min() if len(vals) > 0 else np.nan,
        "best_ret": vals.max() if len(vals) > 0 else np.nan,
        "n": len(vals),
        "top_industry": sub["industry"].value_counts().index[0] if "industry" in sub.columns and sub["industry"].notna().any() else "",
    }

# Monthly TopK
print("\n--- Monthly TopK quality ---")
monthly_rows = []
for period_name in ["2024", "2025"]:
    sub = df[df["year"] == int(period_name)]
    for month in sorted(sub["month"].unique()):
        m = sub[sub["month"] == month]
        for k in [20, 50, 100]:
            s = topk_stats(m, k)
            monthly_rows.append({"period": month, "topk": k, **s})

mdf = pd.DataFrame(monthly_rows)
print(f"\n{'Month':>8s} {'K':>4s} {'mean_ret':>9s} {'hit_rate':>9s} {'bad_rate':>9s} {'good>30':>8s} {'big>60':>7s}")
print("-" * 60)
for _, r in mdf.iterrows():
    if r["topk"] == 20:
        print(f"{r['period']:>8s} {r['topk']:>4d} {r['mean_ret']:>9.4f} {r['hit_rate']:>9.2%} {r['bad_rate']:>9.2%} {r['good_rate_30']:>8.2%} {r['big_win_rate_60']:>7.2%}")

mdf.to_csv(OUT / "monthly_topk_quality.csv", index=False)

# Yearly TopK summary
print("\n--- Yearly TopK summary ---")
yearly_rows = []
for period_name in ["2021", "2022", "2023", "2024", "2025"]:
    sub = df[df["year"] == int(period_name)]
    for k in [20, 50, 100]:
        s = topk_stats(sub, k)
        yearly_rows.append({"year": period_name, "topk": k, **s})

ydf = pd.DataFrame(yearly_rows)
print(f"\n{'Year':>5s} {'K':>4s} {'mean_ret':>9s} {'hit_rate':>9s} {'bad_rate':>9s} {'good>30':>8s} {'big>60':>7s}")
print("-" * 60)
for _, r in ydf.iterrows():
    print(f"{r['year']:>5s} {r['topk']:>4d} {r['mean_ret']:>9.4f} {r['hit_rate']:>9.2%} {r['bad_rate']:>9.2%} {r['good_rate_30']:>8.2%} {r['big_win_rate_60']:>7.2%}")

# 2024/2025 Top50 industry distribution
print("\n--- Industry distribution: Top50 in 2024 vs 2025 ---")
for target_year in [2024, 2025]:
    sub = df[df["year"] == target_year]
    # Per-date Top50 then aggregate industry counts
    all_ind = []
    for dt in sorted(sub["trade_date"].unique()):
        day_slice = sub[sub["trade_date"] == dt].sort_values("score", ascending=False).head(50)
        all_ind.extend(day_slice["industry"].dropna().tolist())
    if all_ind:
        ind_freq = pd.Series(all_ind).value_counts()
        print(f"\n  {target_year} Top50 industries (per-date Top50 aggregated):")
        print(f"  {ind_freq.head(15).to_string()}")

# Save
ydf.to_csv(OUT / "yearly_topk_quality.csv", index=False)
print(f"\n  Saved: {OUT}/yearly_topk_quality.csv")

print("\n✅ Done — all diagnostics saved to", OUT)
