"""Tests for ProbabilityCalibrator — F02: sigmoid branch must return
continuous calibrated probabilities, not hard 0/1 class labels."""

from __future__ import annotations

import numpy as np
import pytest

from qsys.signal.alpha_v1.calibrate import ProbabilityCalibrator


def _make_data(n: int = 2000, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """Raw probabilities with a real (monotone) relationship to a binary label."""
    rng = np.random.default_rng(seed)
    margin = rng.normal(size=n)
    p = 1.0 / (1.0 + np.exp(-(margin * 1.5)))
    y = (rng.random(n) < p).astype(int)
    prob = np.clip(p, 1e-6, 1 - 1e-6)
    return prob, y


class TestSigmoidCalibrator:
    def test_sigmoid_predict_is_continuous(self) -> None:
        """Sigmoid (Platt) branch returns continuous probabilities, not 0/1."""
        prob, y = _make_data()
        cal = ProbabilityCalibrator(method="sigmoid", use_margin=False)
        cal.fit(prob, y)
        out = cal.predict(prob)
        assert out.shape == prob.shape
        assert np.unique(out).size > 2  # F02: degenerate 0/1 would fail here
        assert ((out > 0) & (out < 1)).mean() > 0.9

    def test_sigmoid_predict_monotonic_in_raw_prob(self) -> None:
        """Platt scaling is monotonic in the logit of raw probability."""
        prob, y = _make_data()
        cal = ProbabilityCalibrator(method="sigmoid", use_margin=False)
        cal.fit(prob, y)
        order = np.argsort(prob)
        out = cal.predict(prob)[order]
        assert (np.diff(out) >= -1e-9).all()

    def test_sigmoid_requires_fit(self) -> None:
        cal = ProbabilityCalibrator(method="sigmoid")
        with pytest.raises(RuntimeError, match="fit"):
            cal.predict(np.array([0.5]))


class TestIsotonicCalibrator:
    def test_isotonic_predict_is_continuous(self) -> None:
        prob, y = _make_data()
        cal = ProbabilityCalibrator(method="isotonic", use_margin=False)
        cal.fit(prob, y)
        out = cal.predict(prob)
        assert np.unique(out).size > 2
        assert ((out >= 0) & (out <= 1)).all()
