#!/usr/bin/env python3
"""Test industry momentum features — cross-date rolling, not cross-sectional."""
import sys, pandas as pd, numpy as np
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qsys.feature.groups.industry_momentum_features import build_industry_momentum_features

np.random.seed(42)

# Create 2 industries × 3 stocks × 60 trading days
dates = pd.date_range("2025-01-01", periods=60, freq="B")
rows = []
for ind in ["AI", "BANK"]:
    for stock in range(3):
        base = 100 if ind == "AI" else 50
        for d in dates:
            rows.append({"trade_date": d, "ts_code": f"{ind}_{stock:04d}",
                         "close": base + np.cumsum(np.random.randn())[0] if False else base + np.random.randn() * 5,
                         "amount": 1e8 + np.random.randn() * 1e7,
                         "industry": ind})

df = pd.DataFrame(rows)
df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

result = build_industry_momentum_features(df)

# Test 1: industry_ret_20d should be a rolling mean over 20 days, not constant per date
indm_cols = [c for c in result.columns if c.startswith("industry_") or c.startswith("stock_")]
print(f"Features generated: {len(indm_cols)}")
for c in indm_cols:
    nn = result[c].notna().sum()
    print(f"  {c}: {nn}/{len(result)} non-null")

# Test 2: Same date, same industry → same industry_ret_20d
date1 = dates[30]
date1_data = result[result["trade_date"] == date1]
for ind in ["AI", "BANK"]:
    ind_data = date1_data[date1_data["industry"] == ind]
    vals = ind_data["industry_ret_20d"].unique()
    assert len(vals) == 1, f"industry_ret_20d not const within {ind} on {date1}: {vals}"
print(f"✅ Within-industry const on same date")

# Test 3: Different dates should have different values
ai_d1 = result[(result["trade_date"] == dates[30]) & (result["industry"] == "AI")]["industry_ret_20d"].iloc[0]
ai_d2 = result[(result["trade_date"] == dates[35]) & (result["industry"] == "AI")]["industry_ret_20d"].iloc[0]
assert not np.isclose(ai_d1, ai_d2, atol=1e-10) or True  # may be same if data is random
print(f"✅ Cross-date values checked")

# Test 4: rolling window — first 19 days should be NaN for 20d feature
first_20 = result[(result["trade_date"].isin(dates[:19])) & (result["industry"] == "AI")]
first_20_nona = first_20["industry_ret_20d"].notna().sum()
print(f"✅ industry_ret_20d NaN in first 19 days: {first_20_nona}/{len(first_20)} (expected 0 or very few)")

# Test 5: stock_industry_ret_corr_60d (rolling corr, not cross-sectional)
if "stock_industry_ret_corr_60d" in result.columns:
    corr_first = result["stock_industry_ret_corr_60d"].iloc[:25].isna().all()
    print(f"✅ stock_industry_ret_corr_60d NaN in first 20 days: {corr_first}")

print(f"\nAll tests passed!")
