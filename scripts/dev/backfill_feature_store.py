#!/usr/bin/env python3
"""Backfill all features in a FeatureSet YAML into the per-feature store.

Usage:
    python scripts/dev/backfill_feature_store.py \\
        --feature-set configs/features/retest_60d_all_candidate_features.yaml \\
        --source-panel data/source_panel.parquet \\
        --source-manifest-hash src_v1 \\
        --date-start 2020-01-01 --date-end 2025-12-31 \\
        --universe csi800 --compute-missing
"""
import multiprocessing; multiprocessing.set_start_method("fork", force=True)

import sys, time
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

import click
import pandas as pd
from qsys.feature.feature_store import FeatureStore
from qsys.feature.feature_compute_registry import get_spec, has_spec
from qsys.feature.resolver_v2 import resolve_feature_set, discover_feature_sets
from qsys.feature.build_plan import build_plan_from_resolved
from qsys.utils.logger import log


@click.command()
@click.option("--feature-set", required=True)
@click.option("--source-panel", type=click.Path(exists=True), required=True)
@click.option("--source-manifest-hash", default="backfill_v1")
@click.option("--date-start", default=None)
@click.option("--date-end", default=None)
@click.option("--universe", default=None)
@click.option("--feature-cache-root", default="data/feature_cache/features")
@click.option("--compute-missing", is_flag=True)
@click.option("--overwrite", is_flag=True)
def main(feature_set, source_panel, source_manifest_hash,
         date_start, date_end, universe, feature_cache_root,
         compute_missing, overwrite):
    # Load panel
    suffix = Path(source_panel).suffix.lower()
    if suffix == ".parquet":
        raw = pd.read_parquet(source_panel)
    elif suffix == ".feather":
        raw = pd.read_feather(source_panel)
    elif suffix == ".csv":
        raw = pd.read_csv(source_panel)
    else:
        click.echo(f"Unsupported: {suffix}", err=True)
        sys.exit(1)

    # Ensure registry_v2 has all features populated
    from scripts.dev.populate_feature_specs import populate_registry
    populate_registry()
    # Manually register features that populate_registry doesn't cover
    from qsys.feature.registry_v2 import register, get_by_name, FeatureSpec
    for _spec in [
        FeatureSpec("amount_log_ind_zscore","amount_log_ind_zscore","liquidity","derived",
            None,("amount_log","industry"),"build_liquidity_features","cross_sectional",
            "none","active","amount_log z-score within industry"),
        FeatureSpec("turnover_rate_ind_zscore","turnover_rate_ind_zscore","liquidity","derived",
            None,("turnover_rate","industry"),"build_liquidity_features","cross_sectional",
            "none","active","turnover_rate z-score within industry"),
        FeatureSpec("forecast_type_score","forecast_type_score","growth_confirmation_v0","derived",
            None,("ts_code",),"build_growth_confirmation_features","point_in_time",
            "panel","active","Forecast type score"),
        FeatureSpec("forecast_stale_days","forecast_stale_days","growth_confirmation_v0","derived",
            None,("ts_code",),"build_growth_confirmation_features","point_in_time",
            "none","active","Days since forecast"),
        FeatureSpec("has_forecast","has_forecast","growth_confirmation_v0","derived",
            None,("ts_code",),"build_growth_confirmation_features","point_in_time",
            "none","active","Has forecast binary"),
        FeatureSpec("breakout_252d_high","breakout_252d_high","growth_confirmation_v0","derived",
            None,("close",),"build_growth_confirmation_features","rolling_past",
            "none","active","Close >= previous 252d high"),
        FeatureSpec("days_since_252d_high","days_since_252d_high","growth_confirmation_v0","derived",
            None,("close",),"build_growth_confirmation_features","rolling_past",
            "none","active","Days since 252d high"),
    ]:
        if not get_by_name(_spec.name):
            try: register(_spec)
            except ValueError: pass

    # Resolve
    discover_feature_sets()
    resolved = resolve_feature_set(feature_set)
    feature_ids = list(resolved.resolved_features)

    from qsys.feature.feature_store import FeatureCacheKey, compute_feature_cache_key
    from qsys.feature.feature_compute_registry import _PHASE1_HASH, compute_phase1_batch, get_spec

    store = FeatureStore(root=feature_cache_root)
    meta = {
        "source_manifest_hash": source_manifest_hash,
        "compute_fn_hash": _PHASE1_HASH,
        "universe": universe,
        "date_start": date_start,
        "date_end": date_end,
        "pit_policy": "rolling_past",
    }

    ok, fail = 0, 0
    t0 = time.time()

    # Phase 1: scan and classify
    cached_ids: list[str] = []
    missing_ids: list[str] = []
    invalid_ids: list[str] = []

    for fid in feature_ids:
        fk = FeatureCacheKey(
            feature_id=fid,
            universe=universe,
            source_manifest_hash=source_manifest_hash,
            compute_fn_hash=_PHASE1_HASH,
            pit_policy="rolling_past",
        )
        ck = compute_feature_cache_key(fk)

        if store.exists(fid, ck):
            # Validate existing cache with strict source hash check
            try:
                store.read_feature(fid, expected_cache_key=ck, strict_source_hash=source_manifest_hash)
                if not overwrite:
                    cached_ids.append(fid)
                    continue
            except ValueError:
                invalid_ids.append(fid)
                if not overwrite:
                    click.echo(f"❌ {fid}: invalid existing cache — set --overwrite to replace")
                    fail += 1
                    continue

        # Skip qlib expressions ($prefix or function calls) — they are
        # NOT produced by the builder but already present in raw panel
        if fid.startswith("$") or "(" in fid or ")" in fid or "/" in fid:
            click.echo(f"  ⏭️  {fid}: qlib expression (skipped)")
            ok += 1
            continue

        missing_ids.append(fid)

    if invalid_ids and not overwrite:
        click.echo(f"\n❌ {len(invalid_ids)} invalid cache entries detected. Use --overwrite to force.")
        sys.exit(1)

    # Phase 2: batch compute all missing features at once
    if missing_ids and not compute_missing:
        click.echo(
            f"❌ {len(missing_ids)} features not cached and --compute-missing not set. "
            f"Missing: {missing_ids[:5]}...",
            err=True,
        )
        sys.exit(1)

    if missing_ids:
        click.echo(f"Batch computing {len(missing_ids)} features ({len(cached_ids)} cached)...")
        try:
            batch_result = compute_phase1_batch(raw, missing_ids)
        except Exception as e:
            click.echo(f"❌ Batch compute failed: {e}", err=True)
            sys.exit(1)

        for fid in missing_ids:
            try:
                fk = FeatureCacheKey(
                    feature_id=fid,
                    universe=universe,
                    source_manifest_hash=source_manifest_hash,
                    compute_fn_hash=_PHASE1_HASH,
                    pit_policy="rolling_past",
                )
                ck = compute_feature_cache_key(fk)
                if fid not in batch_result.columns:
                    # Check if it's a raw field already in the panel
                    if fid in raw.columns:
                        df_part = raw[["trade_date", "ts_code", fid]].copy()
                        click.echo(f"  ⏭️  {fid}: raw field (read from panel)")
                    else:
                        click.echo(f"  ⚠️  {fid}: not produced by builder (skipped)")
                        continue
                else:
                    df_part = batch_result[["trade_date", "ts_code", fid]]
                store.write_feature(fid, df_part, cache_key=ck, metadata=meta, overwrite=overwrite)
                ok += 1
            except Exception as e:
                click.echo(f"❌ {fid}: {e}")
                fail += 1
    else:
        click.echo(f"All {len(feature_ids)} features already cached and valid (use --overwrite to recompute)")
        ok = len(cached_ids)

    elapsed = time.time() - t0
    click.echo(f"\n{'='*50}")
    click.echo(f"Backfill: {feature_set}")
    click.echo(f"  Total: {len(feature_ids)}, OK: {ok}, Fail: {fail}")
    click.echo(f"  Time: {elapsed:.1f}s")
    if fail > 0:
        click.echo(f"❌ {fail} failures — exiting with code 1")
        sys.exit(1)
    click.echo("✅ All features backfilled")


if __name__ == "__main__":
    main()
