#!/usr/bin/env python3
"""Validation backfill — small-scale full-chain test using qlib adapter path.

Strategy: use the existing QlibAdapter.get_features() which correctly loads
all semantic support fields (industry, margin, financials, etc.) and runs
the builder pipeline.  The materializer operates on this enriched panel.

This validates:
  1. QlibAdapter.get_features() works for the superset feature set
  2. materialize_feature_set_cache() writes transform + matrix caches
  3. All resolvable features end up in the matrix
  4. Cache hit works correctly on second call
"""
import multiprocessing
multiprocessing.set_start_method("fork", force=True)

import sys, time, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
from qsys.utils.logger import log

REPO = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO / "data" / "feature_cache_val"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

FEATURE_SET_ID = "retest_60d_all_candidate_features"
DATE_START = "2023-06-01"
DATE_END = "2023-06-30"
SOURCE_HASH = "val_v1"
UNIVERSE = "csi800"

# ── 1. Build raw panel via qlib adapter (enriched with all semantic fields) ──
log.info("Loading raw panel from qlib...")
from qsys.data.adapter import QlibAdapter

adapter = QlibAdapter()
adapter.init_qlib()
adapter.check_and_update()

from qsys.feature.resolver_v2 import discover_feature_sets
discover_feature_sets()

# Build raw fields list (only qlib native fields; derived features are
# computed by materializer via transform_registry)
# materializer will compute derived features via transform_registry
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
log.info(f"Raw panel from qlib: {raw.shape}, time={time.time()-t0:.1f}s")

if raw is None or len(raw) == 0:
    log.error("Empty raw panel from qlib — aborting")
    sys.exit(1)

# Convert to flat DataFrame for materializer (trade_date, ts_code columns)
panel = raw.reset_index().rename(columns={"datetime": "trade_date"})
panel = panel.loc[:, ~panel.columns.duplicated()]
panel["trade_date"] = panel["trade_date"].astype(str).str[:10]

log.info(f"Panel: {len(panel)} rows, {panel['instrument'].nunique()} stocks, "
         f"{len(panel['trade_date'].unique())} days")
log.info(f"Columns ({len(panel.columns)}): {panel.columns.tolist()[:15]}...")

# Rename instrument→ts_code for cache compatibility
if "instrument" in panel.columns and "ts_code" not in panel.columns:
    panel = panel.rename(columns={"instrument": "ts_code"})

# ── 2. Resolve ──
from qsys.feature.resolver_v2 import resolve_feature_set
from qsys.feature.build_plan import build_plan_from_resolved

resolved = resolve_feature_set(FEATURE_SET_ID)
plan = build_plan_from_resolved(resolved)
log.info(f"Resolved: {len(resolved.resolved_features)} features, "
         f"{len(resolved.required_transforms)} transforms")

# ── 3. Materialize ──
from qsys.feature.materializer import materialize_feature_set_cache

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

# ── 4. Verify cache files ──
cache_files = list(CACHE_DIR.rglob("*.parquet"))
meta_files = list(CACHE_DIR.rglob("*.meta.json"))
log.info(f"Cache files: {len(cache_files)} parquet, {len(meta_files)} meta")

matrix_path = next((p for p in cache_files if "matrices" in str(p)), None)
if matrix_path:
    df = pd.read_parquet(matrix_path)
    log.info(f"Matrix: {len(df)} rows x {len(df.columns)} cols")
    missing = [f for f in resolved.resolved_features if f not in df.columns]
    if missing:
        log.error(f"Missing features in matrix ({len(missing)}): {missing[:10]}")
        sys.exit(1)
    log.info(f"✅ All {len(resolved.resolved_features)} resolved features present")

# ── 5. Cache hit test ──
t2 = time.time()
result2 = materialize_feature_set_cache(
    panel,
    feature_set_id=FEATURE_SET_ID,
    date_start=DATE_START, date_end=DATE_END,
    universe=UNIVERSE, source_manifest_hash=SOURCE_HASH,
    cache_root=str(CACHE_DIR), force=False,
)
hit_t = time.time() - t2
log.info(f"Cache 2nd call: hit={result2['hit']}, time={hit_t:.3f}s")
if not result2["hit"]:
    log.error("Second call should have been cache hit!")
    sys.exit(1)

# ── 6. Cleanup ──
import shutil
shutil.rmtree(CACHE_DIR, ignore_errors=True)

log.info("=" * 50)
log.info("✅ VALIDATION BACKFILL PASSED")
log.info(f"   Stocks: {panel['ts_code'].nunique()}")
log.info(f"   Features resolved: {len(resolved.resolved_features)}")
log.info(f"   Transforms: {result.get('transform_count', 0)}")
log.info(f"   Materialize time: {mat_t:.1f}s")
log.info(f"   Cache hit time: {hit_t:.3f}s ({mat_t/hit_t:.0f}x speedup)")
