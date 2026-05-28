"""Tests for qsys.strategy.allocation.schema."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from qsys.strategy.allocation.schema import (
    REQUIRED_TARGET_WEIGHT_COLUMNS,
    add_metadata_columns,
    validate_target_weights,
)


def _valid_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "trade_date": ["2026-06-15", "2026-06-15"],
        "instrument": ["000001.SZ", "000002.SZ"],
        "target_weight": [0.6, 0.4],
    })


class TestRequiredColumns:
    def test_valid_passes(self):
        validate_target_weights(_valid_frame())

    def test_missing_column_fails(self):
        bad = _valid_frame().drop(columns=["target_weight"])
        with pytest.raises(ValueError, match="Missing required columns"):
            validate_target_weights(bad)

    def test_empty_fails_by_default(self):
        with pytest.raises(ValueError, match="empty"):
            validate_target_weights(pd.DataFrame())

    def test_empty_allow_empty(self):
        validate_target_weights(pd.DataFrame(), allow_empty=True)  # no error


class TestNullValues:
    def test_null_trade_date_fails(self):
        frame = _valid_frame()
        frame.loc[0, "trade_date"] = None
        with pytest.raises(ValueError, match="null"):
            validate_target_weights(frame)

    def test_null_instrument_fails(self):
        frame = _valid_frame()
        frame.loc[0, "instrument"] = None
        with pytest.raises(ValueError, match="null"):
            validate_target_weights(frame)

    def test_null_weight_fails(self):
        frame = _valid_frame()
        frame.loc[0, "target_weight"] = None
        with pytest.raises(ValueError, match="null"):
            validate_target_weights(frame)


class TestDuplicates:
    def test_duplicate_raises(self):
        frame = pd.DataFrame({
            "trade_date": ["2026-06-15", "2026-06-15"],
            "instrument": ["000001.SZ", "000001.SZ"],
            "target_weight": [0.5, 0.3],
        })
        with pytest.raises(ValueError, match="Duplicate"):
            validate_target_weights(frame)


class TestWeightConstraints:
    def test_negative_fails(self):
        frame = _valid_frame()
        frame.loc[0, "target_weight"] = -0.1
        with pytest.raises(ValueError, match="negative"):
            validate_target_weights(frame)

    def test_non_finite_fails(self):
        frame = _valid_frame()
        frame.loc[0, "target_weight"] = float("inf")
        with pytest.raises(ValueError, match="NaN|non-finite"):
            validate_target_weights(frame)

    def test_sum_over_one_fails(self):
        frame = _valid_frame()
        frame.loc[0, "target_weight"] = 0.7
        frame.loc[1, "target_weight"] = 0.7
        with pytest.raises(ValueError, match="exceeds 1.0"):
            validate_target_weights(frame)

    def test_sum_at_one_passes(self):
        frame = pd.DataFrame({
            "trade_date": ["2026-06-15", "2026-06-15", "2026-06-15"],
            "instrument": ["a", "b", "c"],
            "target_weight": [0.5, 0.3, 0.2],
        })
        validate_target_weights(frame)  # sum = 1.0


class TestAddMetadataColumns:
    def test_adds_columns(self):
        frame = _valid_frame()
        add_metadata_columns(frame, allocation_method="rank_weight", strategy_id="s1")
        assert "allocation_method" in frame.columns
        assert frame["allocation_method"].iloc[0] == "rank_weight"
        assert frame["strategy_id"].iloc[0] == "s1"
