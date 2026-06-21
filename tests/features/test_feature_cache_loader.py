"""Tests for cache-aware feature matrix loader.

Coverage:
1. use_feature_cache=False → unchanged path
2. use_feature_cache=True + cache hit → read cache
3. use_feature_cache=True + miss + no materialize → fail
4. use_feature_cache=True + miss + materialize → auto-materialize
5. Cache columns = ["trade_date", "ts_code"] + resolved_features
6. Cache missing feature → fail
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[2]


def _make_panel(n: int = 30) -> pd.DataFrame:
    np.random.seed(0)
    return pd.DataFrame({
        "trade_date": pd.date_range("2025-01-01", periods=n, freq="B"),
        "ts_code": ["A"] * n,
        "close": 100.0 + np.cumsum(np.random.randn(n) * 0.5),
        "open": 100.0 + np.cumsum(np.random.randn(n) * 0.5),
        "high": 100.0 + np.cumsum(np.random.randn(n) * 0.6),
        "low": 100.0 + np.cumsum(np.random.randn(n) * 0.6),
        "volume": np.random.uniform(1e6, 1e8, n),
        "amount": np.random.uniform(1e8, 2e9, n),
        "float_shares": np.random.uniform(1e8, 1e9, n),
    })


class TestCacheLoader(unittest.TestCase):
    """Cache loader tests."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.panel = _make_panel()

        # Write test YAML with microstructure features
        self.yaml_dir = self.tmpdir / "yaml"
        self.yaml_dir.mkdir(parents=True, exist_ok=True)
        self.test_yaml = self.yaml_dir / "test_loader_set.yaml"
        with open(self.test_yaml, "w") as f:
            yaml.dump({
                "feature_list_id": "test_loader_set",
                "features": ["close_to_open_gap_1d", "upper_shadow_ratio"],
            }, f)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ── 1. use_feature_cache=False → unchanged ──

    def test_disabled_returns_raw(self):
        from qsys.feature.cache_loader import load_feature_matrix_with_cache

        result = load_feature_matrix_with_cache(
            self.panel,
            feature_set_id=str(self.test_yaml),
            use_feature_cache=False,
        )
        self.assertIs(result, self.panel)

    # ── 2. use_feature_cache=True + hit → read cache ──

    def test_cache_hit_reads_cache(self):
        from qsys.feature.cache_loader import load_feature_matrix_with_cache

        # First materialize
        load_feature_matrix_with_cache(
            self.panel,
            feature_set_id=str(self.test_yaml),
            source_manifest_hash="test_hit",
            universe="test_hit",
            cache_root=str(self.tmpdir / "fc"),
            use_feature_cache=True,
            materialize_on_miss=True,
        )

        # Second call should hit
        result = load_feature_matrix_with_cache(
            self.panel,
            feature_set_id=str(self.test_yaml),
            source_manifest_hash="test_hit",
            universe="test_hit",
            cache_root=str(self.tmpdir / "fc"),
            use_feature_cache=True,
            materialize_on_miss=False,
        )
        self.assertIsNot(result, self.panel)
        self.assertIn("trade_date", result.columns)
        self.assertIn("ts_code", result.columns)
        self.assertIn("close_to_open_gap_1d", result.columns)
        self.assertIn("upper_shadow_ratio", result.columns)

    # ── 3. Miss + no materialize → fail ──

    def test_miss_no_materialize_fails(self):
        from qsys.feature.cache_loader import load_feature_matrix_with_cache

        with self.assertRaises(ValueError):
            load_feature_matrix_with_cache(
                self.panel,
                feature_set_id=str(self.test_yaml),
                source_manifest_hash="test_miss",
                universe="test_miss",
                cache_root=str(self.tmpdir / "fc2"),
                use_feature_cache=True,
                materialize_on_miss=False,
            )

    # ── 4. Miss + materialize → auto-materialize ──

    def test_miss_materialize_works(self):
        from qsys.feature.cache_loader import load_feature_matrix_with_cache

        result = load_feature_matrix_with_cache(
            self.panel,
            feature_set_id=str(self.test_yaml),
            source_manifest_hash="test_mat",
            universe="test_mat",
            cache_root=str(self.tmpdir / "fc3"),
            use_feature_cache=True,
            materialize_on_miss=True,
        )
        self.assertIn("close_to_open_gap_1d", result.columns)
        self.assertIn("upper_shadow_ratio", result.columns)

    # ── 5. Column order ──

    def test_column_order(self):
        from qsys.feature.cache_loader import load_feature_matrix_with_cache

        result = load_feature_matrix_with_cache(
            self.panel,
            feature_set_id=str(self.test_yaml),
            source_manifest_hash="test_cols",
            universe="test_cols",
            cache_root=str(self.tmpdir / "fc4"),
            use_feature_cache=True,
            materialize_on_miss=True,
        )
        expected = ["trade_date", "ts_code", "close_to_open_gap_1d", "upper_shadow_ratio"]
        self.assertListEqual(list(result.columns), expected)

    # ── 6. Missing feature → fail ──

    def test_missing_feature_fails(self):
        """Register a spec for a feature that won't be computed — must fail."""
        from qsys.feature.cache_loader import load_feature_matrix_with_cache

        # Write YAML with a feature that won't be in raw_panel
        bad_yaml = self.yaml_dir / "bad_set.yaml"
        with open(bad_yaml, "w") as f:
            yaml.dump({
                "feature_list_id": "bad_set",
                "features": ["margin_eligible"],
            }, f)

        with self.assertRaises(ValueError):
            load_feature_matrix_with_cache(
                self.panel,
                feature_set_id=str(bad_yaml),
                source_manifest_hash="test_bad",
                cache_root=str(self.tmpdir / "fc5"),
                use_feature_cache=True,
                materialize_on_miss=True,
            )

    # ── 7. Source hash change → miss → materialize → works ──

    def test_source_hash_change_causes_miss(self):
        from qsys.feature.cache_loader import load_feature_matrix_with_cache

        # Materialize with hash v1
        load_feature_matrix_with_cache(
            self.panel,
            feature_set_id=str(self.test_yaml),
            source_manifest_hash="hash_v1",
            universe="test_hash",
            cache_root=str(self.tmpdir / "fc6"),
            use_feature_cache=True,
            materialize_on_miss=True,
        )

        # Same universe but different hash → should NOT crash, should materialize
        result = load_feature_matrix_with_cache(
            self.panel,
            feature_set_id=str(self.test_yaml),
            source_manifest_hash="hash_v2",
            universe="test_hash",
            cache_root=str(self.tmpdir / "fc6"),
            use_feature_cache=True,
            materialize_on_miss=True,
        )
        self.assertIn("close_to_open_gap_1d", result.columns)

    # ── 8. Disabled + source_manifest_hash ignored ──

    def test_disabled_ignores_hash(self):
        """Disabled path should work even with garbage source_manifest_hash."""
        from qsys.feature.cache_loader import load_feature_matrix_with_cache

        result = load_feature_matrix_with_cache(
            self.panel,
            feature_set_id=str(self.test_yaml),
            source_manifest_hash="",
            use_feature_cache=False,
        )
        self.assertIs(result, self.panel)


if __name__ == "__main__":
    unittest.main()
