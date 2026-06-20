#!/usr/bin/env python3
"""CLI to resolve a FeatureSet YAML and write its manifest.

Usage:
    python scripts/dev/resolve_feature_set.py \\
        --feature-set value_growth_multibagger_v3a_features

    python scripts/dev/resolve_feature_set.py \\
        --feature-set configs/features/alpha_v1_clean_132.yaml \\
        --output-dir artifacts/feature_manifests

Exit code: 0 on success, 1 on validation failure, 2 on unresolved transforms.
"""

import sys
import traceback
from pathlib import Path

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import click


@click.command()
@click.option(
    "--feature-set",
    required=True,
    help="Feature set ID or YAML path to resolve",
)
@click.option(
    "--output-dir",
    default="artifacts/feature_manifests",
    show_default=True,
    help="Directory to write manifest JSON",
)
def main(feature_set: str, output_dir: str):
    from qsys.feature.resolver_v2 import resolve_feature_set, discover_feature_sets
    from qsys.feature.build_plan import build_plan_from_resolved
    from qsys.feature.manifest import build_feature_manifest, write_feature_manifest

    try:
        # Ensure index is built
        discover_feature_sets()

        # Resolve
        resolved = resolve_feature_set(feature_set)

        # Build plan
        plan = build_plan_from_resolved(resolved)

        # Manifest
        manifest = build_feature_manifest(resolved, plan)
        manifest_path = write_feature_manifest(manifest, output_dir)

        # Output summary
        print(f"✅ Resolved:          {resolved.feature_set_id}")
        print(f"   Source:            {resolved.source_path}")
        print(f"   Resolved features: {len(resolved.resolved_features)}")
        print(f"   Raw features:      {len(resolved.raw_features)}")
        print(f"   Derived features:  {len(resolved.derived_features)}")
        print(f"   Required transforms: {len(resolved.required_transforms)}")
        print(f"   Unresolved transforms: {len(plan.unresolved_transforms)}")
        print(f"   Warnings:          {len(plan.warnings)}")
        print(f"   Manifest:          {manifest_path}")

        if plan.unresolved_transforms:
            print(
                f"\n⚠️  Unresolved transforms: {plan.unresolved_transforms}",
                file=sys.stderr,
            )
            sys.exit(2)

    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
