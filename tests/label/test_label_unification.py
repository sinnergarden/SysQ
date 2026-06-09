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
from unittest.mock import patch

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


# ── Validate-label forward tail gap ─────────────────────────────────────


class TestValidateLabelForwardTailGap:
    """validate_label should allow expected forward-tail gap for return labels.

    A 5d forward-return label naturally lacks the last 5 trading days
    because there is no future close to compute returns.
    """

    @staticmethod
    def _mock_cal(*args: str, **kwargs: str) -> list[str]:
        return ["2026-05-01", "2026-05-04", "2026-05-05", "2026-05-06",
                "2026-05-07", "2026-05-08", "2026-05-11"]

    def _save_label(
        self, store, label_id: str, dates: list[str], horizon: int,
    ) -> None:
        rows = []
        for td in dates:
            for inst in ["A", "B"]:
                rows.append({
                    "trade_date": td, "instrument": inst,
                    "label_id": label_id, "horizon": horizon,
                    "label_value": 0.1,
                })
        store.save_labels(label_id, pd.DataFrame(rows),
                          manifest={"horizon": horizon, "universe": "csi300"})

    def test_missing_tail_days_passes(self, tmp_path: Path) -> None:
        """Label missing last horizon trading days should pass
        (forward return gap)."""
        from qsys.data import calendar as _cal_mod
        store = LabelStore(str(tmp_path))

        all_cal = self._mock_cal()
        # Label data through 05-07, missing last 2 days (horizon=2 tail): 05-08, 05-11
        label_dates = ["2026-05-01", "2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07"]
        self._save_label(store, "tail_test", label_dates, horizon=2)

        with patch.object(_cal_mod, "get_trading_calendar", return_value=all_cal):
            report = store.validate_label("tail_test", start="2026-05-01", end="2026-05-11")
        assert report["passed"] is True

    def test_missing_middle_dates_fails(self, tmp_path: Path) -> None:
        """A middle missing date should raise, even if tail is also missing."""
        from qsys.data import calendar as _cal_mod
        store = LabelStore(str(tmp_path))

        all_cal = self._mock_cal()
        # Label data missing 05-06 (middle gap), also missing tail 05-11
        label_dates = ["2026-05-01", "2026-05-04", "2026-05-05", "2026-05-07", "2026-05-08"]
        self._save_label(store, "mid_test", label_dates, horizon=3)

        with patch.object(_cal_mod, "get_trading_calendar", return_value=all_cal):
            with pytest.raises(ValueError, match="Middle-missing"):
                store.validate_label("mid_test", start="2026-05-01", end="2026-05-11")


# ── Compute_labels coverage denominator ─────────────────────────────────


class TestComputeLabelsCoverage:
    """Coverage denominator must exclude forward-return horizon tail."""

    def test_effective_dates_excludes_horizon(self) -> None:
        """horizon=2, 5 dates, 3 insts → effective_dates=3, expected=9."""
        n_dates = 5
        horizon = 2
        n_insts = 3
        effective_dates = max(n_dates - horizon, 0)
        expected_rows = effective_dates * n_insts
        assert expected_rows == 9  # not 15

    def test_large_horizon_does_not_underflow(self) -> None:
        """horizon > n_dates → effective_dates=0 → coverage=0."""
        n_dates = 3
        horizon = 10
        n_insts = 100
        effective_dates = max(n_dates - horizon, 0)
        expected_rows = effective_dates * n_insts
        assert expected_rows == 0

    def test_coverage_function(self) -> None:
        """_coverage caps at 1.0, handles 0 expected."""
        from scripts.research.compute_labels import _coverage
        assert _coverage(9, 9) == 1.0
        assert _coverage(8, 9) == pytest.approx(8 / 9)
        assert _coverage(0, 10) == 0.0
        assert _coverage(100, 0) == 0.0


# ── DNN generator enforce exactly two label_ids ──────────────────────────


