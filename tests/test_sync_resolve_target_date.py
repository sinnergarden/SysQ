"""Tests for scripts/ops/sync_csi800_daily._resolve_target_date."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.ops.sync_csi800_daily import _resolve_target_date


def _make_mock_calendar(dates: list[str]) -> pd.DataFrame:
    """Build a mock trade_cal DataFrame."""
    return pd.DataFrame({
        "exchange": "SSE",
        "cal_date": dates,
        "is_open": 1,
        "pretrade_date": dates,
    })


class TestExplicitEndDate:
    def test_with_date_returns_normalized(self):
        assert _resolve_target_date("2026-05-28") == "20260528"

    def test_with_date_already_normalized(self):
        assert _resolve_target_date("20260528") == "20260528"


class TestCalendarLookup:
    def test_normal_day_returns_most_recent_trading_day(self):
        """After the data-ready cutoff, an open session can be selected."""
        mock_cal = _make_mock_calendar(["20260527", "20260528"])
        with patch(
            "scripts.ops.sync_csi800_daily.StockDataStore.get_calendar",
            return_value=mock_cal,
        ):
            result = _resolve_target_date(
                None, now=datetime(2026, 5, 28, 19, 0)
            )
            assert result == "20260528"

    def test_before_ready_cutoff_excludes_current_open_session(self):
        mock_cal = _make_mock_calendar(["20260527", "20260528"])
        with patch(
            "scripts.ops.sync_csi800_daily.StockDataStore.get_calendar",
            return_value=mock_cal,
        ):
            result = _resolve_target_date(
                None, now=datetime(2026, 5, 28, 2, 19)
            )
            assert result == "20260527"

    def test_non_trading_day_returns_most_recent_open_session(self):
        mock_cal = _make_mock_calendar(["20260528", "20260529"])
        with patch(
            "scripts.ops.sync_csi800_daily.StockDataStore.get_calendar",
            return_value=mock_cal,
        ):
            assert _resolve_target_date(
                None, now=datetime(2026, 5, 30, 10, 0)
            ) == "20260529"

    def test_empty_calendar_fails_closed(self):
        mock_cal = pd.DataFrame()
        with patch(
            "scripts.ops.sync_csi800_daily.StockDataStore.get_calendar",
            return_value=mock_cal,
        ), pytest.raises(RuntimeError, match="pass --target-date"):
            _resolve_target_date(None, now=datetime(2026, 5, 28, 19, 0))

    def test_calendar_missing_is_open_column_fails_closed(self):
        bad_cal = pd.DataFrame({"cal_date": ["20260528"]})
        with patch(
            "scripts.ops.sync_csi800_daily.StockDataStore.get_calendar",
            return_value=bad_cal,
        ), pytest.raises(RuntimeError, match="pass --target-date"):
            _resolve_target_date(None, now=datetime(2026, 5, 28, 19, 0))

    def test_calendar_exception_fails_closed(self):
        with patch(
            "scripts.ops.sync_csi800_daily.StockDataStore.get_calendar",
            side_effect=RuntimeError("DB unavailable"),
        ), pytest.raises(RuntimeError, match="pass --target-date"):
            _resolve_target_date(None, now=datetime(2026, 5, 28, 19, 0))


class TestCrossYear:
    def test_new_year_uses_last_completed_prior_year_session(self):
        mock_cal = _make_mock_calendar([
            "20261230", "20261231",
        ])
        with patch(
            "scripts.ops.sync_csi800_daily.StockDataStore.get_calendar",
            return_value=mock_cal,
        ):
            result = _resolve_target_date(
                None, now=datetime(2027, 1, 1, 10, 0)
            )
            assert result == "20261231"

    def test_last_day_of_year_resolves_to_december(self):
        """Dec 31 2026 should resolve normally (same year)."""
        mock_cal = _make_mock_calendar([
            "20261230", "20261231",
        ])
        with patch(
            "scripts.ops.sync_csi800_daily.StockDataStore.get_calendar",
            return_value=mock_cal,
        ):
            result = _resolve_target_date(
                None, now=datetime(2026, 12, 31, 19, 0)
            )
            assert result == "20261231"
