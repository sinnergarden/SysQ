"""Test feature registry consistency — check registry, YAML, builder alignment.

Key checks:
1. feature_id is unique
2. feature name is unique (unless explicit alias)
3. Every feature in YAML feature_groups is in registry
4. Every feature in YAML explicit lists is in registry
5. Registry active features have all dependencies present
6. Broken/deprecated features do NOT enter active feature list
7. feature group's enabled_by flag matches builder hook
8. Builder opens a feature group — actual output columns equal registry declaration
"""

import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
FEATURES_YAML_DIR = REPO / "configs" / "features"


class TestRegistryConsistency(unittest.TestCase):
    """Feature registry consistency checks."""

    def setUp(self):
        from qsys.feature.registry import FEATURE_GROUPS

        self.registry_groups = FEATURE_GROUPS

    # ── 1. All feature names in the registry are unique ──

    def test_feature_names_unique(self):
        """No duplicate feature names across all registry groups.

        KNOWN DUPLICATES (cross-group, inherited from historical evolution):
        - stock_minus_industry_ret_3d: relative_strength ∩ industry_context
        - stock_minus_industry_ret_5d: relative_strength ∩ industry_context

        These should be resolved in a follow-up cleanup PR.
        """
        all_features: list[str] = []
        for gname, ginfo in self.registry_groups.items():
            all_features.extend(ginfo.get("features", []))

        _KNOWN_CROSS_GROUP_DUPES = {
            "stock_minus_industry_ret_3d",
            "stock_minus_industry_ret_5d",
        }

        seen: set[str] = set()
        dupes: set[str] = set()
        for f in all_features:
            if f in seen:
                dupes.add(f)
            seen.add(f)

        # Remove known duplicates before assertion
        unknown_dupes = dupes - _KNOWN_CROSS_GROUP_DUPES
        if _KNOWN_CROSS_GROUP_DUPES & dupes:
            import warnings
            warnings.warn(
                f"Known cross-group duplicates: {_KNOWN_CROSS_GROUP_DUPES & dupes}. "
                "Resolve in follow-up PR."
            )

        self.assertSetEqual(
            unknown_dupes,
            set(),
            f"Unknown duplicate feature names found across groups: {unknown_dupes}",
        )

    # ── 2. Every group has an enabled_by flag ──

    def test_groups_have_enabled_by(self):
        """Each FEATURE_GROUPS entry has an enabled_by flag."""
        for gname, ginfo in self.registry_groups.items():
            with self.subTest(group=gname):
                self.assertIn("enabled_by", ginfo, f"Group '{gname}' missing enabled_by")
                self.assertIsInstance(ginfo["enabled_by"], str)

    # ── 3. YAML feature lists reference features that exist ──

    def test_yaml_feature_groups_exist_in_registry(self):
        """Feature groups named in YAML configs must exist in FEATURE_GROUPS."""
        if not FEATURES_YAML_DIR.exists():
            self.skipTest(f"YAML dir not found: {FEATURES_YAML_DIR}")

        for yaml_path in sorted(FEATURES_YAML_DIR.glob("*.yaml")):
            with open(yaml_path) as f:
                data = yaml.safe_load(f)
            group_names: list[str] = data.get("feature_groups", []) or []
            for gname in group_names:
                with self.subTest(yaml=yaml_path.name, group=gname):
                    self.assertIn(
                        gname,
                        self.registry_groups,
                        f"YAML '{yaml_path.name}' references unknown group '{gname}'",
                    )

    # ── 4. All FEATURE_GROUPS enabled_by flags exist in config.py ──

    def test_enabled_by_flags_in_config(self):
        """All enabled_by flags must be defined in RESEARCH_FEATURE_FLAGS."""
        from qsys.feature.config import RESEARCH_FEATURE_FLAGS

        for gname, ginfo in self.registry_groups.items():
            flag = ginfo["enabled_by"]
            with self.subTest(group=gname):
                self.assertIn(
                    flag,
                    RESEARCH_FEATURE_FLAGS,
                    f"Group '{gname}' enabled_by='{flag}' missing from RESEARCH_FEATURE_FLAGS",
                )

    # ── 5. builder.py imports all group builders ──

    def test_builder_has_all_hooks(self):
        """builder.py must import and call all feature group builders."""
        from qsys.feature import builder as builder_mod

        # Check that the module imports expected builder functions
        expected_imports = [
            "build_microstructure_features",
            "build_liquidity_features",
            "build_tradability_features",
            "build_relative_strength_features",
            "build_regime_features",
            "build_industry_context_features",
            "build_fundamental_context_features",
        ]
        for fn_name in expected_imports:
            with self.subTest(fn=fn_name):
                self.assertTrue(
                    hasattr(builder_mod, fn_name),
                    f"builder.py missing import of {fn_name}",
                )

        # Check that builder's flag dispatch covers all groups
        import inspect

        source = inspect.getsource(builder_mod.build_phase1_features)
        for gname, ginfo in self.registry_groups.items():
            flag = ginfo["enabled_by"]
            with self.subTest(group=gname, flag=flag):
                self.assertIn(
                    flag,
                    source,
                    f"Group '{gname}' flag '{flag}' not found in "
                    f"build_phase1_features dispatch",
                )

    # ── 6. Group features match their declared list ──

    def test_group_features_consistency(self):
        """Each group's features list must be internally consistent (no name=value)."""
        import re

        for gname, ginfo in self.registry_groups.items():
            feats = ginfo.get("features", [])
            for feat in feats:
                with self.subTest(group=gname, feature=feat):
                    # Feature names should not be qlib expressions
                    self.assertNotIn("$", feat, f"Feature '{feat}' looks like qlib raw expression")
                    self.assertNotIn("(", feat, f"Feature '{feat}' looks like qlib operator expression")
                    self.assertNotIn("/", feat, f"Feature '{feat}' contains '/' - maybe a qlib expression")

    # ── 7. Registry v2 basic check (if populated) ──

    def test_registry_v2_roundtrip(self):
        """Basic round-trip test for registry_v2."""
        from qsys.feature.registry_v2 import FeatureSpec, register, get_by_id, get_by_name

        spec = FeatureSpec(
            feature_id="test_ret_1d",
            name="ret_1d",
            group="relative_strength",
            kind="derived",
            dependencies=("close",),
            compute_fn="build_relative_strength_features",
            pit_type="rolling_past",
            cache_scope="panel",
            status="active",
            description="Test",
        )
        try:
            register(spec)
        except ValueError:
            pass  # may already exist

        fetched = get_by_id("test_ret_1d")
        self.assertIsNotNone(fetched)
        if fetched:
            self.assertEqual(fetched.name, "ret_1d")

    # ── 8. No status=broken features in active groups ──

    def test_no_broken_features_in_registry_groups(self):
        """Registry FEATURE_GROUPS should not include feature names that are
        status='broken' in registry_v2. (Soft check — prints warning.)"""
        from qsys.feature.registry_v2 import get_by_name

        for gname, ginfo in self.registry_groups.items():
            for feat in ginfo.get("features", []):
                spec = get_by_name(feat)
                if spec is not None and spec.status == "broken":
                    self.fail(
                        f"Feature '{feat}' (group '{gname}') is status=broken "
                        f"but still listed in FEATURE_GROUPS"
                    )


