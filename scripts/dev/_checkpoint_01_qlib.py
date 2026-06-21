#!/usr/bin/env python3
"""Checkpoint 1: qlib data access check."""
import multiprocessing
multiprocessing.set_start_method("fork", force=True)

import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qsys.data.adapter import QlibAdapter
from qlib.data import D

adapter = QlibAdapter()
adapter.init_qlib()
adapter.check_and_update()

# 1. Instrument count
inst = adapter.normalize_instruments("csi800")
print(f"=== Qlib Data Check ===")
print(f"D.instruments('csi800') via adapter: {len(inst)} codes")
if inst:
    print(f"  First 5: {inst[:5]}")
    print(f"  Last 5: {inst[-5:]}")

# 2. get_features with small window
t0 = time.time()
raw = adapter.get_features(
    "csi800",
    ["$close", "$open", "$volume", "$amount"],
    start_time="2024-06-01",
    end_time="2024-06-15",
)
elapsed = time.time() - t0
print(f"\nget_features(2024-06-01 ~ 2024-06-15):")
print(f"  Shape: {raw.shape}")
print(f"  Time: {elapsed:.1f}s")
if raw is not None and len(raw) > 0:
    insts = raw.index.get_level_values("instrument").nunique()
    dts = raw.index.get_level_values("datetime").nunique()
    print(f"  Instruments: {insts}")
    print(f"  Trading days: {dts}")
    print(f"  Columns: {raw.columns.tolist()}")
    print("✅ Qlib data access OK")
else:
    print("❌ No data returned — check qlib_bin content")
    import os
    print(f"  qlib_bin/features dirs: {len(os.listdir(adapter.qlib_dir / 'features'))}")
    print(f"  calendars: {Path(adapter.qlib_dir / 'calendars/day.txt').read_text()[:50]}")

# 3. Feature availability check
print(f"\n=== Semantic feature support fields ===")
support = adapter._semantic_support_fields()
print(f"  Support fields: {len(support)}")
for f in ["$margin_balance", "$roe", "$industry", "$op_cashflow"]:
    print(f"  {f}: {'✅' if f in support else '❌'} available")
