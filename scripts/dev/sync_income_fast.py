#!/usr/bin/env python3
"""Fast income sync — batch by period to minimize API calls."""
import sys, time, pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import tushare as ts
pro = ts.pro_api()

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "data" / "tushare" / "income.parquet"

# Periods to fetch (4 quarters per year, 2018-2025)
periods = []
for y in range(2018, 2026):
    for q in [1, 2, 3, 4]:
        periods.append(f"{y}q{q}")

print(f"Fetching {len(periods)} periods across 800 stocks...")

all_chunks = []
t0 = time.time()

for p_idx, period in enumerate(periods):
    df = pro.income(period=period)
    if df is not None and len(df) > 0:
        # Filter to CSI800 universe
        all_chunks.append(df)
    if (p_idx + 1) % 4 == 0:
        print(f"  Period {period} ({p_idx+1}/{len(periods)}, {time.time()-t0:.0f}s)")
    time.sleep(0.5)

if all_chunks:
    result = pd.concat(all_chunks, ignore_index=True)
    result = result.drop_duplicates(subset=["ts_code", "ann_date", "end_date", "report_type"])
    result = result[result["report_type"] == 1]
    result = result.sort_values(["ts_code", "ann_date"]).reset_index(drop=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(OUT, index=False)
    print(f"\n✅ income: {len(result)} rows, {result['ts_code'].nunique()} stocks")
    print(f"   ann_date: {result['ann_date'].min()}~{result['ann_date'].max()}")
    print(f"   revenue notna: {result['revenue'].notna().sum()}/{len(result)}")
else:
    print("❌ No data")
print(f"Total time: {time.time()-t0:.0f}s")
