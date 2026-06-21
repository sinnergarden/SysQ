#!/usr/bin/env python3
"""Quick test: verify qlib data accessibility."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qsys.data.adapter import QlibAdapter

adapter = QlibAdapter()
adapter.init_qlib()
adapter.check_and_update()

raw = adapter.get_features('csi800', ['$close', '$open', '$volume'], start_time='2025-01-01', end_time='2025-03-01')
print(f"Data shape: {raw.shape}")
print(f"Index names: {raw.index.names}")
cols = raw.columns.tolist()
print(f"Columns: {cols[:8]}")
print(f"Total columns: {len(cols)}")
dt_idx = raw.index.get_level_values("datetime")
print(f"Date range: {dt_idx.min()} to {dt_idx.max()}")
print(f"Number of instruments: {raw.index.get_level_values('instrument').nunique()}")

adapter.check_and_update()
print("Data access: OK")
