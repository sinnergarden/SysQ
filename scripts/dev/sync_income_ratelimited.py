#!/usr/bin/env python3
"""Sync Tushare income data — rate-limited to avoid API ban."""
import sys, time, pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import tushare as ts

OUT = Path("data/tushare/income.parquet")
fc = pd.read_parquet("data/tushare/forecast.parquet")
targets = sorted(fc["ts_code"].unique())
N = len(targets)
print(f"Income: {N} stocks @ 0.38s", flush=True)

all_chunks = []
t0 = time.time()
for i, code in enumerate(targets):
    try:
        df = ts.pro_api().income(ts_code=code, start_date="20180101", end_date="20251231")
        if df is not None and len(df) > 0:
            all_chunks.append(df[df["report_type"] == 1])
    except Exception as e:
        if i < 5: print(f"  FAIL[{i}] {code}: {e}", flush=True)
    if (i+1) % 100 == 0:
        n = sum(len(c) for c in all_chunks) if all_chunks else 0
        print(f"  {i+1}/{N} ({time.time()-t0:.0f}s) rows={n}", flush=True)
    time.sleep(0.38)

if all_chunks:
    result = pd.concat(all_chunks, ignore_index=True)
    result = result.drop_duplicates(subset=["ts_code","ann_date","end_date"])
    result = result.sort_values(["ts_code","ann_date"]).reset_index(drop=True)
    result.to_parquet(OUT, index=False)
    print(f"\n✅ income: {len(result)} rows, {result['ts_code'].nunique()} stocks")
    print(f"   revenue notna: {result['revenue'].notna().sum()}/{len(result)}", flush=True)
else:
    print("❌ No data", flush=True)
print(f"Time: {time.time()-t0:.0f}s", flush=True)
