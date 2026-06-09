"""Golden test for AlphaV1ExistingGenerator — lock input→output.

Mocks the underlying adapter to produce deterministic predictions,
then asserts exact score values from the generator pipeline.
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest


class _FakeAdapter:
    """Deterministic adapter stand-in."""

    def generate_predictions_for_date(self, trade_date: str) -> pd.DataFrame:
        instruments = ["000001.SZ", "000002.SZ"]
        scores = [0.5, -0.3]
        return pd.DataFrame({
            "instrument": instruments,
            "score": scores,
            "trade_date": trade_date,
        })


class TestAlphaV1ExistingGolden:
    """Lock AlphaV1ExistingGenerator output from deterministic adapter."""

    @patch("qsys.research.generators.alpha_v1_existing._resolve_adapter")
    def test_golden_scores(self, mock_adapter_factory) -> None:
        from qsys.research.generators.alpha_v1_existing import (
            AlphaV1ExistingGenerator,
        )

        mock_adapter_factory.return_value = _FakeAdapter()
        gen = AlphaV1ExistingGenerator()
        with patch.object(gen, "_get_adapter") as mock_get:
            mock_get.return_value = _FakeAdapter()
            result = gen.generate(
                train_start="2026-01-01",
                train_end="2026-01-10",
                predict_start="2026-01-13",
                predict_end="2026-01-15",
                signal_id="alpha_golden",
                signal_run_id="run_v1",
            )
        result = result.sort_values(["trade_date", "instrument"]).reset_index(drop=True)

        assert len(result) == 6  # 3 dates × 2 instruments
        required = {"trade_date", "data_date", "instrument", "signal_id", "signal_run_id", "score"}
        assert required.issubset(set(result.columns))

        # Adapter returns scores [0.5, -0.3] per date, same across dates.
        # No transform applied (raw pass-through).
        expected = [
            ("2026-01-13", "000001.SZ", 0.5),
            ("2026-01-13", "000002.SZ", -0.3),
            ("2026-01-14", "000001.SZ", 0.5),
            ("2026-01-14", "000002.SZ", -0.3),
            ("2026-01-15", "000001.SZ", 0.5),
            ("2026-01-15", "000002.SZ", -0.3),
        ]
        for i, (td, inst, exp_score) in enumerate(expected):
            row = result.iloc[i]
            assert row["trade_date"] == td, f"row {i}: trade_date mismatch"
            assert row["instrument"] == inst, f"row {i}: instrument mismatch"
            assert abs(row["score"] - exp_score) < 1e-9, (
                f"row {i} {td} {inst}: {row['score']} != {exp_score}"
            )

    @patch("qsys.research.generators.alpha_v1_existing._resolve_adapter")
    def test_data_date_always_before_trade_date(self, mock_adapter_factory) -> None:
        """Ensure data_date < trade_date for every row (no lookahead)."""
        from qsys.research.generators.alpha_v1_existing import (
            AlphaV1ExistingGenerator,
        )

        mock_adapter_factory.return_value = _FakeAdapter()
        gen = AlphaV1ExistingGenerator()
        with patch.object(gen, "_get_adapter") as mock_get:
            mock_get.return_value = _FakeAdapter()
            result = gen.generate(
                train_start="2026-01-01",
                train_end="2026-01-10",
                predict_start="2026-01-13",
                predict_end="2026-01-15",
                signal_id="alpha_golden",
                signal_run_id="run_v1",
            )
        for _, row in result.iterrows():
            assert row["data_date"] < row["trade_date"], (
                f"data_date {row['data_date']} >= trade_date {row['trade_date']}"
            )
