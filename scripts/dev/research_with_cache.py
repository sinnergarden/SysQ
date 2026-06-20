#!/usr/bin/env python3
"""Research with feature cache — standalone CLI for cache-accelerated feature loading.

Usage:
    python scripts/dev/research_with_cache.py \\
        --feature-set value_growth_multibagger_v3a_features \\
        --source-panel data/xxx.parquet \\
        --source-manifest-hash src_v1 \\
        --date-start 2018-01-01 --date-end 2025-12-31 \\
        --universe csi800 \\
        --use-feature-cache \\
        --materialize-on-miss

Exit code: 0 on success, 1 on failure.
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import click
import pandas as pd


@click.command()
@click.option("--feature-set", required=True, help="Feature set ID or YAML path")
@click.option("--source-panel", type=click.Path(exists=True),
              help="Path to source panel parquet/feather/csv")
@click.option("--source-manifest-hash", default="research_v1", help="Source data version hash")
@click.option("--date-start", default=None, help="Start date (YYYY-MM-DD)")
@click.option("--date-end", default=None, help="End date (YYYY-MM-DD)")
@click.option("--universe", default=None, help="Universe (e.g. csi800)")
@click.option("--feature-cache-root", default="data/feature_cache", help="Cache root directory")
@click.option("--use-feature-cache", is_flag=True, default=False, help="Enable cache reading")
@click.option("--materialize-on-miss", is_flag=True, default=False, help="Auto-materialize on miss")
@click.option("--output", type=click.Path(), help="Optional: save output feature matrix to parquet")
def main(feature_set, source_panel, source_manifest_hash,
         date_start, date_end, universe,
         feature_cache_root, use_feature_cache, materialize_on_miss, output):

    from qsys.feature.cache_loader import load_feature_matrix_with_cache
    from qsys.utils.logger import log

    # Load source panel if provided
    if source_panel:
        sp = Path(source_panel)
        suff = sp.suffix.lower()
        if suff == ".parquet":
            raw = pd.read_parquet(sp)
        elif suff == ".feather":
            raw = pd.read_feather(sp)
        elif suff == ".csv":
            raw = pd.read_csv(sp)
        else:
            click.echo(f"Unsupported format: {suff}", err=True)
            sys.exit(1)
        click.echo(f"Loaded panel: {len(raw)} rows, {list(raw.columns)}")
    else:
        raw = pd.DataFrame()

    click.echo(f"Feature set:  {feature_set}")
    click.echo(f"Cache:        {'ENABLED' if use_feature_cache else 'DISABLED'}")
    click.echo(f"Materialize:  {'YES' if materialize_on_miss else 'NO'}")
    click.echo(f"Root:         {feature_cache_root}")

    try:
        result = load_feature_matrix_with_cache(
            raw,
            feature_set_id=feature_set,
            date_start=date_start,
            date_end=date_end,
            universe=universe,
            source_manifest_hash=source_manifest_hash,
            cache_root=feature_cache_root,
            use_feature_cache=use_feature_cache,
            materialize_on_miss=materialize_on_miss,
        )

        click.echo(f"\n✅ Matrix loaded: {len(result)} rows × {len(result.columns)} cols")
        click.echo(f"   Columns: {list(result.columns)[:8]}...")

        if output:
            out_path = Path(output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            result.to_parquet(out_path, index=False)
            click.echo(f"   Written:  {out_path}")

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
