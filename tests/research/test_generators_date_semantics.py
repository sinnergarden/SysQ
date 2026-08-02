"""F01 date-semantics tests for lightgbm_binary and lightgbm_alpha_v1.

Verifies backward-shift emission (data_date < trade_date, output stays inside
the execution window) and the training-label maturity gate.  The
single-label generator's date semantics are covered in
test_lightgbm_single_label.py.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest


def _business_days(start: str, end: str) -> list[str]:
    out: list[str] = []
    cur = dt.datetime.strptime(start, "%Y-%m-%d")
    endd = dt.datetime.strptime(end, "%Y-%m-%d")
    while cur <= endd:
        if cur.weekday() < 5:
            out.append(cur.strftime("%Y-%m-%d"))
        cur += dt.timedelta(days=1)
    return out


def _fake_calendar(start: str, end: str) -> list[str]:
    return _business_days(start, end)


def _make_frame(start: str, end: str) -> pd.DataFrame:
    rows = []
    for td in _business_days(start, end):
        for inst in ["000001.SZ", "000002.SZ", "000003.SZ"]:
            rows.append({"trade_date": td, "instrument": inst, "f1": 1.0, "f2": 2.0, "$close": 100.0})
    return pd.DataFrame(rows)


def _fake_labels(label_id: str, start: str, end: str, *, binary: bool = False) -> pd.DataFrame:
    rows = []
    inst_labels = {"000001.SZ": 0.01, "000002.SZ": 0.02, "000003.SZ": 0.03}
    for td in _business_days(start, end):
        for inst, val in inst_labels.items():
            lv = float((_business_days(start, end).index(td) + 1) % 2) if binary else val
            rows.append({"trade_date": td, "instrument": inst, "label_id": label_id, "horizon": 5, "label_value": lv})
    return pd.DataFrame(rows)


class _FakeModel:
    def predict(self, X):
        return np.array([0.3] * len(X))


def _fake_train_model(X, y, tag, **kw):
    return _FakeModel(), pd.Series([1.0] * X.shape[1]), pd.Series([0.0] * X.shape[1])


def _fake_predict_model(model, center, scale, X, **kw):
    return pd.Series([0.3] * len(X), index=X.index)


class TestLightGBMBinaryDateSemantics:
    """Backward-shift emission + maturity gate for the binary generator."""

    def _gen(self):
        from qsys.research.generators.lightgbm_binary import LightGBMBinaryGenerator

        return LightGBMBinaryGenerator(label_id="fwd_maxdd_5d_binary_5pct")

    def test_backward_shift_no_lookahead(self) -> None:
        with patch("qsys.data.calendar.get_trading_calendar", _fake_calendar), \
             patch("qsys.signal.alpha_v1.training.train_model", _fake_train_model), \
             patch("qsys.signal.alpha_v1.training.predict_model", _fake_predict_model), \
             patch("qsys.label.store.LabelStore.load_labels") as mock_labels:
            mock_labels.return_value = _fake_labels("fwd_maxdd_5d_binary_5pct", "2026-01-02", "2026-01-22", binary=True)
            gen = self._gen()
            with patch.object(gen, "_load_data") as mock_load, patch.object(gen, "_ensure_qlib"):
                mock_load.return_value = _make_frame("2026-01-02", "2026-01-22"), ["f1", "f2"]
                result = gen.generate(
                    train_start="2026-01-02", train_end="2026-01-10",
                    predict_start="2026-01-20", predict_end="2026-01-22",
                    signal_id="b", signal_run_id="r",
                )
        assert len(result) > 0
        # F01: feature date strictly before trade_date, output inside window.
        assert (result["data_date"] < result["trade_date"]).all()
        assert set(result["trade_date"]) <= {"2026-01-20", "2026-01-21", "2026-01-22"}

    def test_maturity_gate_raises_when_lag_too_small(self) -> None:
        with patch("qsys.data.calendar.get_trading_calendar", _fake_calendar), \
             patch("qsys.signal.alpha_v1.training.train_model", _fake_train_model), \
             patch("qsys.signal.alpha_v1.training.predict_model", _fake_predict_model), \
             patch("qsys.label.store.LabelStore.load_labels") as mock_labels:
            mock_labels.return_value = _fake_labels("fwd_maxdd_5d_binary_5pct", "2026-01-02", "2026-01-22", binary=True)
            gen = self._gen()
            with patch.object(gen, "_load_data") as mock_load, patch.object(gen, "_ensure_qlib"):
                mock_load.return_value = _make_frame("2026-01-02", "2026-01-22"), ["f1", "f2"]
                with pytest.raises(ValueError, match="maturity"):
                    gen.generate(
                        train_start="2026-01-02", train_end="2026-01-16",  # too close to predict_start
                        predict_start="2026-01-20", predict_end="2026-01-22",
                        signal_id="b", signal_run_id="r",
                    )


class TestTrainingLabelMaturityGate:
    """Regression: shifted vs non-shifted critical trading-day gaps (F01 review 3)."""

    @staticmethod
    def _run(train_end: str, predict_start: str, horizon: int, shifted: bool):
        from qsys.research.generators.utils import check_training_label_maturity

        with patch("qsys.data.calendar.get_trading_calendar", _fake_calendar):
            return check_training_label_maturity(train_end, predict_start, horizon, shifted=shifted)

    def test_shifted_requires_gap_h_plus_2(self) -> None:
        # shifted (forward-return): needs gap >= horizon+2.
        with pytest.raises(ValueError, match="maturity"):
            self._run("2026-01-08", "2026-01-16", 5, shifted=True)  # gap=6 < 7
        assert self._run("2026-01-07", "2026-01-16", 5, shifted=True) == 7  # gap=7 OK

    def test_unshifted_requires_gap_h_plus_1(self) -> None:
        # non-shifted (maxdd): needs gap >= horizon+1.
        with pytest.raises(ValueError, match="maturity"):
            self._run("2026-01-09", "2026-01-16", 5, shifted=False)  # gap=5 < 6
        assert self._run("2026-01-08", "2026-01-16", 5, shifted=False) == 6  # gap=6 OK

    def test_binary_training_pairs_feature_date_with_fwd_maxdd(self) -> None:
        """Binary training must merge labels on the SAME trade_date (no
        next_td shift), because fwd_maxdd[d] already spans [d+1, d+h]."""
        import inspect

        from qsys.research.generators.lightgbm_binary import LightGBMBinaryGenerator

        src = inspect.getsource(LightGBMBinaryGenerator.generate)
        assert "label_date" not in src, "binary training must not shift labels"
        assert 'on=["trade_date", "instrument"]' in src


class TestLightGBMAlphaV1DateSemantics:
    """Backward-shift emission + maturity gate for the multi-label generator."""

    LABEL_IDS = ("fwd_ret_5d_xsz_clip3", "fwd_ret_20d_xsz_clip3")

    def _gen(self):
        from qsys.research.generators.lightgbm_alpha_v1 import LightGBMAlphaV1Generator

        return LightGBMAlphaV1Generator(label_ids=self.LABEL_IDS, blend_weights={"5d": 0.5, "20d": 0.5})

    def test_backward_shift_no_lookahead(self) -> None:
        with patch("qsys.data.calendar.get_trading_calendar", _fake_calendar), \
             patch("qsys.signal.alpha_v1.training.train_model", _fake_train_model), \
             patch("qsys.signal.alpha_v1.training.predict_model", _fake_predict_model), \
             patch("qsys.label.store.LabelStore.load_labels") as mock_labels:
            mock_labels.side_effect = lambda lid: _fake_labels(lid, "2026-01-02", "2026-02-24")
            gen = self._gen()
            with patch.object(gen, "_load_data") as mock_load, patch.object(gen, "_ensure_qlib"):
                mock_load.return_value = _make_frame("2026-01-02", "2026-02-24"), ["f1", "f2"]
                result = gen.generate(
                    train_start="2026-01-02", train_end="2026-01-10",
                    predict_start="2026-02-20", predict_end="2026-02-24",
                    signal_id="a", signal_run_id="r",
                )
        assert len(result) > 0
        assert (result["data_date"] < result["trade_date"]).all()
        assert set(result["trade_date"]) <= {"2026-02-20", "2026-02-23", "2026-02-24"}

    def test_maturity_gate_uses_max_horizon(self) -> None:
        # 20d label -> requires >= 22 trading days gap; a 7-day gap must raise.
        with patch("qsys.data.calendar.get_trading_calendar", _fake_calendar), \
             patch("qsys.signal.alpha_v1.training.train_model", _fake_train_model), \
             patch("qsys.signal.alpha_v1.training.predict_model", _fake_predict_model), \
             patch("qsys.label.store.LabelStore.load_labels") as mock_labels:
            mock_labels.side_effect = lambda lid: _fake_labels(lid, "2026-01-02", "2026-02-24")
            gen = self._gen()
            with patch.object(gen, "_load_data") as mock_load, patch.object(gen, "_ensure_qlib"):
                mock_load.return_value = _make_frame("2026-01-02", "2026-02-24"), ["f1", "f2"]
                with pytest.raises(ValueError, match="maturity"):
                    gen.generate(
                        train_start="2026-01-02", train_end="2026-01-10",
                        predict_start="2026-01-20", predict_end="2026-01-22",  # gap only 7 < 22
                        signal_id="a", signal_run_id="r",
                    )
