#!/usr/bin/env python3
"""Income sync with checkpoint: saves every 100 stocks, resumes if interrupted."""
import sys, time, json, pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import tushare as ts

OUT = Path("data/tushare/income.parquet")
CKPT = Path("data/tushare/income_ckpt.json")

fc = pd.read_parquet("data/tushare/forecast.parquet")
all_codes = sorted(fc["ts_code"].unique())

# Load checkpoint
done = set()
if CKPT.exists():
    done = set(json.loads(CKPT.read_text()))

remaining = [c for c in all_codes if c not in done]
print(f"Total: {len(all_codes)}, Done: {len(done)}, Remaining: {len(remaining)}", flush=True)

if not remaining:
    print("All done!", flush=True)
    sys.exit(0)

pro = ts.pro_api()
chunks = []
t0 = time.time()

for i, code in enumerate(remaining):
    try:
        df = pro.income(ts_code=code)
        if df is not None and len(df) > 0:
            chunks.append(df)
    except:
        pass
    done.add(code)
    if (i+1) % 100 == 0:
        n = sum(len(c) for c in chunks) if chunks else 0
        elapsed = time.time() - t0
        # Save checkpoint + partial data
        json.dump(sorted(done), CKPT.open("w"))
        if chunks:
            interim = pd.concat(chunks, ignore_index=True)
            interim.drop_duplicates(subset=["ts_code","ann_date","end_date"]).to_parquet(OUT, index=False)
            n_stocks = interim["ts_code"].nunique()
            print(f"  {i+1}/{len(remaining)} ({elapsed:.0f}s) rows={n} stocks={n_stocks} ✅ checkpoint", flush=True)
            chunks = []
        else:
            print(f"  {i+1}/{len(remaining)} ({elapsed:.0f}s) empty batch", flush=True)
    time.sleep(1.2)

# Final save
if chunks:
    final = pd.concat(chunks, ignore_index=True)
    final.drop_duplicates(subset=["ts_code","ann_date","end_date"]).to_parquet(OUT, index=False)
    json.dump(sorted(done), CKPT.open("w"))
    final_df = pd.read_parquet(OUT)
    print(f"\n✅ income: {len(final_df)} rows, {final_df['ts_code'].nunique()} stocks", flush=True)
    print(f"   revenue notna: {final_df['revenue'].notna().sum()}/{len(final_df)}", flush=True)
else:
    print("No new data", flush=True)
print(f"Time: {time.time()-t0:.0f}s", flush=True)
