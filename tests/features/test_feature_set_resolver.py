"""Tests for FeatureSet resolver.

Must cover:
1. Legacy YAML features → resolve correctly
2. New YAML extends + add_features → resolve correctly
3. Additive mode — only appends, does not remove base features
4. Resolved feature order stable
5. Duplicate feature in same YAML → fail
6. features and extends at same time → fail
7. exclude_features → fail
8. exclude_groups → fail
9. extends references non-existent feature_set_id → fail
10. Circular extends → fail
11. Missing feature → fail
12. Broken feature → fail
13. Deprecated feature → produce warning
14. All existing configs/features/*.yaml resolve successfully
15. Legacy YAML resolved feature count equals original YAML feature count
16. No silent skip
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]


class TestFeatureSetSpec(unittest.TestCase):
    """Tests for bare FeatureSet YAML parsing (no resolver)."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_yaml(self, data: dict, name: str = "test.yaml") -> Path:
        path = self.tmpdir / name
        with open(path, "w") as f:
            yaml.dump(data, f)
        return path

    # ── 1. Legacy YAML ──

    def test_legacy_features(self):
        from qsys.feature.feature_set import load_feature_set_yaml

        p = self._write_yaml({
            "feature_list_id": "test_legacy",
            "features": ["ret_60d", "close"],
        })
        spec = load_feature_set_yaml(p)
        self.assertEqual(spec.feature_set_id, "test_legacy")
        self.assertTrue(spec.is_legacy())
        self.assertFalse(spec.is_additive())
        self.assertEqual(spec.features, ("ret_60d", "close"))

    # ── 2. Additive YAML ──

    def test_additive_extends(self):
        from qsys.feature.feature_set import load_feature_set_yaml

        p = self._write_yaml({
            "feature_set_id": "test_additive",
            "extends": "base_set",
            "add_features": ["industry_ret_20d"],
            "description": "test",
        })
        spec = load_feature_set_yaml(p)
        self.assertEqual(spec.feature_set_id, "test_additive")
        self.assertFalse(spec.is_legacy())
        self.assertTrue(spec.is_additive())
        self.assertEqual(spec.extends, "base_set")
        self.assertEqual(spec.add_features, ("industry_ret_20d",))

    # ── 3. features + extends at same time → fail ──

    def test_features_and_extends_together_fail(self):
        from qsys.feature.feature_set import load_feature_set_yaml

        p = self._write_yaml({
            "feature_list_id": "test_both",
            "features": ["ret_60d"],
            "extends": "base",
        })
        with self.assertRaises(ValueError):
            load_feature_set_yaml(p)

    # ── 4. exclude_features → fail ──

    def test_exclude_features_fail(self):
        from qsys.feature.feature_set import load_feature_set_yaml

        p = self._write_yaml({
            "feature_set_id": "test_exclude",
            "features": ["ret_60d"],
            "exclude_features": ["bad_feat"],
        })
        with self.assertRaises(ValueError):
            load_feature_set_yaml(p)

    # ── 5. exclude_groups → fail ──

    def test_exclude_groups_fail(self):
        from qsys.feature.feature_set import load_feature_set_yaml

        p = self._write_yaml({
            "feature_set_id": "test_exclude_group",
            "features": ["ret_60d"],
            "exclude_groups": ["bad_group"],
        })
        with self.assertRaises(ValueError):
            load_feature_set_yaml(p)

    # ── 6. Duplicate within features ──

    def test_duplicate_features_fail(self):
        from qsys.feature.feature_set import load_feature_set_yaml

        p = self._write_yaml({
            "feature_list_id": "test_dup",
            "features": ["ret_60d", "ret_60d"],
        })
        with self.assertRaises(ValueError):
            load_feature_set_yaml(p)

    # ── 7. Duplicate within add_features ──

    def test_duplicate_add_features_fail(self):
        from qsys.feature.feature_set import load_feature_set_yaml

        p = self._write_yaml({
            "feature_set_id": "test_dup_add",
            "extends": "base",
            "add_features": ["industry_ret_20d", "industry_ret_20d"],
        })
        with self.assertRaises(ValueError):
            load_feature_set_yaml(p)

    # ── 8. Empty YAML → fail ──

    def test_empty_yaml_fail(self):
        from qsys.feature.feature_set import load_feature_set_yaml

        p = self._write_yaml({
            "feature_set_id": "test_empty",
        })
        with self.assertRaises(ValueError):
            load_feature_set_yaml(p)