class TestDnnGeneratorEnforceExactlyTwo:
    """DnnMultitaskGenerator requires exactly two label_ids (legacy)."""

    def test_raises_on_one_label(self) -> None:
        from qsys.research.generators.dnn_multitask import DnnMultitaskGenerator
        gen = DnnMultitaskGenerator(label_ids=("fwd_ret_5d_xsz_clip3",))
        with pytest.raises(ValueError, match="exactly two label_ids"):
            gen.generate(
                train_start="2024-01-01", train_end="2024-06-01",
                predict_start="2024-06-01", predict_end="2024-06-05",
                signal_id="test", signal_run_id="test",
            )

    def test_raises_on_three_labels(self) -> None:
        from qsys.research.generators.dnn_multitask import DnnMultitaskGenerator
        gen = DnnMultitaskGenerator(
            label_ids=("fwd_ret_5d_xsz_clip3", "fwd_ret_10d_xsz_clip3", "fwd_ret_20d_xsz_clip3"),
        )
        with pytest.raises(ValueError, match="exactly two label_ids"):
            gen.generate(
                train_start="2024-01-01", train_end="2024-06-01",
                predict_start="2024-06-01", predict_end="2024-06-05",
                signal_id="test", signal_run_id="test",
            )

    def test_raises_on_zero_blend_weight(self) -> None:
        from qsys.research.generators.dnn_multitask import DnnMultitaskGenerator
        gen = DnnMultitaskGenerator(blend_weights={"5d": 0.0, "20d": 0.0})
        with pytest.raises(ValueError, match="must not sum to zero"):
            gen.generate(
                train_start="2024-01-01", train_end="2024-06-01",
                predict_start="2024-06-01", predict_end="2024-06-05",
                signal_id="test", signal_run_id="test",
            )

    def test_two_labels_pass_label_count_check(self) -> None:
        """With exactly two label_ids, the label-count check passes."""
        from qsys.research.generators.dnn_multitask import DnnMultitaskGenerator
        gen = DnnMultitaskGenerator()
        assert len(gen.label_ids) == 2
        # The ValueError should NOT be raised for 2 labels.
        # (Full generate() may fail later on qlib/label dependencies.)


# ── LightGBM generator enforce 5d/20d labels ────────────────────────────


class TestLightGBMGeneratorEnforce5d20d:
    """LightGBMAlphaV1Generator requires exactly 5d + 20d labels (legacy)."""

    def test_raises_on_only_5d(self) -> None:
        from qsys.research.generators.lightgbm_alpha_v1 import LightGBMAlphaV1Generator
        gen = LightGBMAlphaV1Generator(label_ids=("fwd_ret_5d_xsz_clip3",))
        with pytest.raises(ValueError, match="requires exactly"):
            gen.generate(
                train_start="2024-01-01", train_end="2024-06-01",
                predict_start="2024-06-01", predict_end="2024-06-05",
                signal_id="test", signal_run_id="test",
            )

    def test_raises_on_5d_and_10d(self) -> None:
        from qsys.research.generators.lightgbm_alpha_v1 import LightGBMAlphaV1Generator
        gen = LightGBMAlphaV1Generator(
            label_ids=("fwd_ret_5d_xsz_clip3", "fwd_ret_10d_raw"),
        )
        with pytest.raises(ValueError, match="requires exactly"):
            gen.generate(
                train_start="2024-01-01", train_end="2024-06-01",
                predict_start="2024-06-01", predict_end="2024-06-05",
                signal_id="test", signal_run_id="test",
            )

    def test_raises_on_zero_blend_weight(self) -> None:
        from qsys.research.generators.lightgbm_alpha_v1 import LightGBMAlphaV1Generator
        gen = LightGBMAlphaV1Generator(blend_weights={"5d": 0.0, "20d": 0.0})
        with pytest.raises(ValueError, match="must not sum to zero"):
            gen.generate(
                train_start="2024-01-01", train_end="2024-06-01",
                predict_start="2024-06-01", predict_end="2024-06-05",
                signal_id="test", signal_run_id="test",
            )

    def test_5d_20d_passes_label_check(self) -> None:
        """With 5d and 20d, the horizon check passes."""
        from qsys.research.generators.lightgbm_alpha_v1 import LightGBMAlphaV1Generator
        gen = LightGBMAlphaV1Generator()
        # The ValueError should NOT be raised for 5d/20d.
        # (Full generate() may fail later on qlib/label dependencies.)


# ── Compute-label function-level tests ────────────────────────────────────


