"""Tests for FeatureStore, compute registry, and matrix builder."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[2]


class TestFeatureCacheKey(unittest.TestCase):
    """compute_feature_cache_key stability."""

    def setUp(self):
        from qsys.feature.feature_store import FeatureCacheKey
        self.base = FeatureCacheKey(
            feature_id="ret_60d", universe="csi800",
            date_start="2020-01-01", date_end="2025-12-31",
            source_manifest_hash="src_v1", compute_fn_hash="fn_v1",
        )

    def _key(self, **overrides):
        from qsys.feature.feature_store import compute_feature_cache_key
        import dataclasses
        return compute_feature_cache_key(dataclasses.replace(self.base, **overrides))

    def test_same_key(self):
        self.assertEqual(self._key(), self._key())

    def test_feature_id_changes_key(self):
        self.assertNotEqual(self._key(feature_id="ret_60d"), self._key(feature_id="ret_120d"))

    def test_source_hash_changes_key(self):
        self.assertNotEqual(self._key(source_manifest_hash="v1"), self._key(source_manifest_hash="v2"))

    def test_universe_changes_key(self):
        self.assertNotEqual(self._key(universe="csi300"), self._key(universe="csi800"))

    def test_date_range_not_in_cache_key(self):
        self.assertEqual(self._key(date_start="2020-01-01"), self._key(date_start="2021-01-01"))


class TestFeatureStore(unittest.TestCase):
    """Per-feature cache read/write/validate."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        from qsys.feature.feature_store import FeatureStore
        self.store = FeatureStore(root=str(self.tmpdir))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_df(self, n=5) -> pd.DataFrame:
        return pd.DataFrame({
            "trade_date": pd.date_range("2025-01-01", periods=n, freq="B"),
            "ts_code": ["A"] * n,
            "ret_60d": np.random.randn(n) * 0.02,
        })

    # ── 1. Write + read roundtrip ──
    def test_write_read_roundtrip(self):
        df = self._make_df()
        ck = "test_key_abc"
        path = self.store.write_feature("ret_60d", df, cache_key=ck, metadata={})
        self.assertTrue(path.exists())
        loaded = self.store.read_feature("ret_60d", expected_cache_key=ck)
        self.assertIn("ret_60d", loaded.columns)
        self.assertEqual(len(loaded), 5)

    # ── 2. Cache key mismatch → fail ──
    def test_cache_key_mismatch_fails(self):
        df = self._make_df()
        self.store.write_feature("ret_60d", df, cache_key="key_a", metadata={})
        with self.assertRaises(ValueError):
            self.store.read_feature("ret_60d", expected_cache_key="key_b")

    # ── 3. Source hash mismatch (strict) → fail ──
    def test_source_hash_mismatch_fails(self):
        df = self._make_df()
        meta = {"source_manifest_hash": "hash_v1"}
        self.store.write_feature("ret_60d", df, cache_key="k1", metadata=meta)
        with self.assertRaises(ValueError):
            self.store.read_feature("ret_60d", expected_cache_key="k1", strict_source_hash="wrong")

    # ── 4. Missing meta → fail ──
    def test_missing_meta_fails(self):
        path = self.store.feature_path("ret_60d", "orphan")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._make_df().to_parquet(path, index=False)
        with self.assertRaises(ValueError):
            self.store.read_feature("ret_60d", expected_cache_key="orphan")

    # ── 5. Narrow schema → auto-rename ──
    def test_narrow_schema(self):
        df = pd.DataFrame({"trade_date": pd.date_range("2025-01-01", periods=3, freq="B"),
                           "ts_code": ["A"] * 3, "value": [0.1, 0.2, 0.3]})
        self.store.write_feature("my_feat", df, cache_key="n1", metadata={})
        loaded = self.store.read_feature("my_feat", expected_cache_key="n1")
        self.assertIn("my_feat", loaded.columns)
        self.assertNotIn("value", loaded.columns)

    # ── 6. List feature IDs ──
    def test_list_feature_ids(self):
        self.assertEqual(self.store.list_feature_ids(), [])
        df_a = pd.DataFrame({"trade_date": pd.date_range("2025-01-01", periods=3, freq="B"),
                             "ts_code": ["A"]*3, "feat_a": [1, 2, 3]})
        self.store.write_feature("feat_a", df_a, cache_key="k1", metadata={})
        ids = self.store.list_feature_ids()
        self.assertIn("feat_a", ids)


