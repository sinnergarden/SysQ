#!/usr/bin/env python3
"""Backfill feature caches for a FeatureSet from a source panel file.

Usage:
    python scripts/dev/backfill_feature_cache.py \\
        --feature-set value_growth_multibagger_v3a_features \\
        --source-panel data/test_panel.parquet \\
        --source-manifest-hash src_v1 \\
        --date-start 2018-01-01 --date-end 2025-12-31 \\
        --universe csi800 \\
        --cache-root data/feature_cache \\
        --force

Exit code: 0 on success, 1 on failure.
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import click

from qsys.utils.logger import log


@click.command()
@click.option("--feature-set", required=True, help="Feature set ID or YAML path")
@click.option("--source-panel", required=True, type=click.Path(exists=True),
              help="Path to source panel (parquet/feather/csv)")
@click.option("--source-manifest-hash", default="backfill_v1", help="Source data version hash")
@click.option("--date-start", default=None, help="Start date (YYYY-MM-DD)")
@click.option("--date-end", default=None, help="End date (YYYY-MM-DD)")
@click.option("--universe", default=None, help="Universe (e.g. csi800)")
@click.option("--builder-hash", default=None, help="Builder code version hash")
@click.option("--cache-root", default="data/feature_cache",
              help="Cache storage root")
@click.option("--force", is_flag=True, default=False,
              help="Force recomputation even if cache hits")
def main(feature_set, source_panel, source_manifest_hash,
         date_start, date_end, universe, builder_hash,
         cache_root, force):
    import pandas as pd

    # Load source panel
    source_path = Path(source_panel)
    suffix = source_path.suffix.lower()
    if suffix == ".parquet":
        raw = pd.read_parquet(source_path)
    elif suffix == ".feather":
        raw = pd.read_feather(source_path)
    elif suffix == ".csv":
        raw = pd.read_csv(source_path)
    else:
        click.echo(f"Unsupported format: {suffix}", err=True)
        sys.exit(1)

    click.echo(f"Loaded panel: {len(raw)} rows, {list(raw.columns)}")

    # Materialize
    from qsys.feature.materializer import materialize_feature_set_cache

    try:
        result = materialize_feature_set_cache(
            raw,
            feature_set_id=feature_set,
            date_start=date_start,
            date_end=date_end,
            universe=universe,
            source_manifest_hash=source_manifest_hash,
            builder_hash=builder_hash,
            cache_root=cache_root,
            force=force,
        )

        if result["hit"]:
            click.echo(f"✅ Cache hit: {result['feature_set_id']}")
            click.echo(f"   Matrix:  {result['matrix_cache_path']}")
        else:
            click.echo(f"✅ Materialized: {result['feature_set_id']}")
            click.echo(f"   Transforms:    {result.get('transform_count', 0)}")
            click.echo(f"   Matrix:        {result['matrix_cache_path']}")
            click.echo(f"   Manifest:      {result.get('manifest_path', 'N/A')}")
            click.echo(f"   Builder mode:  {result['builder_mode']}")
            if result.get("warnings"):
                click.echo(f"   Warnings:      {len(result['warnings'])}")

        # Verify features
        n_resolved = len(result["resolved_features"])
        click.echo(f"   Features:      {n_resolved}")

    except ValueError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ Unexpected: {e}", err=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