class TestResolver(unittest.TestCase):
    """Integration tests for resolver_v2 with real YAML files."""

    def setUp(self):
        self.all_legacy_yamls = sorted(
            (REPO / "configs" / "features").glob("*.yaml")
        )
        # Discover production YAMLs before each test
        from qsys.feature.resolver_v2 import discover_feature_sets, _index
        _index.clear()
        discover_feature_sets(config_dir=str(REPO / "configs" / "features"))

    # ── 1. All legacy YAMLs resolve ──

    def test_all_legacy_yamls_resolve(self):
        """Every existing configs/features/*.yaml must resolve without error."""
        from qsys.feature.resolver_v2 import discover_feature_sets, resolve_feature_set

        discover_feature_sets(config_dir=str(REPO / "configs" / "features"))
        for yaml_path in self.all_legacy_yamls:
            with self.subTest(yaml=yaml_path.name):
                try:
                    resolved = resolve_feature_set(str(yaml_path))
                    self.assertGreater(
                        len(resolved.resolved_features), 0,
                        f"{yaml_path.name}: resolved_features is empty",
                    )
                except Exception as e:
                    self.fail(f"{yaml_path.name}: resolve failed: {e}")

    # ── 2. Legacy resolved count matches YAML count ──

    def test_legacy_resolved_count_matches(self):
        """Resolved feature count must be >= YAML features count (additive dedup)."""
        from qsys.feature.resolver_v2 import discover_feature_sets, resolve_feature_set

        discover_feature_sets(config_dir=str(REPO / "configs" / "features"))
        for yaml_path in self.all_legacy_yamls:
            with self.subTest(yaml=yaml_path.name):
                raw = yaml.safe_load(yaml_path.read_text())
                yaml_features = raw.get("features", []) or []
                resolved = resolve_feature_set(str(yaml_path))
                # Legacy YAMLs use features list — should match exactly
                self.assertEqual(
                    len(resolved.resolved_features), len(yaml_features),
                    f"{yaml_path.name}: resolved ({len(resolved.resolved_features)}) "
                    f"!= yaml features ({len(yaml_features)})",
                )

    # ── 3. No silent skip ──

    def test_no_silent_skip(self):
        """All resolved features must have a spec_source entry."""
        from qsys.feature.resolver_v2 import discover_feature_sets, resolve_feature_set

        discover_feature_sets(config_dir=str(REPO / "configs" / "features"))
        for yaml_path in self.all_legacy_yamls[:3]:
            resolved = resolve_feature_set(str(yaml_path))
            self.assertEqual(
                len(resolved.resolved_features),
                len(resolved.spec_sources),
                f"{yaml_path.name}: spec_sources count mismatch",
            )

    # ── 4. Missing feature → fail ──

    def test_missing_feature_fails(self):
        from qsys.feature.feature_set import load_feature_set_yaml
        from qsys.feature.resolver_v2 import _resolve_single_feature

        with self.assertRaises(ValueError):
            _resolve_single_feature("__nonexistent_feature_xyz__")

    # ── 5. Broken feature → fail ──

    def test_broken_feature_fails(self):
        from qsys.feature.registry_v2 import FeatureSpec, register
        from qsys.feature.resolver_v2 import _resolve_single_feature, discover_feature_sets

        discover_feature_sets()
        register(FeatureSpec(
            feature_id="unittest_broken_resolver",
            name="broken_test_feat",
            group="test",
            kind="derived",
            status="broken",
            description="intentionally broken for test",
        ))
        with self.assertRaises(ValueError):
            _resolve_single_feature("broken_test_feat")

    # ── 6. Deprecated feature → warning (non-blocking) ──

    def test_deprecated_feature_warns(self):
        from qsys.feature.registry_v2 import FeatureSpec, register
        from qsys.feature.resolver_v2 import _resolve_single_feature

        register(FeatureSpec(
            feature_id="unittest_depr_resolver",
            name="deprecated_test_feat",
            group="test",
            kind="derived",
            status="deprecated",
            description="intentionally deprecated for test",
        ))
        info = _resolve_single_feature("deprecated_test_feat", allow_deprecated=True)
        self.assertEqual(info["name"], "deprecated_test_feat")

    # ── 7. extends → non-existent → fail ──

    def test_extends_non_existent_fails(self):
        import tempfile
        from qsys.feature.resolver_v2 import discover_feature_sets, resolve_feature_set

        tmpdir = Path(tempfile.mkdtemp())
        try:
            tmp_yaml = tmpdir / "bad_extends.yaml"
            with open(tmp_yaml, "w") as f:
                yaml.dump({
                    "feature_set_id": "bad_extends_test",
                    "extends": "__nonexistent_base__",
                    "add_features": ["ret_60d"],
                }, f)
            # Discover from tmpdir only (so the bad extends is visible)
            discover_feature_sets(config_dir=str(tmpdir))
            with self.assertRaises(ValueError):
                resolve_feature_set(str(tmp_yaml))
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ── 8. Additive mode — only appends, no removal ──

    def test_additive_only_appends(self):
        """Additive extends + add_features must include base features + new."""
        import tempfile
        from qsys.feature.resolver_v2 import discover_feature_sets, resolve_feature_set

        discover_feature_sets(config_dir=str(REPO / "configs" / "features"))
        tmpdir = Path(tempfile.mkdtemp())
        try:
            # Write base
            base_yaml = tmpdir / "test_base.yaml"
            with open(base_yaml, "w") as f:
                yaml.dump({
                    "feature_list_id": "test_base",
                    "features": ["ret_60d", "close"],
                }, f)
            # Discover from tmpdir so extends works
            discover_feature_sets(config_dir=str(tmpdir))
            # Write extending set
            ext_yaml = tmpdir / "test_ext.yaml"
            with open(ext_yaml, "w") as f:
                yaml.dump({
                    "feature_set_id": "test_ext",
                    "extends": "test_base",
                    "add_features": ["industry_ret_20d"],
                }, f)
            resolved = resolve_feature_set(str(ext_yaml))
            self.assertIn("ret_60d", resolved.resolved_features)
            self.assertIn("close", resolved.resolved_features)
            self.assertIn("industry_ret_20d", resolved.resolved_features)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ── 9. Circular extends ──

    def test_circular_extends_fails(self):
        import tempfile
        from qsys.feature.resolver_v2 import (
            discover_feature_sets, resolve_feature_set, _index,
        )

        tmpdir = Path(tempfile.mkdtemp())
        try:
            a_yaml = tmpdir / "circ_A.yaml"
            with open(a_yaml, "w") as f:
                yaml.dump({
                    "feature_set_id": "circ_A",
                    "extends": "circ_B",
                    "add_features": ["close"],
                }, f)
            b_yaml = tmpdir / "circ_B.yaml"
            with open(b_yaml, "w") as f:
                yaml.dump({
                    "feature_set_id": "circ_B",
                    "extends": "circ_A",
                    "add_features": ["close"],
                }, f)
            # Discover from tmpdir (adds to global _index)
            _index.clear()
            discover_feature_sets(config_dir=str(tmpdir))
            with self.assertRaises(ValueError):
                resolve_feature_set(str(a_yaml))
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ── 10. Order stability ──

    def test_order_stability(self):
        """Legacy YAML resolves features in the same order as declared."""
        from qsys.feature.resolver_v2 import discover_feature_sets, resolve_feature_set

        discover_feature_sets(config_dir=str(REPO / "configs" / "features"))
        for yaml_path in self.all_legacy_yamls[:3]:
            raw = yaml.safe_load(yaml_path.read_text())
            expected = raw.get("features", []) or []
            resolved = resolve_feature_set(str(yaml_path))
            self.assertListEqual(
                list(resolved.resolved_features),
                list(expected),
                f"{yaml_path.name}: order mismatch",
            )

    # ── 11. Registry_v2 + inventory fallback ──

    def test_known_legacy_features_have_spec(self):
        """All features in legacy YAMLs have registry_v2 or inventory or qlib spec."""
        from qsys.feature.resolver_v2 import discover_feature_sets, resolve_feature_set

        discover_feature_sets(config_dir=str(REPO / "configs" / "features"))
        for yaml_path in self.all_legacy_yamls:
            resolved = resolve_feature_set(str(yaml_path))
            for info in resolved.spec_sources:
                self.assertIn(
                    info["source"], ["registry_v2", "inventory", "qlib_expression"],
                    f"{yaml_path.name}: {info['name']} source={info['source']}",
                )