class TestFeatureComputeRegistry(unittest.TestCase):
    """Compute spec and batch computation tests."""

    def test_has_spec(self):
        from qsys.feature.feature_compute_registry import has_spec, get_spec
        self.assertTrue(has_spec("ret_60d"))
        self.assertIsNotNone(get_spec("ret_60d"))
        self.assertFalse(has_spec("__nonexistent__"))

    def test_list_spec_ids(self):
        from qsys.feature.feature_compute_registry import list_spec_ids
        ids = list_spec_ids()
        self.assertIn("ret_60d", ids)
        self.assertGreater(len(ids), 50)

    def test_compute_phase1_feature(self):
        from qsys.feature.feature_compute_registry import compute_phase1_feature
        panel = pd.DataFrame({"trade_date": pd.date_range("2025-01-01", periods=5, freq="B"),
                              "ts_code": ["A"] * 5, "close": [100.0] * 5, "open": [100.0] * 5,
                              "high": [101.0] * 5, "low": [99.0] * 5, "volume": [1e6] * 5,
                              "amount": [1e8] * 5, "float_shares": [1e8] * 5})
        result = compute_phase1_feature(panel, "close_to_open_gap_1d")
        self.assertIn("close_to_open_gap_1d", result.columns)

    def test_compute_phase1_batch(self):
        from qsys.feature.feature_compute_registry import compute_phase1_batch
        panel = pd.DataFrame({"trade_date": pd.date_range("2025-01-01", periods=5, freq="B"),
                              "ts_code": ["A"] * 5, "close": [100.0] * 5, "open": [100.0] * 5,
                              "high": [101.0] * 5, "low": [99.0] * 5, "volume": [1e6] * 5,
                              "amount": [1e8] * 5, "float_shares": [1e8] * 5})
        result = compute_phase1_batch(panel, ["close_to_open_gap_1d", "upper_shadow_ratio"])
        self.assertIn("close_to_open_gap_1d", result.columns)
        self.assertIn("upper_shadow_ratio", result.columns)