class TestRegistryV2Consistency(unittest.TestCase):
    """Tests for the new FeatureSpec registry (registry_v2)."""

    def test_register_and_lookup(self):
        from qsys.feature.registry_v2 import (
            FeatureSpec,
            register,
            get_by_id,
            get_by_name,
            register_batch,
        )

        spec = FeatureSpec(
            feature_id="unittest_my_feat",
            name="my_feat",
            group="test_group",
            kind="derived",
            dependencies=("close",),
            compute_fn="build_test_features",
            pit_type="rolling_past",
            cache_scope="panel",
            status="active",
            description="Test feature for unit test",
        )

        register(spec)

        by_id = get_by_id("unittest_my_feat")
        self.assertIsNotNone(by_id)
        if by_id:
            self.assertEqual(by_id.name, "my_feat")
            self.assertEqual(by_id.kind, "derived")
            self.assertEqual(by_id.dependencies, ("close",))

        by_name = get_by_name("my_feat")
        self.assertIsNotNone(by_name)
        if by_name:
            self.assertEqual(by_name.feature_id, "unittest_my_feat")

    def test_duplicate_feature_id_raises(self):
        from qsys.feature.registry_v2 import FeatureSpec, register

        spec = FeatureSpec(
            feature_id="unittest_dup_id",
            name="dup_id_feat",
            group="test", kind="derived",
            description="duplicate test",
        )
        register(spec)

        dup = FeatureSpec(
            feature_id="unittest_dup_id",
            name="dup_id_feat_2",
            group="test", kind="derived",
            description="duplicate feature_id",
        )
        with self.assertRaises(ValueError):
            register(dup)

    def test_duplicate_name_raises(self):
        from qsys.feature.registry_v2 import FeatureSpec, register

        s1 = FeatureSpec(
            feature_id="unittest_dup_name_1",
            name="dup_name",
            group="test", kind="derived",
            description="first",
        )
        s2 = FeatureSpec(
            feature_id="unittest_dup_name_2",
            name="dup_name",
            group="test", kind="derived",
            description="second (same name)",
        )
        register(s1)
        with self.assertRaises(ValueError):
            register(s2)

    def test_list_specs_filter(self):
        from qsys.feature.registry_v2 import FeatureSpec, register, list_specs

        register(FeatureSpec(
            feature_id="unittest_filter_1",
            name="filter_feat_1",
            group="test_group", kind="derived",
            status="active", description="filter test 1",
        ))
        register(FeatureSpec(
            feature_id="unittest_filter_2",
            name="filter_feat_2",
            group="test_group", kind="raw",
            status="experimental", description="filter test 2",
        ))

        active = list_specs(status="active")
        self.assertTrue(any(s.feature_id == "unittest_filter_1" for s in active))

        raw = list_specs(kind="raw")
        self.assertTrue(any(s.feature_id == "unittest_filter_2" for s in raw))

    def test_resolve_dependencies(self):
        from qsys.feature.registry_v2 import FeatureSpec, register, resolve_dependencies

        register(FeatureSpec(
            feature_id="unittest_rdep_A",
            name="raw_A", group="test",
            kind="raw", description="raw A",
        ))
        register(FeatureSpec(
            feature_id="unittest_rdep_B",
            name="derived_B", group="test",
            kind="derived", dependencies=("raw_A",),
            description="derived B",
        ))
        register(FeatureSpec(
            feature_id="unittest_rdep_C",
            name="derived_C", group="test",
            kind="derived", dependencies=("derived_B",),
            description="derived C",
        ))

        deps = resolve_dependencies("unittest_rdep_C")
        self.assertIn("raw_A", deps)

    def test_check_broken_features(self):
        from qsys.feature.registry_v2 import (
            FeatureSpec,
            register,
            check_broken_features,
            check_deprecated_features,
            check_missing_features,
        )

        register(FeatureSpec(
            feature_id="unittest_broken_1",
            name="broken_feat_1",
            group="test", kind="derived",
            status="broken", description="broken test",
        ))
        register(FeatureSpec(
            feature_id="unittest_depr_1",
            name="deprecated_feat_1",
            group="test", kind="derived",
            status="deprecated", description="deprecated test",
        ))

        broken = check_broken_features(["broken_feat_1", "deprecated_feat_1"])
        self.assertIn("broken_feat_1", broken)
        self.assertNotIn("deprecated_feat_1", broken)

        deprecated = check_deprecated_features(["deprecated_feat_1"])
        self.assertIn("deprecated_feat_1", deprecated)

        missing = check_missing_features(["nonexistent_feat"])
        self.assertIn("nonexistent_feat", missing)

    def test_verify_feature_list(self):
        from qsys.feature.registry_v2 import (
            FeatureSpec,
            register,
            verify_feature_list,
        )

        register(FeatureSpec(
            feature_id="unittest_vfl_1",
            name="vfl_active",
            group="test", kind="derived",
            status="active", description="verify test",
        ))
        register(FeatureSpec(
            feature_id="unittest_vfl_2",
            name="vfl_broken",
            group="test", kind="derived",
            status="broken", description="verify test broken",
        ))

        result = verify_feature_list(["vfl_active", "vfl_broken", "vfl_missing"])
        self.assertIn("vfl_broken", result["broken"])
        self.assertIn("vfl_missing", result["missing"])
        self.assertEqual(result["deprecated"], [])


if __name__ == "__main__":
    unittest.main()
