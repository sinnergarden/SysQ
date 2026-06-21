#!/usr/bin/env python3
"""Backfill cache for a single feature_id.

Usage:
    python scripts/dev/backfill_single_feature_cache.py \\
        --feature-id margin_trend_confirm_score \\
        --source-panel data/source_panel.parquet \\
        --source-manifest-hash src_v1 \\
        --date-start 2020-01-01 --date-end 2025-12-31 \\
        --universe csi800 --overwrite
"""
import multiprocessing; multiprocessing.set_start_method("fork", force=True)

import sys
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

import click
import pandas as pd
from qsys.feature.feature_compute_registry import get_spec, has_spec
from qsys.feature.feature_store import FeatureStore


@click.command()
@click.option("--feature-id", required=True)
@click.option("--source-panel", type=click.Path(exists=True), required=True)
@click.option("--source-manifest-hash", default="backfill_v1")
@click.option("--date-start", default=None)
@click.option("--date-end", default=None)
@click.option("--universe", default=None)
@click.option("--feature-cache-root", default="data/feature_cache/features")
@click.option("--overwrite", is_flag=True)
def main(feature_id, source_panel, source_manifest_hash,
         date_start, date_end, universe, feature_cache_root, overwrite):
    spec = get_spec(feature_id)
    if spec is None:
        click.echo(f"❌ No compute spec for '{feature_id}'", err=True)
        sys.exit(1)

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

    click.echo(f"Computing '{feature_id}'...")
    result = spec.compute_fn(raw)

    from qsys.feature.feature_store import FeatureCacheKey, compute_feature_cache_key

    fk = FeatureCacheKey(
        feature_id=feature_id,
        universe=universe,
        date_start=date_start,
        date_end=date_end,
        source_manifest_hash=source_manifest_hash,
        compute_fn_hash=spec.compute_fn_hash,
        pit_policy="rolling_past",
    )
    ck = compute_feature_cache_key(fk)

    store = FeatureStore(root=feature_cache_root)
    meta = {
        "source_manifest_hash": source_manifest_hash,
        "compute_fn_hash": spec.compute_fn_hash,
        "universe": universe,
        "date_start": date_start,
        "date_end": date_end,
        "pit_policy": "rolling_past",
    }
    path = store.write_feature(feature_id, result, cache_key=ck, metadata=meta, overwrite=overwrite)
    click.echo(f"✅ Written: {path}")


if __name__ == "__main__":
    main()