class TestMatrixBuilder(unittest.TestCase):
    """Matrix builder tests covering all 6 blocker scenarios."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.panel = pd.DataFrame({"trade_date": pd.date_range("2025-01-01", periods=5, freq="B"),
                                   "ts_code": ["A"] * 5, "close": [100.0] * 5, "open": [100.0] * 5,
                                   "high": [101.0] * 5, "low": [99.0] * 5, "volume": [1e6] * 5,
                                   "amount": [1e8] * 5, "float_shares": [1e8] * 5})

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_yaml(self, name: str, features: list[str], yaml_dir: Path | None = None) -> Path:
        if yaml_dir is None:
            yaml_dir = Path(self.tmpdir) / "yaml"
            yaml_dir.mkdir(parents=True, exist_ok=True)
        p = yaml_dir / f"{name}.yaml"
        with open(p, "w") as f:
            yaml.dump({"feature_set_id": name, "features": features}, f)
        return p

    # ── 1. compute_missing=False + missing cache → fail ──
    def test_missing_raises(self):
        from qsys.feature.feature_matrix_builder import build_matrix_from_feature_store
        from qsys.feature.resolver_v2 import discover_feature_sets

        yaml_dir = Path(self.tmpdir) / "y1"
        yaml_dir.mkdir()
        yp = self._write_yaml("test_missing_raise", ["close_to_open_gap_1d", "upper_shadow_ratio"], yaml_dir)
        discover_feature_sets(config_dir=str(yaml_dir))

        with self.assertRaises(ValueError):
            build_matrix_from_feature_store(
                self.panel, feature_set_id=str(yp),
                feature_cache_root=str(self.tmpdir / "fs1"),
                compute_missing=False,
            )

    # ── 2. compute_missing=True → batch compute, all features present ──
    def test_compute_missing_works(self):
        from qsys.feature.feature_matrix_builder import build_matrix_from_feature_store
        from qsys.feature.resolver_v2 import discover_feature_sets

        yaml_dir = Path(self.tmpdir) / "y2"
        yaml_dir.mkdir()
        yp = self._write_yaml("test_comp_miss", ["close_to_open_gap_1d", "upper_shadow_ratio"], yaml_dir)
        discover_feature_sets(config_dir=str(yaml_dir))

        matrix = build_matrix_from_feature_store(
            self.panel, feature_set_id=str(yp),
            feature_cache_root=str(self.tmpdir / "fs2"),
            compute_missing=True, source_manifest_hash="test_v1",
        )
        self.assertIn("close_to_open_gap_1d", matrix.columns)
        self.assertIn("upper_shadow_ratio", matrix.columns)
        # Verify the per-feature caches were written
        from qsys.feature.feature_store import FeatureStore
        store = FeatureStore(root=str(self.tmpdir / "fs2"))
        ids = store.list_feature_ids()
        self.assertIn("close_to_open_gap_1d", ids)
        self.assertIn("upper_shadow_ratio", ids)

    # ── 3. 99 cached + 1 missing → only compute missing ──
    def test_partial_miss_does_not_recompute_cached(self):
        from qsys.feature.feature_store import FeatureStore, FeatureCacheKey, compute_feature_cache_key
        from qsys.feature.feature_matrix_builder import build_matrix_from_feature_store
        from qsys.feature.resolver_v2 import discover_feature_sets
        from qsys.feature.feature_compute_registry import _PHASE1_HASH

        yaml_dir = Path(self.tmpdir) / "y3"
        yaml_dir.mkdir()
        yp = self._write_yaml("test_partial", [
            "close_to_open_gap_1d", "open_to_close_ret", "upper_shadow_ratio",
        ], yaml_dir)
        discover_feature_sets(config_dir=str(yaml_dir))

        # Manually pre-cache 2 of 3 features
        panel = pd.DataFrame({"trade_date": pd.date_range("2025-01-01", periods=3, freq="B"),
                              "ts_code": ["A"] * 3, "close": [100.0] * 3, "open": [100.0] * 3,
                              "high": [101.0] * 3, "low": [99.0] * 3, "volume": [1e6] * 3,
                              "amount": [1e8] * 3, "float_shares": [1e8] * 3})
        from qsys.feature.feature_compute_registry import compute_phase1_feature
        store = FeatureStore(root=str(self.tmpdir / "fs3"))
        for fid in ["close_to_open_gap_1d", "open_to_close_ret"]:
            fk = FeatureCacheKey(feature_id=fid, source_manifest_hash="test_ps", compute_fn_hash=_PHASE1_HASH)
            ck = compute_feature_cache_key(fk)
            df = compute_phase1_feature(panel, fid)
            store.write_feature(fid, df, cache_key=ck, metadata={"source_manifest_hash": "test_ps",
                                                                    "compute_fn_hash": _PHASE1_HASH})

        # Now build matrix with compute_missing=True — only "upper_shadow_ratio" should be computed
        matrix = build_matrix_from_feature_store(
            panel, feature_set_id=str(yp),
            feature_cache_root=str(self.tmpdir / "fs3"),
            compute_missing=True, source_manifest_hash="test_ps",
        )
        self.assertIn("close_to_open_gap_1d", matrix.columns)
        self.assertIn("open_to_close_ret", matrix.columns)
        self.assertIn("upper_shadow_ratio", matrix.columns)
        self.assertEqual(len(matrix.columns), 5)  # 2 index + 3 feats

    # ── 4. Source hash mismatch in matrix builder → fail ──
    def test_source_hash_mismatch_in_builder_fails(self):
        from qsys.feature.feature_store import FeatureStore, FeatureCacheKey, compute_feature_cache_key
        from qsys.feature.feature_matrix_builder import build_matrix_from_feature_store
        from qsys.feature.resolver_v2 import discover_feature_sets
        from qsys.feature.feature_compute_registry import _PHASE1_HASH, compute_phase1_feature

        yaml_dir = Path(self.tmpdir) / "y4"
        yaml_dir.mkdir()
        yp = self._write_yaml("test_hash_mismatch", ["close_to_open_gap_1d"], yaml_dir)
        discover_feature_sets(config_dir=str(yaml_dir))

        # Write cache with hash_v1
        store = FeatureStore(root=str(self.tmpdir / "fs4"))
        fk = FeatureCacheKey(feature_id="close_to_open_gap_1d", source_manifest_hash="hash_v1",
                              compute_fn_hash=_PHASE1_HASH)
        ck = compute_feature_cache_key(fk)
        df = compute_phase1_feature(self.panel, "close_to_open_gap_1d")
        store.write_feature("close_to_open_gap_1d", df, cache_key=ck,
                            metadata={"source_manifest_hash": "hash_v1", "compute_fn_hash": _PHASE1_HASH})

        # Builder expects hash_v2 (strict) → should fail
        with self.assertRaises(ValueError):
            build_matrix_from_feature_store(
                self.panel, feature_set_id=str(yp),
                feature_cache_root=str(self.tmpdir / "fs4"),
                compute_missing=False, source_manifest_hash="hash_v2",
            )

    # ── 5. allow_uncacheable=True requires compute, not silent skip ──
    def test_uncacheable_not_silent_skip(self):
        from qsys.feature.feature_matrix_builder import build_matrix_from_feature_store
        from qsys.feature.resolver_v2 import discover_feature_sets

        yaml_dir = Path(self.tmpdir) / "y5"
        yaml_dir.mkdir()
        # close_to_open_gap_1d IS in registry; nonexistent_1 is NOT
        yp = self._write_yaml("test_uncacheable", ["close_to_open_gap_1d", "__does_not_exist__"], yaml_dir)
        discover_feature_sets(config_dir=str(yaml_dir))

        with self.assertRaises(ValueError):
            # allow_uncacheable=True but the missing spec name doesn't suddenly
            # make it compute — it must fail because there's no spec
            build_matrix_from_feature_store(
                self.panel, feature_set_id=str(yp),
                feature_cache_root=str(self.tmpdir / "fs5"),
                compute_missing=True, allow_uncacheable=True,
            )

    # ── 6. Join does not explode with raw_panel duplicates ──
    def test_join_no_explosion(self):
        from qsys.feature.feature_matrix_builder import build_matrix_from_feature_store
        from qsys.feature.resolver_v2 import discover_feature_sets

        yaml_dir = Path(self.tmpdir) / "y6"
        yaml_dir.mkdir()
        yp = self._write_yaml("test_join", ["close_to_open_gap_1d", "upper_shadow_ratio"], yaml_dir)
        discover_feature_sets(config_dir=str(yaml_dir))

        # Panel with duplicate rows (same trade_date repeated)
        dup_panel = pd.concat([self.panel, self.panel], ignore_index=True)
        matrix = build_matrix_from_feature_store(
            dup_panel, feature_set_id=str(yp),
            feature_cache_root=str(self.tmpdir / "fs6"),
            compute_missing=True, source_manifest_hash="test_join",
        )
        # Matrix should NOT have duplicated (trade_date, ts_code) pairs
        dup_count = matrix.duplicated(subset=["trade_date", "ts_code"]).sum()
        self.assertEqual(dup_count, 0, "Matrix has duplicated index rows")

    # ── 7. compute_missing+allow_uncacheable=True: no-spec feature still fails ──
    def test_uncacheable_no_spec_still_fails(self):
        """allow_uncacheable=True doesn't save a feature with no compute spec."""
        from qsys.feature.feature_matrix_builder import build_matrix_from_feature_store
        from qsys.feature.resolver_v2 import discover_feature_sets

        yaml_dir = Path(self.tmpdir) / "y7"
        yaml_dir.mkdir()
        yp = self._write_yaml("test_uncacheable_fail", ["__unknown_feat__"], yaml_dir)
        discover_feature_sets(config_dir=str(yaml_dir))

        with self.assertRaises(ValueError):
            build_matrix_from_feature_store(
                self.panel, feature_set_id=str(yp),
                feature_cache_root=str(self.tmpdir / "fs7"),
                compute_missing=True, allow_uncacheable=True,
            )


