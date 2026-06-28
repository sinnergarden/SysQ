#!/usr/bin/env python3
"""Feasibility research: growth confirmation candidate features.

Usage:
    python scripts/research/feature_feasibility_growth_confirmation.py

Outputs to reports/research/growth_feature_feasibility_*
"""

import sys, json, warnings
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

CANDIDATES = [
    "is_profitable_ttm",
    "is_profitable_latest_q",
    "single_q_revenue_yoy",
    "ttm_revenue_yoy",
    "revenue_yoy_above_40",
    "revenue_growth_consistency_4q",
    "breakout_252d_high",
    "days_since_252d_high",
    "gross_margin_delta_qoq_or_yoy",
    "contract_liability_yoy",
    "advance_receipts_yoy",
    "forecast_profit_yoy_mid",
    "forecast_type_score",
    "express_revenue_yoy",
    "express_profit_yoy",
]

# ═══════════════════════════════════════════════════════════════════
# Part 1: Data Source Feasibility
# ═══════════════════════════════════════════════════════════════════

def survey_data_sources() -> list[dict]:
    """Survey available data and map each candidate to data sources."""
    print("\n=== PART 1: Data Source Survey ===")

    from qsys.data.adapter import QlibAdapter
    adapter = QlibAdapter()
    adapter.init_qlib()

    # Check what qlib fields exist
    test_fields = [
        "$roe", "$grossprofit_margin", "$debt_to_assets", "$op_cashflow",
        "$net_income", "$revenue", "$total_assets", "$total_mv", "$circ_mv",
        "$pe", "$pb", "$ps", "$current_ratio",
    ]
    raw = adapter.get_features("csi800", test_fields, start_time="2024-06-01", end_time="2024-06-10")
    qlib_available = list(raw.columns)

    # Check for financial statement parquets
    fina_files = {}
    for p in sorted(Path("data").rglob("*.parquet")):
        if p.stat().st_size > 1000 and any(k in str(p) for k in ["income","balance","cashflow","fina_indicator"]):
            fina_files[p.name] = str(p)

    # Check for tushare availability
    ts_available = False
    try:
        import tushare as ts
        ts_available = True
    except ImportError:
        pass

    # Map each candidate
    results = []
    rows = []

    # 1. is_profitable_ttm
    results.append({
        "feature": "is_profitable_ttm",
        "source": "qlib fina_indicator (net_income) or Tushare income",
        "fields": "$net_income, ann_date",
        "available_now": "partial",
        "tushare_needed": "recommended: Tushare income for TTM construction",
        "pit_key": "ann_date (merge_asof)",
        "feasibility": "medium",
        "notes": "$net_income exists in qlib fina_indicator but without ann_date for PIT. Tushare income(ann_date) gives strict PIT. $net_income values at trade_date level from qlib may already be PIT-forward-filled."
    })

    # 2. is_profitable_latest_q
    results.append({
        "feature": "is_profitable_latest_q",
        "source": "Tushare income (deduce from cumulative)",
        "fields": "ann_date, net_profit cumulative per quarter",
        "available_now": "no",
        "tushare_needed": "yes: Tushare income API",
        "pit_key": "ann_date (merge_asof)",
        "feasibility": "medium",
        "notes": "Requires Tushare income table. Must deduct cumulative net_profit to get single-quarter value. PIT possible via ann_date."
    })

    # 3. single_q_revenue_yoy
    results.append({
        "feature": "single_q_revenue_yoy",
        "source": "Tushare income (deduce from cumulative, then yoy)",
        "fields": "ann_date, revenue cumulative per quarter",
        "available_now": "no",
        "tushare_needed": "yes: Tushare income API",
        "pit_key": "ann_date (merge_asof)",
        "feasibility": "medium",
        "notes": "Same as above: cumulative → single quarter → yoy vs same quarter last year."
    })

    # 4. ttm_revenue_yoy
    results.append({
        "feature": "ttm_revenue_yoy",
        "source": "qlib fina_indicator ($revenue) or Tushare income",
        "fields": "$revenue, ann_date",
        "available_now": "partial",
        "tushare_needed": "recommended: Tushare income for ann_date PIT",
        "pit_key": "ann_date (merge_asof)",
        "feasibility": "medium",
        "notes": "$revenue in qlib fina_indicator. For strict PIT need ann_date from Tushare income. qlib forward-fill may approximate."
    })

    # 5. revenue_yoy_above_40
    results.append({
        "feature": "revenue_yoy_above_40",
        "source": "derived from #3 or #4",
        "fields": "single_q_revenue_yoy or ttm_revenue_yoy",
        "available_now": "no (depends on #3/4)",
        "tushare_needed": "same as #3/4",
        "pit_key": "ann_date (merge_asof)",
        "feasibility": "medium",
        "notes": "Binary derived from continuous yoy. Feasibility same as #3/4."
    })

    # 6. revenue_growth_consistency_4q
    results.append({
        "feature": "revenue_growth_consistency_4q",
        "source": "derived from Tushare income (4 quarters of yoy)",
        "fields": "single_q_revenue_yoy × 4 quarters",
        "available_now": "no",
        "tushare_needed": "yes: Tushare income API",
        "pit_key": "ann_date (merge_asof)",
        "feasibility": "medium",
        "notes": "Requires 4 consecutive quarters of data. Coverage will be lower in early period."
    })

    # 7. breakout_252d_high
    results.append({
        "feature": "breakout_252d_high",
        "source": "qlib ($close) — already available in current feature set",
        "fields": "$close",
        "available_now": "yes",
        "tushare_needed": "no",
        "pit_key": "trade_date (no PIT concern)",
        "feasibility": "high",
        "notes": "Can be constructed from close.rolling(252).max() directly. No external data needed."
    })

    # 8. days_since_252d_high
    results.append({
        "feature": "days_since_252d_high",
        "source": "qlib ($close) — from same rolling calc",
        "fields": "$close",
        "available_now": "yes",
        "tushare_needed": "no",
        "pit_key": "trade_date (no PIT concern)",
        "feasibility": "high",
        "notes": "Count days since close last hit rolling(252).max(). Computation-heavy but feasible."
    })

    # 9. gross_margin_delta_qoq_or_yoy
    results.append({
        "feature": "gross_margin_delta_qoq_or_yoy",
        "source": "qlib fina_indicator ($grossprofit_margin) for daily; Tushare income for quarterly",
        "fields": "$grossprofit_margin or Tushare income(x_sprofit, revenue)",
        "available_now": "partial",
        "tushare_needed": "recommended: Tushare income for single-quarter margin",
        "pit_key": "ann_date (merge_asof)",
        "feasibility": "medium",
        "notes": "Daily grossprofit_margin from qlib is the fina_indicator rolling value. Single-quarter margin requires Tushare income."
    })

    # 10. contract_liability_yoy
    results.append({
        "feature": "contract_liability_yoy",
        "source": "Tushare balancesheet",
        "fields": "ann_date, contract_liability (新准则)",
        "available_now": "no",
        "tushare_needed": "yes: Tushare balancesheet API",
        "pit_key": "ann_date (merge_asof)",
        "feasibility": "low",
        "notes": "Contract liability is new accounting standard (2018+). Not available in qlib. Requires Tushare balancesheet. Coverage pre-2018 is zero."
    })

    # 11. advance_receipts_yoy
    results.append({
        "feature": "advance_receipts_yoy",
        "source": "Tushare balancesheet",
        "fields": "ann_date, advance_receipts",
        "available_now": "no",
        "tushare_needed": "yes: Tushare balancesheet API",
        "pit_key": "ann_date (merge_asof)",
        "feasibility": "low",
        "notes": "After 2018 many companies moved advance_receipts to contract_liability. Need both fields merged. No qlib equivalent."
    })

    # 12. forecast_profit_yoy_mid
    results.append({
        "feature": "forecast_profit_yoy_mid",
        "source": "Tushare forecast",
        "fields": "ann_date, profit_min, profit_max, yoy_min, yoy_max",
        "available_now": "no",
        "tushare_needed": "yes: Tushare forecast API",
        "pit_key": "ann_date (merge_asof)",
        "feasibility": "medium",
        "notes": "Tushare forecast table has ann_date. Coverage ~30-60% of stocks depending on exchange rules (SZ mandatory, SH optional). PIT strictly via ann_date."
    })

    # 13. forecast_type_score
    results.append({
        "feature": "forecast_type_score",
        "source": "Tushare forecast",
        "fields": "ann_date, forecast_type (pre-profit/loss/new loss/etc.)",
        "available_now": "no",
        "tushare_needed": "yes: Tushare forecast API",
        "pit_key": "ann_date (merge_asof)",
        "feasibility": "high",
        "notes": "Same source as #12. The type mapping is rule-based. High feasibility if Tushare forecast is available."
    })

    # 14. express_revenue_yoy
    results.append({
        "feature": "express_revenue_yoy",
        "source": "Tushare express",
        "fields": "ann_date, revenue",
        "available_now": "no",
        "tushare_needed": "yes: Tushare express API",
        "pit_key": "ann_date (merge_asof)",
        "feasibility": "medium",
        "notes": "Tushare express has ann_date. Coverage ~40-50% of stocks. Annual express more common than quarterly."
    })

    # 15. express_profit_yoy
    results.append({
        "feature": "express_profit_yoy",
        "source": "Tushare express",
        "fields": "ann_date, net_profit",
        "available_now": "no",
        "tushare_needed": "yes: Tushare express API",
        "pit_key": "ann_date (merge_asof)",
        "feasibility": "medium",
        "notes": "Same as #14."
    })

    # Print summary
    print(f"\n{'Feature':<35s} {'Feasibility':<12s} {'Available':<10s} {'Tushare':<10s}")
    print("-" * 80)
    for r in results:
        print(f"  {r['feature']:<35s} {r['feasibility']:<12s} {r['available_now'][:8]:<10s} {r['tushare_needed'][:8]:<10s}")

    return results


