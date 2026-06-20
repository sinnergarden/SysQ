"""Tests for feature cache — key stability, paths, metadata, manifest integration.

Key checks:
1. Transform cache key stability and sensitivity
2. Matrix cache key stability and sensitivity
3. Cache path formatting
4. Metadata round-trip
5. Manifest cache section
6. CLI smoke test
"""

import json
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


class TestTransformCacheKey(unittest.TestCase):
    """Transform-level cache key stability and sensitivity."""

    def setUp(self):
        from qsys.feature.cache import FeatureCacheContext

        self.base_ctx = FeatureCacheContext(
            feature_set_id="test_fs",
            date_start="2018-01-01",
            date_end="2025-12-31",
            universe="csi800",
            source_manifest_hash="src_v1",
            builder_hash="builder_v1",
            pit_policy_hash="pit_v1",
        )

    def _key(self, transform_id="build_test", input_features=None,
             output_features=None, compute_fn_hash="fn_v1", context=None):
        from qsys.feature.cache import compute_transform_cache_key

        return compute_transform_cache_key(
            transform_id,
            input_features=input_features or ["close"],
            output_features=output_features or ["ret_60d"],
            compute_fn_hash=compute_fn_hash,
            context=context or self.base_ctx,
        )

    # ── 1. Same inputs → same key ──
    def test_same_inputs_same_key(self):
        k1 = self._key()
        k2 = self._key()
        self.assertEqual(k1.key, k2.key)

    # ── 2. transform_id changes → key changes ──
    def test_transform_id_changes_key(self):
        k1 = self._key(transform_id="build_A")
        k2 = self._key(transform_id="build_B")
        self.assertNotEqual(k1.key, k2.key)

    # ── 3. compute_fn_hash changes → key changes ──
    def test_compute_fn_hash_changes_key(self):
        k1 = self._key(compute_fn_hash="fn_v1")
        k2 = self._key(compute_fn_hash="fn_v2")
        self.assertNotEqual(k1.key, k2.key)

    # ── 4. source_manifest_hash changes → key changes ──
    def test_source_manifest_hash_changes_key(self):
        from qsys.feature.cache import FeatureCacheContext

        ctx2 = FeatureCacheContext(
            feature_set_id="test_fs",
            date_start="2018-01-01",
            date_end="2025-12-31",
            universe="csi800",
            source_manifest_hash="src_v2",
        )
        k1 = self._key()
        k2 = self._key(context=ctx2)
        self.assertNotEqual(k1.key, k2.key)

    # ── 5. date range changes → key changes ──
    def test_date_range_changes_key(self):
        from qsys.feature.cache import FeatureCacheContext

        ctx2 = FeatureCacheContext(
            feature_set_id="test_fs",
            date_start="2020-01-01",
            date_end="2024-12-31",
            universe="csi800",
            source_manifest_hash="src_v1",
        )
        k1 = self._key()
        k2 = self._key(context=ctx2)
        self.assertNotEqual(k1.key, k2.key)

    # ── 6. universe changes → key changes ──
    def test_universe_changes_key(self):
        from qsys.feature.cache import FeatureCacheContext

        ctx2 = FeatureCacheContext(
            feature_set_id="test_fs",
            date_start="2018-01-01",
            date_end="2025-12-31",
            universe="csi300",
            source_manifest_hash="src_v1",
        )
        k1 = self._key()
        k2 = self._key(context=ctx2)
        self.assertNotEqual(k1.key, k2.key)

    # ── 7. output_features changes → key changes ──
    def test_output_features_changes_key(self):
        k1 = self._key(output_features=["ret_60d"])
        k2 = self._key(output_features=["ret_120d"])
        self.assertNotEqual(k1.key, k2.key)

    # ── 8. object type ──
    def test_cache_key_dataclass(self):
        k = self._key()
        self.assertEqual(k.kind, "transform")
        self.assertIsInstance(k.key, str)
        self.assertEqual(len(k.key), 20)
        self.assertIn("transform_id", k.parts)


