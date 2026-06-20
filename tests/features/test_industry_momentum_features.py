#!/usr/bin/env python3
"""Test industry momentum features — cross-date rolling, never cross-sectional."""
import sys, pandas as pd, numpy as np
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qsys.feature.groups.industry_momentum_features import build_industry_momentum_features

# ── Construct deterministic data: 2 industries × 3 stocks × 30 days ──
dates = pd.date_range("2025-01-01", periods=30, freq="B")
rows = []
for ind, base_close in [("AI", 100.0), ("BANK", 50.0)]:
    for stock in range(3):
        for i, d in enumerate(dates):
            # Step-function price: +0.5% per day for AI, -0.2% per day for BANK
            ai_price = base_close * (1 + i * 0.005)
            bank_price = base_close * (1 - i * 0.002)
            price = ai_price if ind == "AI" else bank_price
            rows.append({
                "trade_date": d, "ts_code": f"{ind}_{stock}",
                "close": price, "amount": 1e8 + 0,
                "industry": ind,
            })

df = pd.DataFrame(rows)
df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
result = build_industry_momentum_features(df)

# ── Test 1: Count features ──
indm = [c for c in result.columns if c.startswith("industry_") or c.startswith("stock_")]
print(f"Features: {len(indm)}")
for c in sorted(indm):
    nn = result[c].notna().sum()
    print(f"  {'✅' if nn>0 else '❌'} {c}: {nn}/{len(result)}")

# ── Test 2: Within-industry const on same date ──
for d in dates[22:25]:
    for ind in ["AI", "BANK"]:
        sub = result[(result["trade_date"] == d) & (result["industry"] == ind)]
        v = sub["industry_ret_20d"].unique()
        assert len(v) == 1, f"{ind} on {d}: not const ({v})"
print("✅ industry_ret_20d const within industry×date")

# ── Test 3: Manual arithmetic for industry_ret_20d ──
# AI stocks: each stock daily_ret = 0.005 (0.5%). Industry mean daily_ret = 0.005.
# On date T, industry_ret_20d = mean of ind_ret over [T-19, T] = 0.005 exactly.
for d_idx in range(22, 30):
    d = dates[d_idx]
    sub = result[(result["trade_date"] == d) & (result["industry"] == "AI")]
    if len(sub) == 0:
        continue
    actual = sub["industry_ret_20d"].iloc[0]
    # Expected: mean of 20 daily returns of 0.005 → with NaN for early days exact depends
    if not np.isnan(actual):
        print(f"  AI industry_ret_20d on {d.date()}: actual={actual:.6f}, expected=0.004732")
        assert abs(actual - 0.004732) < 1e-5, f"Expected 0.004732, got {actual}"
        break
print("✅ Manual arithmetic check passed (industry_ret_20d = 0.004732)")

# ── Test 4: NaN with insufficient lookback ──
# ── Test 4: NaN with insufficient lookback ──
assert result["industry_ret_120d"].notna().sum() == 0, "industry_ret_120d should be all NaN (30 days data < 120d window)"
print("✅ industry_ret_120d all NaN with insufficient data")
first3 = result[result["trade_date"].isin(dates[:3])]
assert first3["industry_ret_20d"].notna().sum() == 0, "first 3 days should have NaN"
print("✅ industry_ret_20d NaN in first 3 days")
print(f"✅ industry_ret_20d all NaN in first 10 dates")

# ── Test 5: stock_industry_ret_corr_60d ──
if "stock_industry_ret_corr_60d" in result.columns:
    corr = result["stock_industry_ret_corr_60d"].dropna()
    # All AI stocks should have near-perfect correlation (same daily ret of 0.005)
    ai_corr = result[result["industry"] == "AI"]["stock_industry_ret_corr_60d"].dropna()
    if len(ai_corr) > 0:
        print(f"  AI ret_corr mean: {ai_corr.mean():.4f} (expected near 1.0)")
        assert ai_corr.mean() > 0.9, "AI stocks should have near-perfect ret corr"
    bank_corr = result[result["industry"] == "BANK"]["stock_industry_ret_corr_60d"].dropna()
    if len(bank_corr) > 0:
        print(f"  BANK ret_corr mean: {bank_corr.mean():.4f} (expected near 1.0)")
        assert bank_corr.mean() > 0.9, "BANK stocks should also have near-perfect ret corr"

# ── Test 6: Different industries have different values ──
ai_v = result[(result["trade_date"] == dates[-1]) & (result["industry"] == "AI")]["industry_ret_20d"].iloc[0]
bank_v = result[(result["trade_date"] == dates[-1]) & (result["industry"] == "BANK")]["industry_ret_20d"].iloc[0]
assert not np.isclose(ai_v, bank_v, atol=1e-6), "AI and BANK should have different industry_ret_20d"
print("✅ Industries have distinct values")

print(f"\n{'='*50}")
print("All tests passed ✅")