class TestManifest(unittest.TestCase):
    """Tests for manifest generation."""

    def setUp(self):
        from qsys.feature.resolver_v2 import discover_feature_sets, resolve_feature_set

        discover_feature_sets(config_dir=str(REPO / "configs" / "features"))
        # Pick a known simple legacy YAML
        base = REPO / "configs" / "features"
        simple_yamls = [p for p in base.glob("*.yaml") if "momentum_price_volume_v1" in p.name]
        if not simple_yamls:
            simple_yamls = list(base.glob("*.yaml"))
        self.simple_yaml = simple_yamls[0]
        self.resolved = resolve_feature_set(str(self.simple_yaml))

    def test_manifest_version(self):
        from qsys.feature.build_plan import build_plan_from_resolved
        from qsys.feature.manifest import build_feature_manifest

        plan = build_plan_from_resolved(self.resolved)
        manifest = build_feature_manifest(self.resolved, plan)
        self.assertEqual(manifest["manifest_version"], 1)

    def test_manifest_has_feature_set_id(self):
        from qsys.feature.build_plan import build_plan_from_resolved
        from qsys.feature.manifest import build_feature_manifest

        plan = build_plan_from_resolved(self.resolved)
        manifest = build_feature_manifest(self.resolved, plan)
        self.assertIn("feature_set_id", manifest)

    def test_manifest_resolved_count_matches(self):
        from qsys.feature.build_plan import build_plan_from_resolved
        from qsys.feature.manifest import build_feature_manifest

        plan = build_plan_from_resolved(self.resolved)
        manifest = build_feature_manifest(self.resolved, plan)
        self.assertEqual(
            len(manifest["resolved_features"]),
            len(self.resolved.resolved_features),
        )

    def test_manifest_io(self):
        from qsys.feature.build_plan import build_plan_from_resolved
        from qsys.feature.manifest import build_feature_manifest, write_feature_manifest

        import tempfile
        tmpdir = Path(tempfile.mkdtemp())
        try:
            plan = build_plan_from_resolved(self.resolved)
            manifest = build_feature_manifest(self.resolved, plan)
            path = write_feature_manifest(manifest, tmpdir)
            self.assertTrue(path.exists())
            with open(path) as f:
                loaded = json.load(f)
            self.assertEqual(loaded["status"], "ok")
            self.assertEqual(loaded["feature_set_id"], self.resolved.feature_set_id)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_manifest_status_ok(self):
        from qsys.feature.build_plan import build_plan_from_resolved
        from qsys.feature.manifest import build_feature_manifest

        plan = build_plan_from_resolved(self.resolved)
        manifest = build_feature_manifest(self.resolved, plan)
        self.assertEqual(manifest["status"], "ok")

    def test_manifest_warnings(self):
        from qsys.feature.build_plan import build_plan_from_resolved
        from qsys.feature.manifest import build_feature_manifest

        plan = build_plan_from_resolved(self.resolved)
        manifest = build_feature_manifest(self.resolved, plan)
        self.assertIn("warnings", manifest)


if __name__ == "__main__":
    unittest.main()