class TestMatrixCacheKey(unittest.TestCase):
    """Matrix-level cache key stability and sensitivity."""

    def setUp(self):
        from qsys.feature.cache import FeatureCacheContext

        self.base_ctx = FeatureCacheContext(
            feature_set_id="test_fs",
            date_start="2018-01-01",
            date_end="2025-12-31",
            universe="csi800",
            source_manifest_hash="src_v1",
            builder_hash="builder_v1",
        )

    def _key(self, feature_set_id="test_fs", resolved_features=None,
             required_transforms=None, context=None):
        from qsys.feature.cache import compute_matrix_cache_key

        return compute_matrix_cache_key(
            feature_set_id,
            resolved_features=resolved_features or ["ret_60d", "close"],
            required_transforms=required_transforms or ["build_relative_strength"],
            context=context or self.base_ctx,
        )

    # ── 1. Same inputs → same key ──
    def test_same_inputs_same_key(self):
        k1 = self._key()
        k2 = self._key()
        self.assertEqual(k1.key, k2.key)

    # ── 2. Order changes → key changes ──
    def test_feature_order_changes_key(self):
        k1 = self._key(resolved_features=["ret_60d", "close"])
        k2 = self._key(resolved_features=["close", "ret_60d"])
        self.assertNotEqual(k1.key, k2.key)

    # ── 3. New feature added → key changes ──
    def test_new_feature_changes_key(self):
        k1 = self._key(resolved_features=["ret_60d"])
        k2 = self._key(resolved_features=["ret_60d", "close"])
        self.assertNotEqual(k1.key, k2.key)

    # ── 4. required_transforms changes → key changes ──
    def test_transforms_changes_key(self):
        k1 = self._key(required_transforms=["build_margin"])
        k2 = self._key(required_transforms=["build_relative_strength"])
        self.assertNotEqual(k1.key, k2.key)

    # ── 5. builder_hash changes → key changes ──
    def test_builder_hash_changes_key(self):
        from qsys.feature.cache import FeatureCacheContext

        ctx2 = FeatureCacheContext(
            feature_set_id="test_fs",
            date_start="2018-01-01",
            date_end="2025-12-31",
            universe="csi800",
            source_manifest_hash="src_v1",
            builder_hash="builder_v2",
        )
        k1 = self._key()
        k2 = self._key(context=ctx2)
        self.assertNotEqual(k1.key, k2.key)


class TestCachePath(unittest.TestCase):
    """Cache path formatting."""

    # ── 1. Transform path ──
    def test_transform_cache_path(self):
        from qsys.feature.cache import transform_cache_path

        p = transform_cache_path("build_margin", "abc123", root="/tmp/cache")
        self.assertEqual(p, Path("/tmp/cache/transforms/build_margin/abc123.parquet"))

    # ── 2. Matrix path ──
    def test_matrix_cache_path(self):
        from qsys.feature.cache import matrix_cache_path

        p = matrix_cache_path("my_feature_set", "def456", root="/tmp/cache")
        self.assertEqual(p, Path("/tmp/cache/matrices/my_feature_set/def456.parquet"))

    # ── 3. Default root ──
    def test_default_root(self):
        from qsys.feature.cache import transform_cache_path

        p = transform_cache_path("build_test", "k")
        self.assertEqual(p, Path("data/feature_cache/transforms/build_test/k.parquet"))

    # ── 4. No illegal characters ──
    def test_path_no_illegal_chars(self):
        from qsys.feature.cache import transform_cache_path

        p = transform_cache_path("build_relative_strength_features", "a1b2c3d4e5f6g7h8i9j0")
        for part in p.parts:
            # No characters that are problematic across OS
            self.assertFalse(part.startswith("."), f"Hidden file path part: {part}")


