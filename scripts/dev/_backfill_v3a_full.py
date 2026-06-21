#!/usr/bin/env python3
"""Backfill v3a_full as baseline for delayed 60d retest.

This backfills only the v3a_full feature set (83 features, 4 transforms),
which is the baseline for all 11 combos.  After this, each combo only
needs to materialize its delta transforms.
"""
import multiprocessing
multiprocessing.set_start_method("fork", force=True)

import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
from qsys.utils.logger import log

REPO = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO / "data" / "feature_cache"

FEATURE_SET_ID = "configs/features/retest_60d_combinations/v3a_full.yaml"
DATE_START = "2020-01-01"
DATE_END = "2025-12-31"
SOURCE_HASH = "tushare_daily_adj_csi800_20200101_20251231_v1"
UNIVERSE = "csi800"

log.info("=" * 50)
log.info("Backfill: v3a_full baseline")
log.info(f"  Period: {DATE_START} ~ {DATE_END}")
log.info(f"  Universe: {UNIVERSE}")
log.info(f"  Feature set: {FEATURE_SET_ID}")

# 1. Load raw panel from qlib
log.info("Loading raw panel from qlib...")
from qsys.data.adapter import QlibAdapter

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
raw = adapter.get_features(
    UNIVERSE, raw_fields,
    start_time=DATE_START, end_time=DATE_END,
)
log.info(f"Raw panel loaded: {raw.shape}, time={time.time()-t0:.1f}s")

if raw is None or len(raw) == 0:
    log.error("Empty panel")
    sys.exit(1)

panel = raw.reset_index().rename(columns={"datetime": "trade_date"})
panel = panel.loc[:, ~panel.columns.duplicated()]
panel["trade_date"] = panel["trade_date"].astype(str).str[:10]
if "instrument" in panel.columns and "ts_code" not in panel.columns:
    panel = panel.rename(columns={"instrument": "ts_code"})

log.info(f"Panel: {len(panel)} rows, {panel['ts_code'].nunique()} stocks, "
         f"{panel['trade_date'].nunique()} days")

# 2. Materialize
from qsys.feature.resolver_v2 import discover_feature_sets
from qsys.feature.materializer import materialize_feature_set_cache

discover_feature_sets()

t1 = time.time()
result = materialize_feature_set_cache(
    panel,
    feature_set_id=FEATURE_SET_ID,
    date_start=DATE_START, date_end=DATE_END,
    universe=UNIVERSE, source_manifest_hash=SOURCE_HASH,
    cache_root=str(CACHE_DIR), force=True,
)
mat_t = time.time() - t1

log.info(f"Materialize: transforms={result.get('transform_count', 0)}, "
         f"time={mat_t:.1f}s, hit={result.get('hit')}")
log.info(f"Matrix: {result.get('matrix_cache_path')}")
log.info(f"Features: {len(result.get('resolved_features', []))}")

# 3. Verify
cache_files = list((CACHE_DIR / "matrices").rglob("*.parquet"))
matrix_files = [p for p in cache_files if "v3a_full" in str(p)]
log.info(f"Matrix cache files for v3a_full: {len(matrix_files)}")
for mf in matrix_files:
    sz = mf.stat().st_size
    log.info(f"  {mf.name} ({sz / 1024:.1f} KB)")

log.info("✅ v3a_full backfill complete")
