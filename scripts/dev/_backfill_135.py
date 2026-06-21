#!/usr/bin/env python3
"""Full superset backfill: build raw panel from qlib, then batch compute all 135 features."""
import multiprocessing
multiprocessing.set_start_method("fork", force=True)

import sys, time, subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
from qsys.utils.logger import log
from qsys.data.adapter import QlibAdapter

REPO = Path(__file__).resolve().parents[2]
PANEL_DIR = REPO / "data" / "raw_panels"
PANEL_DIR.mkdir(parents=True, exist_ok=True)

SOURCE_HASH = "tushare_daily_adj_csi800_20200101_20251231_v1"
DATE_START = "2020-01-01"
DATE_END = "2025-12-31"
UNIVERSE = "csi800"

# 1. Build raw panel via qlib adapter
log.info("Loading raw panel from qlib (this takes ~1-2 min)...")
adapter = QlibAdapter()
adapter.init_qlib()
adapter.check_and_update()

raw_fields = [
    "$close", "$open", "$high", "$low", "$volume", "$amount", "$factor", "$vwap",
    "$high_limit", "$low_limit",
    "$pe", "$pb", "$total_mv", "$circ_mv",
    "$roe", "$grossprofit_margin", "$debt_to_assets", "$current_ratio",
    "$net_income", "$revenue", "$op_cashflow", "$total_assets", "$equity",
    "$margin_balance", "$margin_buy_amount", "$margin_repay_amount",
    "$margin_total_balance", "$lend_volume", "$lend_sell_volume", "$lend_repay_volume",
    "$net_inflow", "$big_inflow",
    "$industry",
]

t0 = time.time()
raw = adapter.get_features(UNIVERSE, raw_fields, start_time=DATE_START, end_time=DATE_END)
log.info(f"Raw panel loaded: {raw.shape}, time={time.time()-t0:.1f}s")

panel = raw.reset_index().rename(columns={"datetime": "trade_date"})
panel = panel.loc[:, ~panel.columns.duplicated()]
panel["trade_date"] = panel["trade_date"].astype(str).str[:10]
if "instrument" in panel.columns and "ts_code" not in panel.columns:
    panel = panel.rename(columns={"instrument": "ts_code"})

log.info(f"Panel: {len(panel)} rows, {panel['ts_code'].nunique()} stocks, {panel['trade_date'].nunique()} days")

# Save panel
panel_path = PANEL_DIR / f"raw_panel_csi800_{DATE_START}_{DATE_END}.parquet"
panel.to_parquet(panel_path, index=False)
log.info(f"Panel saved: {panel_path} ({panel_path.stat().st_size / 1024 / 1024:.0f} MB)")

# 2. Run batch backfill
log.info("Running batch backfill for 135 features...")
t1 = time.time()
result = subprocess.run([
    "python", str(REPO / "scripts/dev/backfill_feature_store.py"),
    "--feature-set", str(REPO / "configs/features/retest_60d_all_candidate_features.yaml"),
    "--source-panel", str(panel_path),
    "--source-manifest-hash", SOURCE_HASH,
    "--date-start", DATE_START,
    "--date-end", DATE_END,
    "--universe", UNIVERSE,
    "--compute-missing",
    "--feature-cache-root", str(REPO / "data/feature_cache/features"),
], capture_output=True, text=True, timeout=3600)

bf_t = time.time() - t1
log.info(f"Backfill stdout:\n{result.stdout}")
if result.stderr:
    log.warning(f"Backfill stderr:\n{result.stderr[:500]}")

if result.returncode != 0:
    log.error(f"Backfill failed with rc={result.returncode}")
    sys.exit(1)

log.info(f"Backfill completed in {bf_t:.1f}s")

# 3. Summary
feature_dir = REPO / "data/feature_cache/features"
cached_ids = sorted(d.name for d in feature_dir.iterdir() if d.is_dir())
log.info(f"Total features cached: {len(cached_ids)}")
log.info(f"Disk usage: {sum(d.stat().st_size for d in feature_dir.rglob('*.parquet')) / 1024 / 1024:.0f} MB")