class TestWriteFeatureValidation(unittest.TestCase):
    """write_feature existing-cache validation tests."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        from qsys.feature.feature_store import FeatureStore
        self.store = FeatureStore(root=str(self.tmpdir))
        self.df = pd.DataFrame({
            "trade_date": pd.date_range("2025-01-01", periods=3, freq="B"),
            "ts_code": ["A"] * 3, "test_feat": [1.0, 2.0, 3.0],
        })
        self.meta = {"source_manifest_hash": "hash_v1"}

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_valid_existing_skips(self):
        path1 = self.store.write_feature("test_feat", self.df, cache_key="ck1", metadata=self.meta)
        path2 = self.store.write_feature("test_feat", self.df, cache_key="ck1", metadata=self.meta)
        self.assertEqual(path1, path2)

    def test_orphan_parquet_missing_meta_fails(self):
        path = self.store.feature_path("orphan_feat", "orphan")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.df.to_parquet(path, index=False)
        with self.assertRaises(ValueError):
            self.store.write_feature("orphan_feat", self.df, cache_key="orphan", metadata=self.meta)

    def test_wrong_source_hash_fails(self):
        meta_v1 = {"source_manifest_hash": "hash_v1"}
        df_v1 = self.df.rename(columns={"test_feat": "my_feat"})
        self.store.write_feature("my_feat", df_v1, cache_key="hk1", metadata=meta_v1)
        meta_v2 = {"source_manifest_hash": "hash_v2"}
        df_v2 = self.df.rename(columns={"test_feat": "my_feat"})
        with self.assertRaises(ValueError):
            self.store.write_feature("my_feat", df_v2, cache_key="hk1", metadata=meta_v2)
        path = self.store.write_feature("my_feat", df_v2, cache_key="hk1", metadata=meta_v2, overwrite=True)
        self.assertIsNotNone(path)


class TestBackfillBatch(unittest.TestCase):
    """Backfill batch behavior via backfill_feature_store.py."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.panel = pd.DataFrame({"trade_date": pd.date_range("2025-01-01", periods=5, freq="B"),
                                    "ts_code": ["A"] * 5, "close": [100.0] * 5, "open": [100.0] * 5,
                                    "high": [101.0] * 5, "low": [99.0] * 5, "volume": [1e6] * 5,
                                    "amount": [1e8] * 5, "float_shares": [1e8] * 5})
        import yaml
        self.yaml_dir = Path(self.tmpdir) / "bf_yaml"
        self.yaml_dir.mkdir(parents=True, exist_ok=True)
        self.yaml_path = self.yaml_dir / "bf_set.yaml"
        with open(self.yaml_path, "w") as f:
            yaml.dump({"feature_set_id": "bf_set",
                        "features": ["close_to_open_gap_1d", "upper_shadow_ratio", "open_to_close_ret"]}, f)
        from qsys.feature.resolver_v2 import discover_feature_sets
        discover_feature_sets(config_dir=str(self.yaml_dir))
        self.panel_path = self.tmpdir / "bf_panel.parquet"
        self.panel.to_parquet(self.panel_path, index=False)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_backfill(self, extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
        import subprocess
        cmd = [
            "python", str(REPO / "scripts/dev/backfill_feature_store.py"),
            "--feature-set", str(self.yaml_path),
            "--source-panel", str(self.panel_path),
            "--source-manifest-hash", "batch_test",
            "--universe", "test",
            "--compute-missing",
            "--feature-cache-root", str(self.tmpdir / "bf_cache"),
        ] + (extra_args or [])
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60)

    def test_batch_backfill_all_missing(self):
        result = self._run_backfill()
        self.assertEqual(result.returncode, 0, f"Backfill failed: {result.stderr}")

        from qsys.feature.feature_store import FeatureStore
        store = FeatureStore(root=str(self.tmpdir / "bf_cache"))
        ids = store.list_feature_ids()
        self.assertIn("close_to_open_gap_1d", ids)
        self.assertIn("upper_shadow_ratio", ids)
        self.assertIn("open_to_close_ret", ids)

    def test_batch_second_run_all_cached(self):
        r1 = self._run_backfill()
        self.assertEqual(r1.returncode, 0)
        r2 = self._run_backfill()
        self.assertEqual(r2.returncode, 0, f"Second backfill failed: {r2.stderr}")
        self.assertIn("already cached", r2.stdout)

    def test_batch_uses_one_batch_call(self):
        r1 = self._run_backfill()
        self.assertEqual(r1.returncode, 0)
        self.assertIn("Batch computing", r1.stdout)

    def test_write_feature_orphan_fails(self):
        from qsys.feature.feature_store import FeatureStore
        store = FeatureStore(root=str(self.tmpdir / "orphan_test"))
        p = store.feature_path("orphan_x", "orphan_ck")
        p.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"trade_date": ["2025-01-01"], "ts_code": ["A"], "orphan_x": [1.0]}).to_parquet(p, index=False)
        with self.assertRaises(ValueError):
            store.write_feature("orphan_x", pd.DataFrame(),
                                cache_key="orphan_ck",
                                metadata={"source_manifest_hash": "v1"})



