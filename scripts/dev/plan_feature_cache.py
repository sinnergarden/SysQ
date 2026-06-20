#!/usr/bin/env python3
"""CLI to generate a cache plan for a FeatureSet YAML.

The cache plan computes cache keys and paths — it does NOT execute any
feature computation or write any parquet files.

Usage:
    python scripts/dev/plan_feature_cache.py \\
        --feature-set value_growth_multibagger_v3a_features \\
        --source-manifest-hash src_v1 \\
        --date-start 2018-01-01 --date-end 2025-12-31 \\
        --universe csi800 \\
        --output-dir artifacts/feature_cache_plans

Exit code: 0 on success, 1 on resolver failure.
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import click
import json


@click.command()
@click.option("--feature-set", required=True, help="Feature set ID or YAML path")
@click.option("--source-manifest-hash", default="dummy_source_v1", help="Source data version hash")
@click.option("--date-start", default=None, help="Start date (YYYY-MM-DD)")
@click.option("--date-end", default=None, help="End date (YYYY-MM-DD)")
@click.option("--universe", default=None, help="Universe (e.g. csi800)")
@click.option("--builder-hash", default=None, help="Builder code version hash")
@click.option("--pit-policy-hash", default=None, help="PIT policy version hash")
@click.option("--cache-root", default="data/feature_cache", help="Cache storage root")
@click.option("--output-dir", default="artifacts/feature_cache_plans", help="Output directory for cache plan JSON")
def main(feature_set, source_manifest_hash, date_start, date_end,
         universe, builder_hash, pit_policy_hash, cache_root, output_dir):

    from qsys.feature.cache import (
        FeatureCacheContext,
        compute_transform_cache_key,
        compute_matrix_cache_key,
        transform_cache_path,
        matrix_cache_path,
    )
    from qsys.feature.resolver_v2 import resolve_feature_set, discover_feature_sets
    from qsys.feature.build_plan import build_plan_from_resolved

    try:
        discover_feature_sets()
        resolved = resolve_feature_set(feature_set)
        plan = build_plan_from_resolved(resolved)

        context = FeatureCacheContext(
            feature_set_id=resolved.feature_set_id,
            date_start=date_start,
            date_end=date_end,
            universe=universe,
            source_manifest_hash=source_manifest_hash,
            builder_hash=builder_hash,
            pit_policy_hash=pit_policy_hash,
        )

        # Transform cache keys
        transform_keys: dict[str, str] = {}
        transform_paths: dict[str, str] = {}
        for tspec in resolved.required_transforms:
            # Derive input/output features from the resolved spec_sources
            info_for_transform = [
                s for s in resolved.spec_sources
                if s.get("compute_fn") == tspec
            ]
            output_feats = [s["name"] for s in info_for_transform]
            input_feats = list(output_feats)  # simplified: Phase 4+ uses TransformSpec

            ck = compute_transform_cache_key(
                tspec,
                input_features=input_feats,
                output_features=output_feats,
                compute_fn_hash=source_manifest_hash,  # simplified
                context=context,
            )
            transform_keys[tspec] = ck.key
            transform_paths[tspec] = str(transform_cache_path(tspec, ck.key, root=cache_root))

        # Matrix cache key
        matrix_ck = compute_matrix_cache_key(
            resolved.feature_set_id,
            resolved_features=list(resolved.resolved_features),
            required_transforms=list(resolved.required_transforms),
            context=context,
        )
        matrix_path = str(matrix_cache_path(resolved.feature_set_id, matrix_ck.key, root=cache_root))

        plan_data = {
            "feature_set_id": resolved.feature_set_id,
            "matrix_cache_key": matrix_ck.key,
            "matrix_cache_path": matrix_path,
            "transform_cache_keys": transform_keys,
            "transform_cache_paths": transform_paths,
            "resolved_features_count": len(resolved.resolved_features),
            "warning_count": len(plan.warnings),
        }

        # Write
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{resolved.feature_set_id}.json"
        out_path.write_text(
            json.dumps(plan_data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        # Print summary
        print(f"✅ Cache plan:        {resolved.feature_set_id}")
        print(f"   Matrix key:        {matrix_ck.key}")
        print(f"   Matrix path:       {matrix_path}")
        print(f"   Transforms:        {len(transform_keys)}")
        print(f"   Transform keys:    {list(transform_keys.values())}")
        print(f"   Plan path:         {out_path}")

        if plan.warnings:
            print(f"\n⚠️  Warnings ({len(plan.warnings)}):")
            for w in plan.warnings[:3]:
                print(f"     - {w}")
            if len(plan.warnings) > 3:
                print(f"     ... and {len(plan.warnings) - 3} more")

    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
