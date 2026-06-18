#!/usr/bin/env python3
"""Test for feature group resolver and manifest builder.

Usage:
    python tests/features/test_feature_group_resolver_manifest.py

Checks:
    1. read old config without feature_groups: resolves unchanged
    2. explicit features + feature_groups: stable de-duplicate
    3. manifest has correct status fields
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import numpy as np

pass_count = 0
fail_count = 0


def check(condition: bool, msg: str):
    global pass_count, fail_count
    if condition:
        pass_count += 1
        print(f"  ✅ {msg}")
    else:
        fail_count += 1
        print(f"  ❌ {msg}")


def test_old_config_no_feature_groups():
    """Config without 'feature_groups' key returns 'features' unchanged."""
    from qsys.feature.resolver import resolve_feature_list

    # Simulate old-style config with only explicit features
    config = {
        "features": ["ret_60d", "ret_120d", "roe", "pb_raw"],
    }
    resolved = resolve_feature_list(config)
    check(resolved == ["ret_60d", "ret_120d", "roe", "pb_raw"], "resolved matches explicit list")
    check(len(resolved) == 4, "no extra features added")

    # Empty features list
    config2 = {"features": []}
    resolved2 = resolve_feature_list(config2)
    check(resolved2 == [], "empty features list returns empty list")

    # No 'features' key at all
    config3 = {}
    resolved3 = resolve_feature_list(config3)
    check(resolved3 == [], "empty config returns empty list")


def test_explicit_features_plus_feature_groups():
    """Both explicit features and feature_groups: union, stable dedup, explicit first."""
    from qsys.feature.resolver import resolve_feature_list

    # Fundamental context has many features. Use a small group like 'regime'.
    config = {
        "features": ["ret_60d", "market_breadth"],
        "feature_groups": ["regime", "microstructure"],
    }

    resolved = resolve_feature_list(config)
    check(len(resolved) > 0, "resolved list is non-empty")

    # Explicit features come first, in order
    check(resolved[0] == "ret_60d", "explicit 'ret_60d' is first")
    check(resolved[1] == "market_breadth", "explicit 'market_breadth' is second")

    # market_breadth appears only once (deduped), in the explicit position
    # regime group features come next, then microstructure
    regime_start = 2
    # Check regime features are present
    check("market_breadth" in resolved, "market_breadth present (explicit)")
    check("limit_up_breadth" in resolved, "limit_up_breadth present (from regime group)")

    # No duplicates
    check(len(resolved) == len(set(resolved)), "no duplicate features after resolve")


def test_manifest_status_fields():
    """build_feature_manifest has correct status fields: existing, added, skipped."""
    from qsys.feature.resolver import resolve_feature_list, build_feature_manifest

    config = {
        "features": ["ret_60d", "ret_120d"],
        "feature_groups": ["liquidity"],
    }
    resolved = resolve_feature_list(config)

    # Build expansions dict
    from qsys.feature.registry import FEATURE_GROUPS
    expansions = {
        "liquidity": list(FEATURE_GROUPS["liquidity"]["features"]),
        "v3a_margin": list(FEATURE_GROUPS["v3a_margin"]["features"]),
    }

    manifest = build_feature_manifest(resolved, expansions)

    # Verify all required keys are present
    for entry in manifest:
        for key in ["feature_name", "group", "formula", "required_fields", "status", "skip_reason", "feature_schema_hash"]:
            check(key in entry, f"manifest entry key '{key}' present for '{entry['feature_name']}'")

    # Check schema hash is the same across all entries
    hashes = [e["feature_schema_hash"] for e in manifest]
    check(len(set(hashes)) == 1, "all entries share the same feature_schema_hash")
    check(len(hashes[0]) == 16, "schema hash is 16 hex chars")

    # status="existing" for explicit features in the resolved list
    explicit_entries = [e for e in manifest if e["feature_name"] in ("ret_60d", "ret_120d")]
    for e in explicit_entries:
        check(e["status"] == "existing", f"'{e['feature_name']}' status is 'existing'")

    # status="added" for features from groups that are in expansions AND in resolved
    from qsys.feature.registry import FEATURE_GROUPS
    liquidity_features = FEATURE_GROUPS["liquidity"]["features"]
    added_entries = [
        e for e in manifest
        if e["feature_name"] in liquidity_features
        and e["feature_name"] not in ("ret_60d", "ret_120d")
    ]
    for e in added_entries:
        check(
            e["status"] == "added",
            f"'{e['feature_name']}' (from liquidity group) status is 'added'",
        )

    # status="skipped" for features in non-resolved groups (v3a_margin)
    from qsys.feature.registry import FEATURE_GROUPS
    margin_features = FEATURE_GROUPS["v3a_margin"]["features"]
    skipped_entries = [e for e in manifest if e["feature_name"] in margin_features]
    for e in skipped_entries:
        check(
            e["status"] == "skipped",
            f"'{e['feature_name']}' (from v3a_margin) status is 'skipped'",
        )
        check(
            e["skip_reason"] is not None,
            f"'{e['feature_name']}' has skip_reason",
        )

    # "explicit" group label for explicit features
    for e in explicit_entries:
        check(
            e["group"] == "explicit",
            f"'{e['feature_name']}' group is 'explicit'",
        )


def test_feature_group_only_resolve():
    """Only feature_groups (no explicit features) resolves correctly."""
    from qsys.feature.resolver import resolve_feature_list

    config = {
        "feature_groups": ["microstructure", "liquidity"],
    }
    resolved = resolve_feature_list(config)

    from qsys.feature.registry import FEATURE_GROUPS
    expected = (
        FEATURE_GROUPS["microstructure"]["features"]
        + FEATURE_GROUPS["liquidity"]["features"]
    )
    check(resolved == expected, "only groups: resolves in group order, no dedup needed")


def test_unknown_group_raises():
    """Unknown group name in feature_groups raises KeyError."""
    from qsys.feature.resolver import resolve_feature_list

    config = {
        "feature_groups": ["nonexistent_group"],
    }
    try:
        resolve_feature_list(config)
        check(False, "should have raised KeyError for unknown group")
    except KeyError:
        check(True, "unknown group raises KeyError")
    except Exception as e:
        check(False, f"unexpected exception: {e}")


def main():
    global pass_count, fail_count

    print("\n=== 1. Old config without feature_groups: resolves unchanged ===")
    test_old_config_no_feature_groups()

    print("\n=== 2. Explicit features + feature_groups: stable de-duplicate ===")
    test_explicit_features_plus_feature_groups()

    print("\n=== 3. Manifest has correct status fields ===")
    test_manifest_status_fields()

    print("\n=== 4. Only feature_groups resolves correctly ===")
    test_feature_group_only_resolve()

    print("\n=== 5. Unknown group raises KeyError ===")
    test_unknown_group_raises()

    print(f"\n{'=' * 40}")
    print(f"Results: {pass_count} passed, {fail_count} failed")
    if fail_count > 0:
        sys.exit(1)
    print("All checks passed ✅")


if __name__ == "__main__":
    main()
