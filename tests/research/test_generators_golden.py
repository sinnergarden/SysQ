"""Golden tests for signal generators — lock input→output.

These tests use deterministic inputs and assert exact score values.
A change in any score beyond the tolerance means the generator's
numerical output has changed — intentional or not.

See Also
--------
qsys/research/generators/base.py — RollingSignalGenerator Protocol
qsys/research/generators/technical_composite.py
qsys/research/generators/lightgbm_alpha_v1.py — blend logic
"""

from __future__ import annotations

import pandas as pd
import pytest


class TestTechnicalCompositeV1Golden:
    """Deterministic OHLCV → TechnicalCompositeV1 scores."""

    @pytest.fixture
    def ohlcv(self) -> pd.DataFrame:
        dates = ["2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22"]
        rows = []
        for day_idx, d in enumerate(dates):
            for inst, base in [("A.SZ", 100.0), ("B.SZ", 50.0), ("C.SZ", 80.0)]:
                close = base + day_idx * 2
                rows.append({
                    "trade_date": d,
                    "instrument": inst,
                    "close": float(close),
                    "open": float(close - 0.5),
                    "high": float(close + 1.0),
                    "low": float(close - 1.0),
                    "volume": float(1_000_000 + day_idx * 100_000),
                })
        return pd.DataFrame(rows)

    def test_golden_scores(self, ohlcv: pd.DataFrame) -> None:
        from qsys.research.generators.technical_composite import (
            TechnicalCompositeV1Generator,
        )

        gen = TechnicalCompositeV1Generator(
            data_loader=lambda **kw: ohlcv,
            momentum_short=2,
            momentum_long=4,
            reversal_days=2,
            volatility_days=2,
            volume_short=2,
            volume_long=3,
        )
        result = gen.generate(
            train_start="2026-01-01",
            train_end="2026-05-17",
            predict_start="2026-05-20",
            predict_end="2026-05-22",
            signal_id="golden",
            signal_run_id="v1",
        )
        result = result.sort_values(["trade_date", "instrument"]).reset_index(drop=True)

        assert len(result) == 9
        assert list(result.columns) == [
            "trade_date", "data_date", "instrument",
            "signal_id", "signal_run_id", "score",
        ]

        # Each row's score must match the golden record
        expected = [
            ("2026-05-20", "A.SZ", 0.3000000000),
            ("2026-05-20", "B.SZ", 0.3000000000),
            ("2026-05-20", "C.SZ", 0.3000000000),
            ("2026-05-21", "A.SZ", 0.3000000000),
            ("2026-05-21", "B.SZ", 0.3000000000),
            ("2026-05-21", "C.SZ", 0.3000000000),
            ("2026-05-22", "A.SZ", -0.0333333333),
            ("2026-05-22", "B.SZ", 0.3000000000),
            ("2026-05-22", "C.SZ", 0.1333333333),
        ]
        for i, (td, inst, exp_score) in enumerate(expected):
            row = result.iloc[i]
            assert row["trade_date"] == td, f"row {i}: trade_date mismatch"
            assert row["instrument"] == inst, f"row {i}: instrument mismatch"
            assert abs(row["score"] - exp_score) < 1e-9, (
                f"row {i} {td} {inst}: score {row['score']:.10f} != {exp_score:.10f}"
            )


class TestBlendGoldens:
    """Lock the inline cs_zscore + weighted blend logic.

    This replicates the exact blend path used by
    ``LightGBMAlphaV1Generator.generate()`` after the
    ``compute_signal`` was inlined.
    """

    def _cs_zscore(self, s: pd.Series, clip: float = 3.0) -> pd.Series:
        std = s.std(ddof=0)
        if pd.isna(std) or std < 1e-12:
            return pd.Series(0.0, index=s.index)
        return ((s - s.mean()) / std).clip(-clip, clip)

    def test_cs_zscore_constant(self) -> None:
        """All-equal input → all-zero zscore."""
        s = pd.Series([5.0, 5.0, 5.0])
        result = self._cs_zscore(s)
        assert (result == 0.0).all()

    def test_cs_zscore_identity(self) -> None:
        """Known values"""
        s = pd.Series([1.0, 2.0, 3.0])
        result = self._cs_zscore(s)
        # mean=2, std=0.816, so [-1.22, 0, 1.22]
        assert abs(result.iloc[0] - (-1.224744871)) < 1e-9
        assert abs(result.iloc[1] - 0.0) < 1e-9
        assert abs(result.iloc[2] - 1.224744871) < 1e-9

    def test_blend_weights_80_20(self) -> None:
        """0.8 * cs_zscore(5d) + 0.2 * cs_zscore(20d) — default blend."""
        instruments = ["A.SZ", "B.SZ", "C.SZ", "D.SZ", "E.SZ"]
        pred_5d = pd.Series([0.01, 0.02, 0.03, 0.04, 0.05], index=instruments)
        pred_20d = pd.Series([0.005, 0.01, 0.02, 0.03, 0.04], index=instruments)

        z5 = self._cs_zscore(pred_5d)
        z20 = self._cs_zscore(pred_20d)
        blended = 0.8 * z5.values + 0.2 * z20.values

        expected = [
            ("A.SZ", -1.3812488689),
            ("B.SZ", -0.7374765630),
            ("C.SZ", -0.0156173762),
            ("D.SZ", 0.7062418106),
            ("E.SZ", 1.4281009975),
        ]
        for i, (inst, exp) in enumerate(expected):
            assert instruments[i] == inst
            assert abs(blended[i] - exp) < 1e-9, (
                f"{inst}: {blended[i]:.10f} != {exp:.10f}"
            )

    def test_blend_weights_equal(self) -> None:
        """0.5/0.5 — equal weight (DNN default)."""
        instruments = ["A", "B", "C"]
        # Same zscore result for both → blended same as either
        pred_a = pd.Series([1.0, 2.0, 3.0], index=instruments)
        pred_b = pd.Series([1.0, 2.0, 3.0], index=instruments)

        z_a = self._cs_zscore(pred_a)
        z_b = self._cs_zscore(pred_b)
        blended = (0.5 * z_a + 0.5 * z_b).values

        # Same signal → blend = same zscore
        assert abs(blended[0] - (-1.224744871)) < 1e-9