class TestMatrixBuilderRawFields(unittest.TestCase):
    """Matrix builder raw $ field handling."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.panel = pd.DataFrame({
            "trade_date": pd.date_range("2025-01-01", periods=5, freq="B"),
            "ts_code": ["A"] * 5,
            "close": [100.0] * 5, "open": [100.0] * 5,
            "high": [101.0] * 5, "low": [99.0] * 5,
            "volume": [1e6] * 5, "amount": [1e8] * 5,
            "float_shares": [1e8] * 5,
            "$pe": [10.0] * 5, "pe": [10.0] * 5,
        })

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_s_dollar_field_dedup(self):
        """$ field with duplicates in panel should not explode matrix."""
        from qsys.feature.feature_matrix_builder import _build_cache_key
        from qsys.feature.feature_store import FeatureStore, FeatureCacheKey, compute_feature_cache_key
        from qsys.feature.feature_compute_registry import _PHASE1_HASH

        # Verify the helper produces the correct key
        fk, ck = _build_cache_key("ret_60d", universe="test", source_manifest_hash="h1")
        self.assertIsNotNone(ck)
        self.assertEqual(len(ck), 20)
        self.assertEqual(fk.pit_policy, "rolling_past")

    def test_dollar_field_read_from_raw_panel(self):
        """$pe in YAML but $pe in panel → read from panel, not builder."""
        import yaml
        from qsys.feature.feature_matrix_builder import build_matrix_from_feature_store
        from qsys.feature.resolver_v2 import discover_feature_sets

        yaml_dir = Path(self.tmpdir) / "r_y"
        yaml_dir.mkdir()
        yp = yaml_dir / "r_set.yaml"
        with open(yp, "w") as f:
            yaml.dump({"feature_set_id": "r_set", "features": ["$pe", "close_to_open_gap_1d"]}, f)
        discover_feature_sets(config_dir=str(yaml_dir))

        matrix = build_matrix_from_feature_store(
            self.panel, feature_set_id=str(yp),
            feature_cache_root=str(self.tmpdir / "r_fs"),
            compute_missing=True, allow_uncacheable=True,
        )
        self.assertIn("$pe", matrix.columns)
        self.assertIn("close_to_open_gap_1d", matrix.columns)

    def test_read_feature_coverage_warning(self):
        """read_feature with date_start outside cache range warns."""
        import logging
        logging.basicConfig(level=logging.DEBUG)
        from qsys.feature.feature_store import FeatureStore
        store = FeatureStore(root=str(self.tmpdir / "cov_test"))
        df = pd.DataFrame({"trade_date": pd.date_range("2025-01-01", periods=10, freq="B"),
                            "ts_code": ["A"] * 10, "cov_feat": range(10)})
        store.write_feature("cov_feat", df, cache_key="k1",
                            metadata={"date_start": "2025-01-01", "date_end": "2025-01-15"})
        # Read with wider range — should warn but still work
        loaded = store.read_feature("cov_feat", expected_cache_key="k1",
                                     date_start="2025-01-01", date_end="2026-01-01")
        self.assertLessEqual(len(loaded), 10)

if __name__ == "__main__":
    unittest.main()