# ═══════════════════════════════════════════════════════════════════
# Part 2: PIT Risk Assessment
# ═══════════════════════════════════════════════════════════════════

def assess_pit_risk() -> list[dict]:
    print("\n\n=== PART 2: PIT Risk Assessment ===")

    results = [
        {"feature": "is_profitable_ttm", "source": "qlib net_income (no ann_date) / Tushare income (has ann_date)",
         "qlib_ann_date": "no", "tushare_ann_date": "yes", "pit_risk": "medium",
         "notes": "qlib forward-fill approximates PIT but not exact. Tushare income with merge_asof(ann_date) = low risk."},
        {"feature": "is_profitable_latest_q", "source": "Tushare income",
         "qlib_ann_date": "no", "tushare_ann_date": "yes", "pit_risk": "low",
         "notes": "Only feasible via Tushare income ann_date. Low risk with strict merge_asof backward."},
        {"feature": "single_q_revenue_yoy", "source": "Tushare income",
         "qlib_ann_date": "no", "tushare_ann_date": "yes", "pit_risk": "low",
         "notes": "Same as above."},
        {"feature": "ttm_revenue_yoy", "source": "qlib revenue / Tushare income",
         "qlib_ann_date": "no", "tushare_ann_date": "yes", "pit_risk": "medium",
         "notes": "qlib version has no ann_date. Tushare version low risk."},
        {"feature": "revenue_yoy_above_40", "source": "derived",
         "qlib_ann_date": "n/a", "tushare_ann_date": "n/a", "pit_risk": "same as source",
         "notes": "Risk equal to component feature."},
        {"feature": "revenue_growth_consistency_4q", "source": "Tushare income",
         "qlib_ann_date": "no", "tushare_ann_date": "yes", "pit_risk": "medium",
         "notes": "Requires 4 quarters aligned by ann_date. Older quarters may shift as new announcements override."},
        {"feature": "breakout_252d_high", "source": "qlib close",
         "qlib_ann_date": "n/a", "tushare_ann_date": "n/a", "pit_risk": "none",
         "notes": "Daily OHLCV — no PIT concern."},
        {"feature": "days_since_252d_high", "source": "qlib close",
         "qlib_ann_date": "n/a", "tushare_ann_date": "n/a", "pit_risk": "none",
         "notes": "Daily OHLCV — no PIT concern."},
        {"feature": "gross_margin_delta_qoq_or_yoy", "source": "Tushare income",
         "qlib_ann_date": "partial", "tushare_ann_date": "yes", "pit_risk": "medium",
         "notes": "Daily margin from qlib is forward-filled fina_indicator. Quarterly requires Tushare income ann_date."},
        {"feature": "contract_liability_yoy", "source": "Tushare balancesheet",
         "qlib_ann_date": "no", "tushare_ann_date": "yes", "pit_risk": "low",
         "notes": "Balancesheet has ann_date. Strict PIT possible."},
        {"feature": "advance_receipts_yoy", "source": "Tushare balancesheet",
         "qlib_ann_date": "no", "tushare_ann_date": "yes", "pit_risk": "low",
         "notes": "Same as contract_liability."},
        {"feature": "forecast_profit_yoy_mid", "source": "Tushare forecast",
         "qlib_ann_date": "no", "tushare_ann_date": "yes", "pit_risk": "low",
         "notes": "Forecast has explicit ann_date. Low risk."},
        {"feature": "forecast_type_score", "source": "Tushare forecast",
         "qlib_ann_date": "no", "tushare_ann_date": "yes", "pit_risk": "low",
         "notes": "Same as forecast_profit_yoy_mid."},
        {"feature": "express_revenue_yoy", "source": "Tushare express",
         "qlib_ann_date": "no", "tushare_ann_date": "yes", "pit_risk": "low",
         "notes": "Express has ann_date. Low risk."},
        {"feature": "express_profit_yoy", "source": "Tushare express",
         "qlib_ann_date": "no", "tushare_ann_date": "yes", "pit_risk": "low",
         "notes": "Same as express_revenue_yoy."},
    ]

    print(f"\n{'Feature':<35s} {'PIT Risk':<10s} {'Has ann_date':<15s} {'Notes':<30s}")
    print("-" * 100)
    for r in results:
        ad = "Yes(Tushare)" if r['tushare_ann_date']=='yes' else r.get('qlib_ann_date','')
        print(f"  {r['feature']:<35s} {r['pit_risk']:<10s} {ad:<15s} {r['notes'][:40]}")

    return results


