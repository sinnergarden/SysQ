#!/usr/bin/env python3
"""Sync Tushare financial data (forecast, income) to local parquet.

Usage:
    python scripts/dev/sync_tushare_financial.py --tables forecast income

Output:
    data/tushare/forecast.parquet
    data/tushare/income.parquet
"""

import sys, time, warnings
from pathlib import Path

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
warnings.filterwarnings("ignore")

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "data" / "tushare"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Load stock list ──
import sqlite3
conn = sqlite3.connect(str(REPO / "data" / "meta.db"))
stocks = pd.read_sql("select ts_code, name from stock_basic", conn)
conn.close()
CSI800 = REPO / "data" / "qlib_bin" / "instruments" / "csi800.txt"
if CSI800.exists():
    target = [line.strip().split("\t")[0] for line in CSI800.read_text().strip().splitlines()]
else:
    target = stocks["ts_code"].tolist()
print(f"Target stocks: {len(target)}")

# ── Rate limiting ──
import tushare as ts
pro = ts.pro_api()

def safe_call(fn, max_retries=3, **kwargs):
    for attempt in range(max_retries):
        try:
            result = fn(**kwargs)
            if result is not None and len(result) > 0:
                return result
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2)
            else:
                print(f"  [WARN] Failed for {kwargs}: {e}")
    return pd.DataFrame()


# ── Sync forecast ──
def sync_forecast():
    print("\n=== Syncing forecast ===")
    all_rows = []
    total = len(target)
    for i, code in enumerate(target):
        df = safe_call(pro.forecast, ts_code=code, start_date="20100101", end_date="20251231")
        if df is not None and len(df) > 0:
            all_rows.append(df)
        if (i + 1) % 100 == 0:
            print(f"  {i+1}/{total} stocks ({time.strftime('%H:%M')})")
        time.sleep(0.3)  # rate limit

    if not all_rows:
        print("  No forecast data collected!")
        return

    result = pd.concat(all_rows, ignore_index=True)
    result = result.drop_duplicates(subset=["ts_code", "ann_date", "end_date", "type"])
    result = result.sort_values(["ts_code", "ann_date"]).reset_index(drop=True)

    out_path = OUT_DIR / "forecast.parquet"
    result.to_parquet(out_path, index=False)
    print(f"  Written: {out_path} ({len(result)} rows, {result['ts_code'].nunique()} stocks)")
    print(f"  Date range: ann_date {result['ann_date'].min()} ~ {result['ann_date'].max()}")
    print(f"  Types: {result['type'].value_counts().to_dict()}")


# ── Sync income ──
def sync_income():
    print("\n=== Syncing income ===")
    all_rows = []
    total = len(target)
    for i, code in enumerate(target):
        df = safe_call(pro.income, ts_code=code, start_date="20100101", end_date="20251231")
        if df is not None and len(df) > 0:
            all_rows.append(df)
        if (i + 1) % 100 == 0:
            print(f"  {i+1}/{total} stocks ({time.strftime('%H:%M')})")
        time.sleep(0.3)

    if not all_rows:
        print("  No income data collected!")
        return

    result = pd.concat(all_rows, ignore_index=True)
    result = result.drop_duplicates(subset=["ts_code", "ann_date", "end_date", "report_type", "end_type"])
    result = result.sort_values(["ts_code", "ann_date"]).reset_index(drop=True)

    # Keep only quarterly reports (report_type=1)
    result = result[result["report_type"] == 1].copy()

    out_path = OUT_DIR / "income.parquet"
    result.to_parquet(out_path, index=False)
    print(f"  Written: {out_path} ({len(result)} rows, {result['ts_code'].nunique()} stocks)")
    print(f"  Date range: ann_date {result['ann_date'].min()} ~ {result['ann_date'].max()}")
    print(f"  Has revenue: {result['revenue'].notna().sum() / len(result):.1%}")


if __name__ == "__main__":
    args = sys.argv[1:] if len(sys.argv) > 1 else ["forecast", "income"]
    if "forecast" in args:
        sync_forecast()
    if "income" in args:
        sync_income()
    print("\nDone.")
