"""Tests for cache key stability, paths, and metadata (Phase 3 coverage)."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import pandas as pd

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

    def test_same_inputs_same_key(self):
        k1 = self._key()
        k2 = self._key()
        self.assertEqual(k1.key, k2.key)

    def test_transform_id_changes_key(self):
        k1 = self._key(transform_id="build_A")
        k2 = self._key(transform_id="build_B")
        self.assertNotEqual(k1.key, k2.key)

    def test_compute_fn_hash_changes_key(self):
        k1 = self._key(compute_fn_hash="fn_v1")
        k2 = self._key(compute_fn_hash="fn_v2")
        self.assertNotEqual(k1.key, k2.key)

    def test_source_manifest_hash_changes_key(self):
        from qsys.feature.cache import FeatureCacheContext
        ctx2 = FeatureCacheContext(
            feature_set_id="test_fs", date_start="2018-01-01", date_end="2025-12-31",
            universe="csi800", source_manifest_hash="src_v2",
        )
        k1 = self._key()
        k2 = self._key(context=ctx2)
        self.assertNotEqual(k1.key, k2.key)

    def test_date_range_changes_key(self):
        from qsys.feature.cache import FeatureCacheContext
        ctx2 = FeatureCacheContext(
            feature_set_id="test_fs", date_start="2020-01-01", date_end="2024-12-31",
            universe="csi800", source_manifest_hash="src_v1",
        )
        k1 = self._key()
        k2 = self._key(context=ctx2)
        self.assertNotEqual(k1.key, k2.key)

    def test_universe_changes_key(self):
        from qsys.feature.cache import FeatureCacheContext
        ctx2 = FeatureCacheContext(
            feature_set_id="test_fs", date_start="2018-01-01", date_end="2025-12-31",
            universe="csi300", source_manifest_hash="src_v1",
        )
        k1 = self._key()
        k2 = self._key(context=ctx2)
        self.assertNotEqual(k1.key, k2.key)

    def test_output_features_changes_key(self):
        k1 = self._key(output_features=["ret_60d"])
        k2 = self._key(output_features=["ret_120d"])
        self.assertNotEqual(k1.key, k2.key)


class TestMatrixCacheKey(unittest.TestCase):
    """Matrix-level cache key stability."""

    def setUp(self):
        from qsys.feature.cache import FeatureCacheContext
        self.base_ctx = FeatureCacheContext(
            feature_set_id="test_fs", date_start="2018-01-01", date_end="2025-12-31",
            universe="csi800", source_manifest_hash="src_v1", builder_hash="builder_v1",
        )

    def _key(self, resolved_features=None, required_transforms=None, ctx=None):
        from qsys.feature.cache import compute_matrix_cache_key
        return compute_matrix_cache_key(
            "test_fs",
            resolved_features=resolved_features or ["ret_60d", "close"],
            required_transforms=required_transforms or ["build_relative_strength"],
            context=ctx or self.base_ctx,
        )

    def test_same_inputs_same_key(self):
        self.assertEqual(self._key().key, self._key().key)

    def test_feature_order_changes_key(self):
        k1 = self._key(resolved_features=["ret_60d", "close"])
        k2 = self._key(resolved_features=["close", "ret_60d"])
        self.assertNotEqual(k1.key, k2.key)

    def test_new_feature_changes_key(self):
        k1 = self._key(resolved_features=["ret_60d"])
        k2 = self._key(resolved_features=["ret_60d", "close"])
        self.assertNotEqual(k1.key, k2.key)


class TestCachePath(unittest.TestCase):
    """Cache path formatting."""

    def test_transform_path(self):
        from qsys.feature.cache import transform_cache_path
        p = transform_cache_path("build_margin", "abc123", root="/tmp/c")
        self.assertEqual(p, Path("/tmp/c/transforms/build_margin/abc123.parquet"))

    def test_matrix_path(self):
        from qsys.feature.cache import matrix_cache_path
        p = matrix_cache_path("my_set", "def456", root="/tmp/c")
        self.assertEqual(p, Path("/tmp/c/matrices/my_set/def456.parquet"))


class TestCacheMetadata(unittest.TestCase):
    """Metadata round-trip."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_metadata_roundtrip(self):
        from qsys.feature.cache import write_cache_metadata, read_cache_metadata

        fake_pq = self.tmpdir / "t.parquet"
        write_cache_metadata(fake_pq, {"cache_key": "abc", "kind": "transform", "context": {"u": "csi800"}})
        meta_path = Path(str(fake_pq) + ".meta.json")
        self.assertTrue(meta_path.exists())

        loaded = read_cache_metadata(fake_pq)
        self.assertEqual(loaded["cache_key"], "abc")
        self.assertEqual(loaded["_version"], 1)

    def test_metadata_missing_returns_empty(self):
        from qsys.feature.cache import read_cache_metadata
        self.assertEqual(read_cache_metadata(self.tmpdir / "no.parquet"), {})


if __name__ == "__main__":
    unittest.main()
