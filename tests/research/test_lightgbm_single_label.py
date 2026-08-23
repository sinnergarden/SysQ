"""Tests for LightGBMSingleLabelGenerator — contract and output format."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pandas as pd
import pytest

from qsys.research.generators.lightgbm_single_label import (
    LightGBMSingleLabelGenerator,
)
from qsys.signal.store import FEATURE_VISIBILITY_CONTRACT_V1


def test_generator_declares_feature_visibility_contract() -> None:
    generator = LightGBMSingleLabelGenerator()
    assert (
        generator.feature_visibility_contract
        == FEATURE_VISIBILITY_CONTRACT_V1
    )


class FakeModel:
    """Minimal LightGBM model stand-in for monkeypatched train_model."""

    def predict(self, X: Any) -> list[float]:
        return [0.1 * i for i in range(len(X))]


def _fake_train_model(X_train, y_train, tag, **kw):
    return FakeModel(), pd.Series([1.0] * X_train.shape[1]), pd.Series([0.0] * X_train.shape[1])


def _fake_predict_model(model, center, scale, X):
    return pd.Series(model.predict(X), index=X.index)


def _fake_calendar(start: str, end: str) -> list[str]:
    """Deterministic business-day calendar for date-semantics tests."""
    from datetime import datetime, timedelta

    out: list[str] = []
    cur = datetime.strptime(start, "%Y-%m-%d")
    endd = datetime.strptime(end, "%Y-%m-%d")
    while cur <= endd:
        if cur.weekday() < 5:
            out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out


class TestLightGBMSingleLabelContract:
    """Verify output matches SignalStore 6-column contract."""

    @staticmethod
    def _fake_labels(label_id: str = "fwd_ret_5d_xsz_clip3") -> pd.DataFrame:
        """Fake label DataFrame covering same dates and instruments as _make_fake_data."""
        rows = []
        inst_labels = {"000001.SZ": 0.01, "000002.SZ": 0.02, "000003.SZ": 0.03}
        for td in [f"2026-01-{d:02d}" for d in range(2, 23)]:
            for inst, val in inst_labels.items():
                rows.append({
                    "trade_date": td,
                    "instrument": inst,
                    "label_id": label_id,
                    "horizon": 5,
                    "label_value": val,
                })
        return pd.DataFrame(rows)

    @patch("qsys.signal.alpha_v1.training.train_model", _fake_train_model)
    @patch("qsys.signal.alpha_v1.training.predict_model", _fake_predict_model)
    @patch("qsys.label.store.LabelStore.load_labels")
    def test_generate_returns_valid_signalrun(self, mock_labels) -> None:
        """generate() output has required columns."""
        mock_labels.return_value = self._fake_labels()
        gen = LightGBMSingleLabelGenerator(label_id="fwd_ret_5d_xsz_clip3")
        with patch.object(gen, "_load_data") as mock_load:
            mock_load.return_value = self._make_fake_data(), ["f1", "f2"]
            with patch.object(gen, "_ensure_qlib"):
                result = gen.generate(
                    train_start="2026-01-02",
                    train_end="2026-01-10",
                    predict_start="2026-01-20",
                    predict_end="2026-01-22",
                    signal_id="lgbm_test",
                    signal_run_id="run1",
                )
        assert isinstance(result, pd.DataFrame)
        required = {"trade_date", "data_date", "instrument", "signal_id", "signal_run_id", "score"}
        assert required.issubset(set(result.columns)), f"Missing columns: {required - set(result.columns)}"
        assert "score_model_raw" in result.columns
        assert len(result) > 0
        assert (result["signal_id"] == "lgbm_test").all()
        assert (result["signal_run_id"] == "run1").all()
        assert result["score"].notna().all()
        assert result["score_model_raw"].notna().all()

    @patch("qsys.data.calendar.get_trading_calendar", _fake_calendar)
    @patch("qsys.signal.alpha_v1.training.train_model", _fake_train_model)
    @patch("qsys.signal.alpha_v1.training.predict_model", _fake_predict_model)
    @patch("qsys.label.store.LabelStore.load_labels")
    def test_generate_output_filtered_by_predict_window(
        self, mock_labels,
    ) -> None:
        """generate() emits trade_date = next trading day after the feature date."""
        mock_labels.return_value = self._fake_labels()
        gen = LightGBMSingleLabelGenerator(label_id="fwd_ret_5d_xsz_clip3")
        with patch.object(gen, "_load_data") as mock_load:
            mock_load.return_value = self._make_fake_data(), ["f1", "f2"]
            with patch.object(gen, "_ensure_qlib"):
                result = gen.generate(
                    train_start="2026-01-02",
                    train_end="2026-01-10",
                    predict_start="2026-01-20",
                    predict_end="2026-01-21",
                    signal_id="s", signal_run_id="r",
                )
        # execution window 2026-01-20/21 -> feature dates 2026-01-19/20
        dates = sorted(result["trade_date"].unique())
        assert dates == ["2026-01-20", "2026-01-21"], dates
        # F01 invariant: every signal's feature date (data_date) is strictly
        # before its trade_date, and data_date lies just before the window.
        assert (result["data_date"] < result["trade_date"]).all()
        assert result["data_date"].between("2026-01-19", "2026-01-20").all()

    @patch("qsys.data.calendar.get_trading_calendar", _fake_calendar)
    @patch("qsys.signal.alpha_v1.training.train_model", _fake_train_model)
    @patch("qsys.signal.alpha_v1.training.predict_model", _fake_predict_model)
    @patch("qsys.label.store.LabelStore.load_labels")
    def test_generate_no_lookahead_invariant(self, mock_labels) -> None:
        """F01: every emitted signal uses features strictly before trade_date."""
        mock_labels.return_value = self._fake_labels()
        gen = LightGBMSingleLabelGenerator(label_id="fwd_ret_5d_xsz_clip3")
        with patch.object(gen, "_load_data") as mock_load:
            mock_load.return_value = self._make_fake_data(), ["f1", "f2"]
            with patch.object(gen, "_ensure_qlib"):
                result = gen.generate(
                    train_start="2026-01-02", train_end="2026-01-10",
                    predict_start="2026-01-20", predict_end="2026-01-22",
                    signal_id="s", signal_run_id="r",
                )
        assert len(result) > 0
        # Framework invariant (F01): no feature bar at/after trade_date.
        assert (result["data_date"] < result["trade_date"]).all()
        # data_date is a real feature date present in the input frame.
        input_dates = set(self._make_fake_data()["trade_date"])
        assert set(result["data_date"]).issubset(input_dates)

    @staticmethod
    def _make_fake_data() -> pd.DataFrame:
        dates = [f"2026-01-{d:02d}" for d in range(2, 23)]
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


class TestLightGBMSingleLabelGolden:
    """Golden test: deterministic input → exact score values."""

    @patch("qsys.data.calendar.get_trading_calendar", _fake_calendar)
    @patch("qsys.signal.alpha_v1.training.train_model", _fake_train_model)
    @patch("qsys.signal.alpha_v1.training.predict_model", _fake_predict_model)
    @patch("qsys.label.store.LabelStore.load_labels")
    def test_golden_scores(self, mock_labels) -> None:
        """Lock the exact score values from deterministic input."""
        mock_labels.return_value = self._fake_labels()
        gen = LightGBMSingleLabelGenerator(label_id="fwd_ret_5d_xsz_clip3")
        with patch.object(gen, "_load_data") as mock_load:
            mock_load.return_value = self._make_fake_data(), ["f1", "f2"]
            with patch.object(gen, "_ensure_qlib"):
                result = gen.generate(
                    train_start="2026-01-02",
                    train_end="2026-01-10",
                    predict_start="2026-01-20",
                    predict_end="2026-01-22",
                    signal_id="lgbm_golden",
                    signal_run_id="run_golden",
                )
        result = result.sort_values(["trade_date", "instrument"]).reset_index(drop=True)

        # FakeModel.predict returns [0.0, 0.1, 0.2] for 3 instruments.
        # _cs_zscore normalizes this to [-1.2247, 0.0, 1.2247] per feature date.
        # F01 backward-shift: emitted trade_date = execution window days,
        # each using features from the previous trading day.
        # execution days 01-20/21/22 -> feature dates 01-19/20/21.
        assert len(result) == 9  # 3 execution days × 3 instruments
        assert list(result.columns) == [
            "trade_date", "data_date", "instrument",
            "signal_id", "signal_run_id", "score_model_raw", "score",
        ]

        expected = [
            ("2026-01-20", "000001.SZ", -1.224744871),
            ("2026-01-20", "000002.SZ", 0.0),
            ("2026-01-20", "000003.SZ", 1.224744871),
            ("2026-01-21", "000001.SZ", -1.224744871),
            ("2026-01-21", "000002.SZ", 0.0),
            ("2026-01-21", "000003.SZ", 1.224744871),
            ("2026-01-22", "000001.SZ", -1.224744871),
            ("2026-01-22", "000002.SZ", 0.0),
            ("2026-01-22", "000003.SZ", 1.224744871),
        ]
        for i, (td, inst, exp_score) in enumerate(expected):
            row = result.iloc[i]
            assert row["trade_date"] == td, f"row {i}: trade_date mismatch"
            assert row["instrument"] == inst, f"row {i}: instrument mismatch"
            assert abs(row["score"] - exp_score) < 1e-9, (
                f"row {i} {td} {inst}: score {row['score']:.10f} != {exp_score:.10f}"
            )
            assert abs(row["score_model_raw"] - 0.1 * i) < 1e-9

    @staticmethod
    def _fake_labels(label_id: str = "fwd_ret_5d_xsz_clip3") -> pd.DataFrame:
        rows = []
        inst_labels = {"000001.SZ": 0.01, "000002.SZ": 0.02, "000003.SZ": 0.03}
        for td in [f"2026-01-{d:02d}" for d in range(2, 23)]:
            for inst, val in inst_labels.items():
                rows.append({
                    "trade_date": td, "instrument": inst,
                    "label_id": label_id, "horizon": 5, "label_value": val,
                })
        return pd.DataFrame(rows)

    @staticmethod
    def _make_fake_data() -> pd.DataFrame:
        dates = [f"2026-01-{d:02d}" for d in range(2, 23)]
        rows = []
        for td in dates:
            for inst in ["000001.SZ", "000002.SZ", "000003.SZ"]:
                rows.append({
                    "trade_date": td, "instrument": inst,
                    "f1": 1.0, "f2": 2.0, "$close": 100.0,
                })
        return pd.DataFrame(rows)


class TestMultiLabelBuildMatrixJobs:
    """Integration: multi_label_lightgbm → expand → build_matrix_jobs."""

    def test_expand_then_build_produces_independent_jobs(self) -> None:
        from qsys.research.rolling_runner import (
            RollingResearchConfig,
            build_matrix_jobs,
            expand_multi_label_generators,
        )

        config = RollingResearchConfig(
            experiment_id="exp1",
            generators=[
                {
                    "generator_id": "multi",
                    "type": "multi_label_lightgbm",
                    "params": {
                        "universe": "csi300",
                        "labels": [
                            {"label_id": "fwd_ret_5d_xsz_clip3", "signal_id": "lgbm_5d"},
                            {"label_id": "fwd_ret_20d_xsz_clip3", "signal_id": "lgbm_20d"},
                        ],
                    },
                },
            ],
            transforms=[{"transform_id": "raw", "type": "identity"}],
            strategies=[{"strategy_id": "s1", "strategy_template_id": "rank_weight_top20"}],
            calendar={"start_date": "2026-01-01", "end_date": "2026-01-10"},
        )

        effective = expand_multi_label_generators(config.generators)
        jobs = build_matrix_jobs(config, effective_generators=effective)

        assert len(jobs) == 2, f"expected 2 jobs, got {len(jobs)}"

        # Job 0: first label
        assert jobs[0].generator_id == "multi__fwd_ret_5d_xsz_clip3"
        assert jobs[0].transform_id == "raw"
        # signal_id should use the label entry's signal_id (lgbm_5d) + transform
        assert jobs[0].signal_id == "lgbm_5d__raw"

        # Job 1: second label
        assert jobs[1].generator_id == "multi__fwd_ret_20d_xsz_clip3"
        assert jobs[1].signal_id == "lgbm_20d__raw"

        # Both jobs carry the same strategy configs
        assert len(jobs[0].strategy_configs) == 1
        assert len(jobs[1].strategy_configs) == 1

    def test_original_config_not_mutated(self) -> None:
        """expand_multi_label_generators does not modify its input."""
        from qsys.research.rolling_runner import expand_multi_label_generators

        original = [
            {
                "generator_id": "multi",
                "type": "multi_label_lightgbm",
                "params": {
                    "labels": [{"label_id": "fwd_ret_5d_xsz_clip3", "signal_id": "lgbm_5d"}],
                },
            },
        ]
        _ = expand_multi_label_generators(original)
        assert len(original) == 1  # unchanged
        assert original[0]["type"] == "multi_label_lightgbm"


class TestTradingDateLookups:
    """Unit tests for the F01 next-trading-day lookup."""

    def test_next_trading_date_lookup(self) -> None:
        from qsys.research.generators.utils import build_next_trading_date_lookup

        with patch("qsys.data.calendar.get_trading_calendar", _fake_calendar):
            lookup = build_next_trading_date_lookup("2026-01-12", "2026-01-16")
        assert lookup["2026-01-12"] == "2026-01-13"
        assert lookup["2026-01-13"] == "2026-01-14"
        assert lookup["2026-01-15"] == "2026-01-16"
        # The lookup extends past predict_end so the last predict date's next
        # trading day (01-19) is available to the generator.
        assert lookup["2026-01-16"] == "2026-01-19"

    def test_next_skips_weekend(self) -> None:
        from qsys.research.generators.utils import build_next_trading_date_lookup

        with patch("qsys.data.calendar.get_trading_calendar", _fake_calendar):
            lookup = build_next_trading_date_lookup("2026-01-15", "2026-01-19")
        # 01-15 (Thu) -> 01-16 (Fri); 01-16 (Fri) -> 01-19 (Mon)
        assert lookup["2026-01-15"] == "2026-01-16"
        assert lookup["2026-01-16"] == "2026-01-19"