class TestCacheMetadata(unittest.TestCase):
    """Cache metadata I/O round-trip."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ── 1. Round-trip ──
    def test_metadata_roundtrip(self):
        from qsys.feature.cache import write_cache_metadata, read_cache_metadata

        # Write to a .parquet fake path
        fake_parquet = self.tmpdir / "test.parquet"
        metadata = {
            "cache_key": "abc123",
            "kind": "transform",
            "context": {"universe": "csi800"},
        }
        write_cache_metadata(fake_parquet, metadata)

        meta_path = Path(str(fake_parquet) + ".meta.json")
        self.assertTrue(meta_path.exists())

        loaded = read_cache_metadata(fake_parquet)
        self.assertEqual(loaded["cache_key"], "abc123")
        self.assertEqual(loaded["kind"], "transform")
        self.assertEqual(loaded["context"]["universe"], "csi800")
        self.assertEqual(loaded["_version"], 1)

    # ── 2. Missing file returns empty dict ──
    def test_metadata_missing(self):
        from qsys.feature.cache import read_cache_metadata

        fake = self.tmpdir / "nonexistent.parquet"
        loaded = read_cache_metadata(fake)
        self.assertEqual(loaded, {})

    # ── 3. Metadata has created_at ──
    def test_metadata_has_created_at(self):
        from qsys.feature.cache import write_cache_metadata, read_cache_metadata

        fake_parquet = self.tmpdir / "test2.parquet"
        write_cache_metadata(fake_parquet, {"cache_key": "x", "kind": "matrix"})
        loaded = read_cache_metadata(fake_parquet)
        self.assertIn("created_at", loaded)

    # ── 4. Metadata kind preservation ──
    def test_metadata_kind(self):
        from qsys.feature.cache import write_cache_metadata, read_cache_metadata

        for kind in ("transform", "matrix"):
            fp = self.tmpdir / f"test_{kind}.parquet"
            write_cache_metadata(fp, {"cache_key": kind, "kind": kind})
            loaded = read_cache_metadata(fp)
            self.assertEqual(loaded["kind"], kind)


class TestManifestCacheSection(unittest.TestCase):
    """Manifest cache section integration."""

    def test_no_cache_info_compat(self):
        """Manifest without cache_info must not have a 'cache' key."""
        from qsys.feature.resolver_v2 import discover_feature_sets, resolve_feature_set
        from qsys.feature.build_plan import build_plan_from_resolved
        from qsys.feature.manifest import build_feature_manifest

        discover_feature_sets(config_dir=str(REPO / "configs" / "features"))
        yamls = list((REPO / "configs" / "features").glob("*.yaml"))
        resolved = resolve_feature_set(str(yamls[0]))
        plan = build_plan_from_resolved(resolved)
        manifest = build_feature_manifest(resolved, plan)
        self.assertNotIn("cache", manifest)

    def test_cache_info_added(self):
        """With cache_info, manifest must include cache section."""
        from qsys.feature.resolver_v2 import discover_feature_sets, resolve_feature_set
        from qsys.feature.build_plan import build_plan_from_resolved
        from qsys.feature.manifest import build_feature_manifest

        discover_feature_sets(config_dir=str(REPO / "configs" / "features"))
        yamls = list((REPO / "configs" / "features").glob("*.yaml"))
        resolved = resolve_feature_set(str(yamls[0]))
        plan = build_plan_from_resolved(resolved)
        cache_info = {
            "enabled": True,
            "matrix_cache_key": "m1",
            "transform_cache_keys": {"build_relative_strength": "t1"},
        }
        manifest = build_feature_manifest(resolved, plan, cache_info=cache_info)
        self.assertIn("cache", manifest)
        self.assertEqual(manifest["cache"]["enabled"], True)
        self.assertEqual(manifest["cache"]["matrix_cache_key"], "m1")
        self.assertIn("transform_cache_keys", manifest["cache"])

    def test_transform_keys_in_manifest(self):
        """Transform cache keys must be present when provided."""
        from qsys.feature.resolver_v2 import discover_feature_sets, resolve_feature_set
        from qsys.feature.build_plan import build_plan_from_resolved
        from qsys.feature.manifest import build_feature_manifest

        discover_feature_sets(config_dir=str(REPO / "configs" / "features"))
        yamls = list((REPO / "configs" / "features").glob("*.yaml"))
        resolved = resolve_feature_set(str(yamls[0]))
        plan = build_plan_from_resolved(resolved)
        cache_info = {
            "enabled": True,
            "matrix_cache_key": "m1",
            "transform_cache_keys": {
                "build_relative_strength": "t1",
                "build_margin": "t2",
            },
        }
        manifest = build_feature_manifest(resolved, plan, cache_info=cache_info)
        self.assertEqual(
            manifest["cache"]["transform_cache_keys"]["build_relative_strength"],
            "t1",
        )
        self.assertEqual(
            manifest["cache"]["transform_cache_keys"]["build_margin"],
            "t2",
        )


class TestCacheCli(unittest.TestCase):
    """CLI smoke test for plan_feature_cache.py."""

    def test_cli_runs_on_legacy_yaml(self):
        """CLI should produce a cache plan for a legacy YAML."""
        import subprocess

        yaml_path = str(REPO / "configs" / "features" / "momentum_price_volume_v1.yaml")
        tmpdir = Path(tempfile.mkdtemp())
        try:
            result = subprocess.run(
                [
                    "python", str(REPO / "scripts" / "dev" / "plan_feature_cache.py"),
                    "--feature-set", yaml_path,
                    "--source-manifest-hash", "test_src",
                    "--date-start", "2020-01-01",
                    "--date-end", "2024-12-31",
                    "--universe", "csi800",
                    "--output-dir", str(tmpdir),
                ],
                capture_output=True, text=True, cwd=str(REPO),
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, f"CLI failed: {result.stderr}")

            # Check plan file
            plan_files = list(tmpdir.glob("*.json"))
            self.assertGreater(len(plan_files), 0)

            with open(plan_files[0]) as f:
                plan = json.load(f)
            self.assertIn("matrix_cache_key", plan)
            self.assertIn("transform_cache_keys", plan)
            self.assertIsInstance(plan["matrix_cache_key"], str)
            self.assertGreater(len(plan["matrix_cache_key"]), 0)
            self.assertIn("transform_cache_paths", plan)

            # No parquet files should be created
            parquet_files = list(tmpdir.rglob("*.parquet"))
            self.assertEqual(len(parquet_files), 0, "CLI must not write parquet files")
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
