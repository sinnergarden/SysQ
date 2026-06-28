#!/usr/bin/env python3
"""Parallel income sync using concurrent workers."""
import sys, time, concurrent.futures, pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import tushare as ts

OUT_DIR = Path("data/tushare")
OUT_DIR.mkdir(parents=True, exist_ok=True)

fc = pd.read_parquet(OUT_DIR / "forecast.parquet")
targets = sorted(fc["ts_code"].unique())
print(f"Income sync: {len(targets)} stocks")

results = []
t0 = time.time()

def fetch(code):
    try:
        pro = ts.pro_api()
        df = pro.income(ts_code=code, start_date="20100101", end_date="20251231")
        if df is not None and len(df) > 0:
            return df[df["report_type"] == 1]
    except:
        pass
    return None

with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
    futs = {ex.submit(fetch, code): code for code in targets}
    done = 0
    for f in concurrent.futures.as_completed(futs):
        r = f.result()
        if r is not None and len(r) > 0:
            results.append(r)
        done += 1
        if done % 200 == 0:
            print(f"  {done}/{len(targets)} ({time.time()-t0:.0f}s)", flush=True)

if results:
    result = pd.concat(results, ignore_index=True)
    result = result.drop_duplicates(subset=["ts_code", "ann_date", "end_date"])
    result = result.sort_values(["ts_code", "ann_date"]).reset_index(drop=True)
    result.to_parquet(OUT_DIR / "income.parquet", index=False)
    print(f"\n✅ income: {len(result)} rows, {result['ts_code'].nunique()} stocks")
    print(f"   revenue notna: {result['revenue'].notna().sum()}/{len(result)}")
    print(f"   oper_cost notna: {result['oper_cost'].notna().sum()}/{len(result)}")
else:
    print("❌ No data")
print(f"Time: {time.time()-t0:.0f}s")