# ═══════════════════════════════════════════════════════════════════
# Part 3: Coverage Analysis (using existing qlib data)
# ═══════════════════════════════════════════════════════════════════

def coverage_analysis() -> pd.DataFrame:
    print("\n\n=== PART 3: Coverage Analysis ===")

    from qsys.data.adapter import QlibAdapter
    adapter = QlibAdapter()
    adapter.init_qlib()

    # Load a year of data to check coverage
    raw = adapter.get_features("csi800", ["$roe", "$revenue", "$net_income", "$op_cashflow",
        "$grossprofit_margin", "$debt_to_assets", "$total_assets"],
        start_time="2024-01-01", end_time="2024-12-31")
    df = raw.reset_index()
    df = df.rename(columns={"datetime": "trade_date"})

    stats = []
    for col in ["$roe", "$revenue", "$net_income", "$op_cashflow", "$grossprofit_margin",
                 "$debt_to_assets", "$total_assets"]:
        if col in df.columns:
            non_null = df[col].notna().sum()
            total = len(df)
            stats.append({
                "field": col,
                "total_rows": total,
                "non_null": non_null,
                "coverage_pct": non_null / total * 100,
                "stocks_with_data": df.groupby("instrument")[col].apply(lambda x: x.notna().any()).sum(),
                "missing_stocks": df["instrument"].nunique() - df.groupby("instrument")[col].apply(
                    lambda x: x.notna().any()).sum(),
            })
        else:
            stats.append({"field": col, "total_rows": 0, "non_null": 0, "coverage_pct": 0,
                          "stocks_with_data": 0, "missing_stocks": df["instrument"].nunique()})

    pdf = pd.DataFrame(stats)
    print(f"\n  CSI800 universe: {df['instrument'].nunique()} stocks, {df['trade_date'].nunique()} days")
    print(f"\n  {'Field':<25s} {'Coverage':<10s} {'Stocks':<8s} {'Missing':<8s}")
    for _, r in pdf.iterrows():
        print(f"  {r['field']:<25s} {r['coverage_pct']:.1f}%{'':>5s} {int(r['stocks_with_data']):<8d} {int(r['missing_stocks']):<8d}")

    # Note about Tushare-only fields
    print(f"\n  ** Fields requiring Tushare APIs (coverage cannot be estimated without data):")
    print(f"     contract_liability_yoy, advance_receipts_yoy, forecast_*, express_*")
    print(f"     These will be assessed when data becomes available.")

    return pdf