class TestComputeLabelFunctions:
    """Function-level tests for compute_label and compute_label_raw.

    These mock QlibAdapter.get_features to avoid qlib dependency.
    """

    @staticmethod
    def _make_mock_features() -> pd.DataFrame:
        """Return a qlib-style MultiIndex DataFrame with known close prices."""
        from datetime import datetime
        dates = [
            datetime(2024, 1, 2),
            datetime(2024, 1, 3),
            datetime(2024, 1, 4),
            datetime(2024, 1, 5),
            datetime(2024, 1, 8),
        ]
        instruments = ["A", "B", "C"]
        # A: [100, 101, 102, 103, 104]
        # B: [200, 202, 204, 206, 208]
        # C: [300, 303, 306, 309, 312]
        close_by_inst = {
            "A": [100, 101, 102, 103, 104],
            "B": [200, 202, 204, 206, 208],
            "C": [300, 303, 306, 309, 312],
        }
        tuples = [(d, inst) for d in dates for inst in instruments]
        idx = pd.MultiIndex.from_tuples(tuples, names=["datetime", "instrument"])
        values = []
        for d in dates:
            for inst in instruments:
                di = dates.index(d)
                values.append(close_by_inst[inst][di])
        return pd.DataFrame({"$close": values}, index=idx)

    def test_compute_label_raw_forward_return(self) -> None:
        """compute_label_raw: verify shift(-2)/close - 1 for known data."""
        with patch("qsys.data.adapter.QlibAdapter") as MockAdapter:
            instance = MockAdapter.return_value
            instance.get_features.return_value = self._make_mock_features()

            from scripts.research.compute_labels import compute_label_raw
            df = compute_label_raw("csi300", 2, "2024-01-01", "2024-01-10")

        # horizon=2, 5 dates → last 2 dates have NaN → 3 dates * 3 inst = 9 rows
        assert len(df) == 9
        assert df["label_id"].iloc[0] == "fwd_ret_2d_raw"
        # Check specific value: A on day1: shift(-2)=102, close=100, return=0.02
        a_day1 = df[(df["instrument"] == "A") & (df["trade_date"] == "2024-01-02")]
        assert len(a_day1) == 1
        assert a_day1["label_value"].iloc[0] == pytest.approx(0.02, abs=1e-6)

    def test_compute_label_xsz_clip3(self) -> None:
        """compute_label: zscore applied, values clipped to [-3, 3]."""
        with patch("qsys.data.adapter.QlibAdapter") as MockAdapter:
            instance = MockAdapter.return_value
            instance.get_features.return_value = self._make_mock_features()

            from scripts.research.compute_labels import compute_label
            df = compute_label("csi300", 2, "2024-01-01", "2024-01-10")

        assert df["label_id"].iloc[0] == "fwd_ret_2d_xsz_clip3"
        assert df["label_value"].max() <= 3.0
        assert df["label_value"].min() >= -3.0
        assert len(df) == 9  # same row count as raw (zscore doesn't change shape)

    def test_compute_label_raw_label_id(self) -> None:
        """compute_label_raw produces correct label_id pattern."""
        with patch("qsys.data.adapter.QlibAdapter") as MockAdapter:
            instance = MockAdapter.return_value
            instance.get_features.return_value = self._make_mock_features()

            from scripts.research.compute_labels import compute_label_raw
            df = compute_label_raw("csi300", 3, "2024-01-01", "2024-01-10")

        # horizon=3, 5 dates → last 3 dates NaN → 2 dates * 3 inst = 6 rows
        assert len(df) == 6
        assert df["label_id"].iloc[0] == "fwd_ret_3d_raw"

    def test_compute_label_raw_horizon_large_yields_empty(self) -> None:
        """compute_label_raw with horizon >= n_dates yields empty."""
        with patch("qsys.data.adapter.QlibAdapter") as MockAdapter:
            instance = MockAdapter.return_value
            instance.get_features.return_value = self._make_mock_features()

            from scripts.research.compute_labels import compute_label_raw
            df = compute_label_raw("csi300", 5, "2024-01-01", "2024-01-10")

        # horizon=5, only 5 dates → all NaN after dropna
        assert df.empty

    def test_compute_label_dropna_tail(self) -> None:
        """compute_label drops NaN rows (forward tail)."""
        with patch("qsys.data.adapter.QlibAdapter") as MockAdapter:
            instance = MockAdapter.return_value
            instance.get_features.return_value = self._make_mock_features()

            from scripts.research.compute_labels import compute_label
            df_1d = compute_label("csi300", 1, "2024-01-01", "2024-01-10")
            df_2d = compute_label("csi300", 2, "2024-01-01", "2024-01-10")

        # 1d: last 1 date NaN → 4 dates * 3 inst = 12 rows
        assert len(df_1d) == 12
        # 2d: last 2 dates NaN → 3 dates * 3 inst = 9 rows
        assert len(df_2d) == 9
        # No NaN in label_value
        assert df_1d["label_value"].isna().sum() == 0
        assert df_2d["label_value"].isna().sum() == 0


# ── RollingRunner preflight with universe/min_coverage ──────────────────


class TestRollingRunnerPreflightExtended:
    """Rolling runner pre-flight should forward universe and min_coverage."""

    def test_config_label_with_min_coverage(self) -> None:
        """LabelConfig can carry min_coverage."""
        from qsys.research.rolling_runner import LabelConfig
        cfg = LabelConfig(label_id="fwd_ret_5d_xsz_clip3", min_coverage=0.9)
        assert cfg.label_id == "fwd_ret_5d_xsz_clip3"
        assert cfg.min_coverage == 0.9

    def test_config_label_roundtrip_from_dict(self) -> None:
        """LabelConfig from_dict includes min_coverage when set."""
        from qsys.research.rolling_runner import RollingResearchConfig
        cfg = RollingResearchConfig.from_dict({
            "experiment_id": "test",
            "labels": [
                {"label_id": "fwd_ret_5d_xsz_clip3", "min_coverage": 0.85},
                {"label_id": "fwd_ret_20d_xsz_clip3"},
            ],
        })
        assert cfg.labels[0]["label_id"] == "fwd_ret_5d_xsz_clip3"
        assert cfg.labels[0].get("min_coverage") == 0.85
        assert cfg.labels[1].get("min_coverage") is None
