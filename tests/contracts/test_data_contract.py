"""Contract tests for data-layer validation.

Verifies that:
1. ``assert_no_future_rows`` rejects future-dated data.
2. ``normalize_trade_date`` accepts valid formats and rejects invalid ones.
3. ``validate_market_snapshot`` checks all instruments are present.
4. ``validate_feature_frame`` enforces cutoff and column requirements.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from qsys.data.contracts import (
    assert_no_future_rows,
    normalize_trade_date,
    validate_feature_frame,
    validate_market_snapshot,
)
from qsys.ops.market_snapshot import ShadowRebalanceError


# ── normalize_trade_date ───────────────────────────────────────────────────────


class TestNormalizeTradeDate:
    """Tests for ``normalize_trade_date``."""

    def test_valid_date_passthrough(self):
        assert normalize_trade_date("2026-05-22") == "2026-05-22"

    def test_valid_date_with_different_format(self):
        result = normalize_trade_date("2026-05-22 00:00:00")
        assert result == "2026-05-22"

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError, match="invalid trade date"):
            normalize_trade_date("not-a-date")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            normalize_trade_date("")

    def test_none_raises(self):
        with pytest.raises(ValueError):
            normalize_trade_date(None)  # type: ignore[arg-type]

    def test_timestamp_input(self):
        result = normalize_trade_date("2026-05-22 14:30:00")
        assert result == "2026-05-22"

    def test_date_with_slashes(self):
        result = normalize_trade_date("2026/05/22")
        assert result == "2026-05-22"


# ── assert_no_future_rows ──────────────────────────────────────────────────────


class TestAssertNoFutureRows:
    """Tests for ``assert_no_future_rows``."""

    @pytest.fixture
    def sample_df(self) -> pd.DataFrame:
        return pd.DataFrame({
            "trade_date": ["2026-05-20", "2026-05-21", "2026-05-22"],
            "value": [1, 2, 3],
        })

    def test_no_future_rows_passes(self, sample_df: pd.DataFrame):
        assert_no_future_rows(sample_df, "2026-05-22")  # equal to max → ok

    def test_cutoff_before_data_raises(self, sample_df: pd.DataFrame):
        with pytest.raises(ValueError, match="future row"):
            assert_no_future_rows(sample_df, "2026-05-21")

    def test_missing_date_column_raises(self, sample_df: pd.DataFrame):
        with pytest.raises(ValueError, match="not found"):
            assert_no_future_rows(sample_df, "2026-05-22", date_col="missing")

    def test_empty_df_passes(self):
        empty = pd.DataFrame(columns=["trade_date"])
        assert_no_future_rows(empty, "2026-05-22")  # no rows → no violation

    def test_far_future_date_raises(self, sample_df: pd.DataFrame):
        sample_df.loc[0, "trade_date"] = "2027-01-01"
        with pytest.raises(ValueError, match="future row"):
            assert_no_future_rows(sample_df, "2026-05-22")

    def test_custom_date_col(self):
        df = pd.DataFrame({"my_date": ["2026-05-23", "2026-05-24"]})
        with pytest.raises(ValueError, match="future row"):
            assert_no_future_rows(df, "2026-05-23", date_col="my_date")

    def test_unparseable_dates_raises(self, sample_df: pd.DataFrame):
        sample_df.loc[0, "trade_date"] = "bad-date"
        with pytest.raises(ValueError, match="unparseable"):
            assert_no_future_rows(sample_df, "2026-05-22")


# ── validate_market_snapshot ───────────────────────────────────────────────────


class TestValidateMarketSnapshot:
    """Tests for ``validate_market_snapshot``."""

    @pytest.fixture
    def prices(self) -> dict[str, float]:
        return {"000001.SZ": 10.0, "000002.SZ": 20.0}

    @pytest.fixture
    def status(self) -> pd.DataFrame:
        return pd.DataFrame(
            {"is_suspended": [False, False]},
            index=pd.Index(["000001.SZ", "000002.SZ"], name="instrument"),
        )

    def test_valid_snapshot_passes(self, prices, status):
        validate_market_snapshot(prices, status, ["000001.SZ", "000002.SZ"])

    def test_missing_price_raises(self, prices, status):
        with pytest.raises(ShadowRebalanceError, match="missing prices"):
            validate_market_snapshot(prices, status, ["000001.SZ", "000003.SZ"])

    def test_missing_status_raises(self, prices, status):
        # Add a third instrument that's in prices but not status
        prices["000003.SZ"] = 30.0
        with pytest.raises(ShadowRebalanceError, match="missing status"):
            validate_market_snapshot(prices, status, ["000001.SZ", "000002.SZ", "000003.SZ"])

    def test_empty_instruments_list_passes(self, prices, status):
        validate_market_snapshot({}, pd.DataFrame(index=pd.Index([])), [])

    def test_extra_status_columns_ignored(self, prices, status):
        status["is_limit_up"] = False
        validate_market_snapshot(prices, status, ["000001.SZ", "000002.SZ"])


# ── validate_feature_frame ─────────────────────────────────────────────────────


class TestValidateFeatureFrame:
    """Tests for ``validate_feature_frame``."""

    @pytest.fixture
    def feature_df(self) -> pd.DataFrame:
        return pd.DataFrame({
            "instrument": ["000001.SZ", "000002.SZ"],
            "datetime": ["2026-05-21", "2026-05-22"],
            "feature_1": [0.5, 0.6],
        })

    def test_valid_frame_passes(self, feature_df):
        validate_feature_frame(feature_df, "2026-05-22")

    def test_future_row_raises(self, feature_df):
        feature_df.loc[0, "datetime"] = "2026-05-23"
        with pytest.raises(ValueError, match="future row"):
            validate_feature_frame(feature_df, "2026-05-22")

    def test_missing_instrument_col_raises(self, feature_df):
        df = feature_df.drop(columns=["instrument"])
        with pytest.raises(ValueError, match="missing column.*instrument"):
            validate_feature_frame(df, "2026-05-22")

    def test_missing_datetime_col_raises(self, feature_df):
        df = feature_df.drop(columns=["datetime"])
        with pytest.raises(ValueError, match="missing column.*datetime"):
            validate_feature_frame(df, "2026-05-22")

    def test_nan_instrument_raises(self, feature_df):
        feature_df.loc[0, "instrument"] = None
        with pytest.raises(ValueError, match="NaN in instrument"):
            validate_feature_frame(feature_df, "2026-05-22")

    def test_nan_datetime_raises(self, feature_df):
        feature_df.loc[0, "datetime"] = None
        with pytest.raises(ValueError, match="NaN in date"):
            validate_feature_frame(feature_df, "2026-05-22")

    def test_custom_column_names(self, feature_df):
        df = feature_df.rename(columns={"instrument": "code", "datetime": "date"})
        validate_feature_frame(df, "2026-05-22", instrument_col="code", date_col="date")