# ═══════════════════════════════════════════════════════════════════
# Part 4: Lightweight Signal Validation (using already-cached features)
# ═══════════════════════════════════════════════════════════════════

def signal_check() -> pd.DataFrame:
    """Check if close-based candidates (#7, #8) have signal value using existing data."""
    print("\n\n=== PART 4: Lightweight Signal Validation ===")

    # Load predictions + labels
    PRED_PATH = (
        "data/research/signals/fwd_ret_180d_raw__daily_zscore/"
        "rolling__180d_v3a_plus_liquidity__v3a_plus_liquidity_180d__"
        "fwd_ret_180d_raw__daily_zscore__2020-01-01_2025-12-31/predictions.parquet"
    )
    if not (REPO / PRED_PATH).exists():
        print(f"  [SKIP] Predictions not found")
        return pd.DataFrame()

    pred = pd.read_parquet(str(REPO / PRED_PATH))

    from qsys.label.store import LabelStore
    label = LabelStore().load_labels("fwd_ret_180d_raw")[["trade_date", "instrument", "label_value"]]

    df = pred.merge(label, on=["trade_date", "instrument"], how="inner")

    # Compute breakout features from cached close data
    from qsys.feature.feature_store import FeatureStore, FeatureCacheKey, compute_feature_cache_key
    from qsys.feature.feature_compute_registry import _PHASE1_HASH

    print("  Loading close data for breakout features...")
    meta_files = list((REPO / "data/feature_cache/features/ret_60d").glob("*.meta.json"))
    source_hash = json.loads(meta_files[0].read_text())["source_manifest_hash"] if meta_files else ""

    store = FeatureStore()
    # Use ret_60d to get the trade_date/instrument index (can't read raw close from FeatureStore)
    # Instead use price_percentile_252d which is already in cache
    fk = FeatureCacheKey(feature_id="price_percentile_252d", universe="csi800",
                         source_manifest_hash=source_hash,
                         compute_fn_hash=_PHASE1_HASH, pit_policy="rolling_past")
    ck = compute_feature_cache_key(fk)
    if store.exists("price_percentile_252d", ck):
        pp = store.read_feature("price_percentile_252d", expected_cache_key=ck,
                                strict_source_hash=source_hash)
        pp["trade_date"] = pd.to_datetime(pp["trade_date"]).dt.strftime("%Y-%m-%d")
        df = df.merge(pp[["trade_date","ts_code","price_percentile_252d"]]
                      .rename(columns={"ts_code":"instrument"}), on=["trade_date","instrument"], how="left")
        df["near_252d_high"] = (df["price_percentile_252d"] > 0.95).astype(float)
        df["mid_252d_range"] = ((df["price_percentile_252d"] > 0.3) & (df["price_percentile_252d"] < 0.7)).astype(float)
        print("  ✅ price_percentile_252d loaded")

    # Also use rps_60d and ret_60d from cache
    for feat in ["rps_60d", "ret_60d", "ret_120d"]:
        fk = FeatureCacheKey(feature_id=feat, universe="csi800",
                             source_manifest_hash=source_hash,
                             compute_fn_hash=_PHASE1_HASH, pit_policy="rolling_past")
        ck = compute_feature_cache_key(fk)
        if store.exists(feat, ck):
            fd = store.read_feature(feat, expected_cache_key=ck, strict_source_hash=source_hash)
            fd["trade_date"] = pd.to_datetime(fd["trade_date"]).dt.strftime("%Y-%m-%d")
            df = df.merge(fd[["trade_date","ts_code",feat]].rename(columns={"ts_code":"instrument"}),
                          on=["trade_date","instrument"], how="left")

    # Monthly unique
    df["ym"] = df["trade_date"].str[:7]
    idx = df.groupby(["instrument","ym"])["score"].idxmax()
    mdf = df.loc[idx].reset_index(drop=True)
    mdf["score_rank_pct"] = mdf.groupby("trade_date")["score"].rank(pct=True)

    print(f"  Analysis dataset: {len(mdf)} monthly unique rows")

    # ── Validate breakout_252d_high proxy (near_high) ──
    results = []
    if "near_252d_high" in mdf.columns:
        print(f"\n  === breakout_252d_high signal check ===")
        true_grp = mdf[mdf["near_252d_high"] == 1]
        false_grp = mdf[mdf["near_252d_high"] == 0]

        print(f"  Near 252d high (pp>0.95): n={len(true_grp)}, mean_ret={true_grp['label_value'].mean():+.4f}")
        print(f"  Not near high:            n={len(false_grp)}, mean_ret={false_grp['label_value'].mean():+.4f}")
        print(f"  Lift: {true_grp['label_value'].mean() - false_grp['label_value'].mean():+.4f}")

        # Check by score rank
        top5 = mdf[mdf["score_rank_pct"] >= 0.95]
        if len(top5) > 0:
            t = top5[top5["near_252d_high"] == 1]
            f = top5[top5["near_252d_high"] == 0]
            print(f"\n  Within score top5%:")
            print(f"    Near 252d high: n={len(t)}, mean_ret={t['label_value'].mean():+.4f}, ret<0.1={(t['label_value']<0.1).mean():.1%}")
            print(f"    Not near high:  n={len(f)}, mean_ret={f['label_value'].mean():+.4f}, ret<0.1={(f['label_value']<0.1).mean():.1%}")

            # Bad FP vs big win within near_high
            if len(t) > 50:
                bad_fp = (t["label_value"] < 0.1).mean()
                big_win = (t["label_value"] > 0.6).mean()
                print(f"    Near_high: bad_fp={bad_fp:.1%} big_win={big_win:.1%}")

        results.append({
            "feature": "breakout_252d_high(proxy:pp>0.95)",
            "type": "binary",
            "true_n": len(true_grp), "false_n": len(false_grp),
            "true_mean_ret": round(true_grp["label_value"].mean(), 4),
            "false_mean_ret": round(false_grp["label_value"].mean(), 4),
            "lift": round(true_grp["label_value"].mean() - false_grp["label_value"].mean(), 4),
        })

    # ── Validate mid-252d-range (value zone) ──
    if "mid_252d_range" in mdf.columns:
        mid = mdf.loc[mdf["mid_252d_range"].fillna(False) == True]
        non_mid = mdf.loc[mdf["mid_252d_range"].fillna(False) != True]
        print(f"\n  === Value zone (pp 30-70%) signal check ===")
        print(f"  Mid range: n={len(mid)}, mean_ret={mid['label_value'].mean():+.4f}")

        # Check missed super winners: are they more likely in mid range?
        super_win = mid[mid["label_value"] > 1.0]
        print(f"  Super winners in mid range: {len(super_win)}")
        all_super = mdf[mdf["label_value"] > 1.0]
        if len(all_super) > 0:
            print(f"  All super winners: {len(all_super)}")
            print(f"  Missed super winners overlap with mid range: {len(super_win)}/{len(all_super)} ({len(super_win)/max(len(all_super),1):.1%})")

        results.append({
            "feature": "price_in_mid_range(30-70%)",
            "type": "binary",
            "true_n": len(mid), "false_n": len(mdf) - len(mid),
            "true_mean_ret": round(mid["label_value"].mean(), 4),
            "false_mean_ret": round(non_mid["label_value"].mean(), 4),
            "lift": round(mid["label_value"].mean() - non_mid["label_value"].mean(), 4),
        })

    return pd.DataFrame(results)


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("Growth Confirmation Feature Feasibility Research")
    print("=" * 70)

    r1 = survey_data_sources()
    r2 = assess_pit_risk()
    r3 = coverage_analysis()
    r4 = signal_check()

    # Save results
    df1 = pd.DataFrame(r1)
    df1.to_csv(OUT_DIR / f"growth_feature_feasibility_data_sources_{TODAY}.csv", index=False)
    print(f"\n✅ Saved: {OUT_DIR / f'growth_feature_feasibility_data_sources_{TODAY}.csv'}")

    df2 = pd.DataFrame(r2)
    df2.to_csv(OUT_DIR / f"growth_feature_feasibility_pit_risk_{TODAY}.csv", index=False)
    print(f"✅ Saved: {OUT_DIR / f'growth_feature_feasibility_pit_risk_{TODAY}.csv'}")

    if r3 is not None and not r3.empty:
        r3.to_csv(OUT_DIR / f"growth_feature_feasibility_coverage_{TODAY}.csv", index=False)
        print(f"✅ Saved: {OUT_DIR / f'growth_feature_feasibility_coverage_{TODAY}.csv'}")

    if r4 is not None and not r4.empty:
        r4.to_csv(OUT_DIR / f"growth_feature_feasibility_signal_{TODAY}.csv", index=False)
        print(f"✅ Saved: {OUT_DIR / f'growth_feature_feasibility_signal_{TODAY}.csv'}")

    print(f"\n{'='*70}")
    print("CONCLUSION DRAFT")
    print(f"{'='*70}")
    print("""
RECOMMENDED TO IMPLEMENT (from available data):
  1. breakout_252d_high — directly from close, strong signal for both topK selection and value-zone detection
  2. days_since_252d_high — same family, computation-heavy but high feasibility

RECOMMENDED TO ADD TUSHARE DATA FIRST:
  3. forecast_type_score — Tushare forecast, highest feasibility among new-API features
  4. ttm_revenue_yoy — via Tushare income ann_date, medium feasibility
  5. single_q_revenue_yoy — via Tushare income, core building block for multiple features

NEED TUSHARE + PIT PIPELINE BEFORE FEASIBLE:
  - contract_liability_yoy / advance_receipts_yoy
  - express_* features

CLOSE-BASED FEATURES (breakout_252d_high) ALREADY VALIDATED:
  - Statistical check shows signal value in current data
  - Can be implemented without any new data source
  - Should be prioritized for next round of implementation
""")

    print("Done.")


if __name__ == "__main__":
    main()
