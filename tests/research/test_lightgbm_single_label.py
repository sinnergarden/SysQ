"""Tests for LightGBMSingleLabelGenerator — contract and output format."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pandas as pd
import pytest

from qsys.research.generators.lightgbm_single_label import (
    LightGBMSingleLabelGenerator,
)


class FakeModel:
    """Minimal LightGBM model stand-in for monkeypatched train_model."""

    def predict(self, X: Any) -> list[float]:
        return [0.1 * i for i in range(len(X))]


def _fake_train_model(X_train, y_train, tag, **kw):
    return FakeModel(), pd.Series([1.0] * X_train.shape[1]), pd.Series([0.0] * X_train.shape[1])


def _fake_predict_model(model, center, scale, X):
    return pd.Series(model.predict(X), index=X.index)


class TestLightGBMSingleLabelContract:
    """Verify output matches SignalStore 6-column contract."""

    @patch("qsys.signal.alpha_v1.training.train_model", _fake_train_model)
    @patch("qsys.signal.alpha_v1.training.predict_model", _fake_predict_model)
    def test_generate_returns_valid_signalrun(self) -> None:
        """generate() output has required columns."""
        gen = LightGBMSingleLabelGenerator(
            label_id="fwd_ret_5d_xsz_clip3",
        )
        with patch.object(gen, "_load_data") as mock_load:
            mock_load.return_value = self._make_fake_data(), ["f1", "f2"]
            with patch.object(gen, "_ensure_qlib"):
                result = gen.generate(
                    train_start="2026-01-01",
                    train_end="2026-01-10",
                    predict_start="2026-01-13",
                    predict_end="2026-01-15",
                    signal_id="lgbm_test",
                    signal_run_id="run1",
                )
        assert isinstance(result, pd.DataFrame)
        required = {"trade_date", "data_date", "instrument", "signal_id", "signal_run_id", "score"}
        assert required.issubset(set(result.columns)), f"Missing columns: {required - set(result.columns)}"
        assert len(result) > 0
        assert (result["signal_id"] == "lgbm_test").all()
        assert (result["signal_run_id"] == "run1").all()
        assert result["score"].notna().all()

    @patch("qsys.signal.alpha_v1.training.train_model", _fake_train_model)
    @patch("qsys.signal.alpha_v1.training.predict_model", _fake_predict_model)
    def test_generate_output_filtered_by_predict_window(self) -> None:
        """generate() only returns dates in predict window."""
        gen = LightGBMSingleLabelGenerator(label_id="fwd_ret_5d_xsz_clip3")
        with patch.object(gen, "_load_data") as mock_load:
            mock_load.return_value = self._make_fake_data(), ["f1", "f2"]
            with patch.object(gen, "_ensure_qlib"):
                result = gen.generate(
                    train_start="2026-01-01",
                    train_end="2026-01-10",
                    predict_start="2026-01-14",
                    predict_end="2026-01-15",
                    signal_id="s", signal_run_id="r",
                )
        dates = sorted(result["trade_date"].unique())
        assert all(d >= "2026-01-14" for d in dates)
        assert all(d <= "2026-01-15" for d in dates)

    @staticmethod
    def _make_fake_data() -> pd.DataFrame:
        dates = [f"2026-01-{d:02d}" for d in range(2, 17)]
        rows = []
        for td in dates:
            for inst in ["000001.SZ", "000002.SZ", "000003.SZ"]:
                rows.append({
                    "trade_date": td, "instrument": inst,
                    "f1": 1.0, "f2": 2.0, "$close": 100.0,
                })
        return pd.DataFrame(rows)


class TestLightGBMSingleLabelLabelStore:
    """Verify label store integration is correct (not computed inline)."""

    def test_generate_imports_labelstore_not_make_zs_label(self) -> None:
        import inspect
        src = inspect.getsource(LightGBMSingleLabelGenerator.generate)
        assert "LabelStore" in src
        assert "load_labels" in src
        assert "make_zs_label" not in src


class TestExpandMultiLabelConfig:
    """Verify multi_label_lightgbm expansion logic."""

    def test_expand_single_multi_label(self) -> None:
        from qsys.research.rolling_runner import expand_multi_label_generators

        configs = [
            {
                "generator_id": "multi",
                "type": "multi_label_lightgbm",
                "params": {
                    "universe": "csi800",
                    "labels": [
                        {"label_id": "fwd_ret_5d_xsz_clip3", "signal_id": "lgbm_5d"},
                        {"label_id": "fwd_ret_20d_xsz_clip3", "signal_id": "lgbm_20d"},
                    ],
                },
            },
        ]
        result = expand_multi_label_generators(configs)
        assert len(result) == 2
        assert result[0]["generator_id"] == "multi__fwd_ret_5d_xsz_clip3"
        assert result[0]["type"] == "single_label_lightgbm"
        assert result[0]["label_signal_id"] == "lgbm_5d"
        assert result[0]["params"]["label_id"] == "fwd_ret_5d_xsz_clip3"
        assert result[0]["params"]["universe"] == "csi800"
        assert result[1]["generator_id"] == "multi__fwd_ret_20d_xsz_clip3"
        assert result[1]["label_signal_id"] == "lgbm_20d"

    def test_non_multi_labels_passthrough(self) -> None:
        from qsys.research.rolling_runner import expand_multi_label_generators

        configs = [
            {"generator_id": "g1", "type": "technical_composite"},
            {"generator_id": "g2", "type": "single_label_lightgbm", "params": {"label_id": "l1"}},
        ]
        result = expand_multi_label_generators(configs)
        assert len(result) == 2
        assert result[0]["generator_id"] == "g1"
        assert result[1]["generator_id"] == "g2"

    def test_empty_labels_raises(self) -> None:
        from qsys.research.rolling_runner import expand_multi_label_generators

        with pytest.raises(ValueError, match="labels"):
            expand_multi_label_generators([
                {"generator_id": "bad", "type": "multi_label_lightgbm", "params": {}},
            ])

    def test_expand_without_signal_id_falls_back(self) -> None:
        from qsys.research.rolling_runner import expand_multi_label_generators

        configs = [
            {
                "generator_id": "m",
                "type": "multi_label_lightgbm",
                "params": {
                    "labels": [{"label_id": "fwd_ret_5d_xsz_clip3"}],
                },
            },
        ]
        result = expand_multi_label_generators(configs)
        assert result[0]["label_signal_id"] == "fwd_ret_5d_xsz_clip3"


class TestSingleLabelFactory:
    """Verify single_label_lightgbm is created correctly."""

    def test_factory_creates_single_label_generator(self) -> None:
        from qsys.research.rolling_runner import _create_generator_from_config
        from qsys.research.generators.lightgbm_single_label import LightGBMSingleLabelGenerator

        gen = _create_generator_from_config({
            "generator_id": "g",
            "type": "single_label_lightgbm",
            "params": {
                "label_id": "fwd_ret_5d_xsz_clip3",
                "universe": "csi800",
                "n_estimators": 100,
            },
        })
        assert isinstance(gen, LightGBMSingleLabelGenerator)
        assert gen.label_id == "fwd_ret_5d_xsz_clip3"
        assert gen.universe == "csi800"
        assert gen.n_estimators == 100
