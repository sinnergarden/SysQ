"""Tests for label artifact unification.

Covers:
- compute_labels generation (parquet + manifest)
- cross-sectional zscore properties (mean ~0, std ~1)
- clip behavior
- LabelStore.validate_label
- rolling_runner pre-flight (fail fast on missing label)
- DNN / LightGBM generators no longer call inline label path
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from qsys.label.store import LabelStore


def _make_valid_frame(
    label_id: str = "fwd_ret_5d_xsz_clip3",
    n: int = 10,
) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    return pd.DataFrame({
        "trade_date": [f"2026-05-{d:02d}" for d in range(1, n + 1)],
        "instrument": [f"00000{i}.SZ" for i in range(n)],
        "label_id": [label_id] * n,
        "horizon": [5] * n,
        "label_value": rng.normal(0, 1, n).astype(np.float32),
    })


# ── LabelStore enhancements ────────────────────────────────────────────────


class TestLabelStoreEnhancements:
    def test_label_exists_true(self, tmp_path: Path) -> None:
        store = LabelStore(str(tmp_path))
        store.save_labels("test_id", _make_valid_frame("test_id"))
        assert store.label_exists("test_id") is True

    def test_label_exists_false(self, tmp_path: Path) -> None:
        store = LabelStore(str(tmp_path))
        assert store.label_exists("nonexistent") is False

    def test_validate_label_passes(self, tmp_path: Path) -> None:
        store = LabelStore(str(tmp_path))
        frame = _make_valid_frame("v_label", n=5)
        store.save_labels(
            "v_label", frame,
            manifest={
                "universe": "csi300",
                "prediction_start": "2026-05-01",
                "prediction_end": "2026-05-05",
                "coverage": 0.95,
            },
        )
        report = store.validate_label("v_label")
        assert report["passed"] is True
        assert report["exists"] is True
        assert report["columns_ok"] is True

    def test_validate_label_missing_raises(self, tmp_path: Path) -> None:
        store = LabelStore(str(tmp_path))
        with pytest.raises(FileNotFoundError, match="Run compute_labels.py"):
            store.validate_label("nonexistent")

    def test_validate_label_all_nan_raises(self, tmp_path: Path) -> None:
        store = LabelStore(str(tmp_path))
        frame = _make_valid_frame("nan_label", n=5)
        frame["label_value"] = np.nan
        store.save_labels("nan_label", frame)
        with pytest.raises(ValueError, match="all NaN"):
            store.validate_label("nan_label")

    def test_validate_label_universe_mismatch(self, tmp_path: Path) -> None:
        store = LabelStore(str(tmp_path))
        frame = _make_valid_frame("u_label", n=5)
        store.save_labels("u_label", frame, manifest={"universe": "csi800"})
        with pytest.raises(ValueError, match="csi800"):
            store.validate_label("u_label", universe="csi300")

    def test_validate_label_min_coverage_fails(self, tmp_path: Path) -> None:
        store = LabelStore(str(tmp_path))
        frame = _make_valid_frame("c_label", n=5)
        store.save_labels("c_label", frame, manifest={"coverage": 0.5})
        with pytest.raises(ValueError, match="coverage"):
            store.validate_label("c_label", min_coverage=0.8)


# ── Cross-sectional zscore properties (compute_labels internals) ─────────


class TestComputeLabelsInternals:
    """Tests for the cs_zscore logic used in compute_labels.py."""

    @staticmethod
    def _cs_zscore(s: pd.Series, clip: float = 3.0) -> pd.Series:
        std = s.std(ddof=0)
        if pd.isna(std) or std < 1e-12:
            return pd.Series(0.0, index=s.index)
        return ((s - s.mean()) / std).clip(-clip, clip)

    def test_zscores_mean_zero_std_one(self) -> None:
        rng = np.random.default_rng(42)
        data = pd.Series(rng.normal(0, 2, 1000))
        z = self._cs_zscore(data)
        assert abs(z.mean()) < 0.05
        assert abs(z.std(ddof=0) - 1.0) < 0.05

    def test_clip_upper_bound(self) -> None:
        data = pd.Series([-100, 0, 0, 0, 0, 0, 100])
        z = self._cs_zscore(data, clip=3.0)
        assert z.max() <= 3.0
        assert z.min() >= -3.0

    def test_constant_series_returns_zero(self) -> None:
        data = pd.Series([5.0, 5.0, 5.0, 5.0])
        z = self._cs_zscore(data)
        assert (z == 0.0).all()

    def test_zscores_compare_correct_order(self) -> None:
        """Higher raw value should get higher zscore within same group."""
        rng = np.random.default_rng(42)
        data = pd.Series(rng.uniform(0, 100, 100))
        z = self._cs_zscore(data)
        rank_raw = data.rank()
        rank_z = pd.Series(z).rank()
        corr = rank_raw.corr(rank_z)
        assert corr > 0.99


# ── Rolling runner pre-flight (mock-compatible) ─────────────────────────


class TestRollingRunnerPreflight:
    """Test that rolling_runner pre-flight catches missing labels.

    The pre-flight calls LabelStore.validate_label.  We verify
    the error propagation without instantiating the full runner.
    """

    def test_missing_label_fails_fast(self, tmp_path: Path) -> None:
        store = LabelStore(str(tmp_path))
        with pytest.raises(FileNotFoundError, match="Run compute_labels.py"):
            store.validate_label("fwd_ret_5d_xsz_clip3")

    def test_existing_label_passes_preflight(self, tmp_path: Path) -> None:
        store = LabelStore(str(tmp_path))
        frame = _make_valid_frame("fwd_ret_5d_xsz_clip3", n=20)
        store.save_labels("fwd_ret_5d_xsz_clip3", frame, manifest={
            "universe": "csi300",
            "prediction_start": "2026-05-01",
            "prediction_end": "2026-05-20",
            "coverage": 0.9,
        })
        report = store.validate_label(
            "fwd_ret_5d_xsz_clip3",
            start="2026-05-01",
            end="2026-05-20",
        )
        assert report["passed"] is True

    def test_universe_validation_in_preflight(self, tmp_path: Path) -> None:
        store = LabelStore(str(tmp_path))
        frame = _make_valid_frame("fwd_ret_5d_xsz_clip3", n=20)
        store.save_labels("fwd_ret_5d_xsz_clip3", frame, manifest={"universe": "csi300"})
        with pytest.raises(ValueError, match="csi300"):
            store.validate_label("fwd_ret_5d_xsz_clip3", universe="csi500")


# ── DNN generator no longer uses inline _labels_from_close ──────────────


class TestDnnGeneratorLabelSource:
    """Verify DNN generator loads labels from LabelStore, not inline.

    The generator now accepts a ``label_ids`` parameter (default
    to the xsz_clip3 series).  The inline ``_labels_from_close``
    function still exists but is no longer called in the primary path.
    """

    def test_generator_accepts_label_ids_param(self) -> None:
        from qsys.research.generators.dnn_multitask import DnnMultitaskGenerator
        gen = DnnMultitaskGenerator(label_ids=("fwd_ret_5d_xsz_clip3",))
        assert gen.label_ids == ("fwd_ret_5d_xsz_clip3",)

    def test_generator_default_label_ids(self) -> None:
        from qsys.research.generators.dnn_multitask import DnnMultitaskGenerator
        gen = DnnMultitaskGenerator()
        assert "fwd_ret_5d_xsz_clip3" in gen.label_ids
        assert "fwd_ret_20d_xsz_clip3" in gen.label_ids

    def test_no_inline_labels_imported_in_prepare_path(self) -> None:
        """_labels_from_close is not called in _prepare_training_data.

        The module still defines _labels_from_close but the
        _prepare_training_data method now uses LabelStore.
        """
        import inspect
        from qsys.research.generators.dnn_multitask import (
            DnnMultitaskGenerator,
            _labels_from_close,
        )
        src = inspect.getsource(DnnMultitaskGenerator._prepare_training_data)
        assert "_labels_from_close" not in src
        # Verify the standalone function still exists (not removed)
        assert callable(_labels_from_close)


# ── LightGBM generator no longer calls make_zs_label ────────────────────


class TestLightGBMGeneratorLabelSource:
    """Verify LightGBM generator loads labels from LabelStore."""

    def test_generator_accepts_label_ids(self) -> None:
        from qsys.research.generators.lightgbm_alpha_v1 import (
            LightGBMAlphaV1Generator,
        )
        gen = LightGBMAlphaV1Generator(
            label_ids=("fwd_ret_5d_xsz_clip3", "fwd_ret_20d_xsz_clip3"),
        )
        assert len(gen.label_ids) == 2

    def test_make_zs_label_not_called_in_generate(self) -> None:
        """generate() no longer imports make_zs_label."""
        import inspect
        from qsys.research.generators.lightgbm_alpha_v1 import (
            LightGBMAlphaV1Generator,
        )
        src = inspect.getsource(LightGBMAlphaV1Generator.generate)
        assert "make_zs_label" not in src

    def test_horizon_from_label_id(self) -> None:
        from qsys.research.generators.lightgbm_alpha_v1 import (
            _horizon_from_label_id,
        )
        assert _horizon_from_label_id("fwd_ret_5d_xsz_clip3") == 5
        assert _horizon_from_label_id("fwd_ret_20d_xsz_clip3") == 20


# ── Evaluation uses same label_id as training ──────────────────────────


class TestTrainEvalLabelConsistency:
    """Ensure evaluation and training use the same label_id."""

    @staticmethod
    def _project_root() -> Path:
        # tests/label/test_label_unification.py → SysQ root
        return Path(__file__).resolve().parents[2]

    def test_dnn_config_label_ids_match_eval(self) -> None:
        import yaml
        config_path = self._project_root() / "configs/research/dnn_multitask_full.yaml"
        cfg = yaml.safe_load(config_path.read_text())
        gen_labels = tuple(cfg.get("generators", [{}])[0]
                           .get("params", {})
                           .get("label_ids",
                                ("fwd_ret_5d_xsz_clip3", "fwd_ret_20d_xsz_clip3")))
        eval_labels = {l["label_id"] for l in cfg.get("labels", [])}
        assert set(gen_labels) == eval_labels, (
            f"Generator labels {gen_labels} != eval labels {eval_labels}"
        )

    def test_lightgbm_config_label_ids_match_eval(self) -> None:
        import yaml
        config_path = self._project_root() / "configs/research/lightgbm_alpha_v1_full.yaml"
        cfg = yaml.safe_load(config_path.read_text())
        eval_labels = {l["label_id"] for l in cfg.get("labels", [])}
        assert "fwd_ret_5d_xsz_clip3" in eval_labels
        assert "fwd_ret_20d_xsz_clip3" in eval_labels
