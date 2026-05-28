"""Tests for scripts/ops/sync_csi800_daily._resolve_target_date."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd

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
        """When today is a trading day already in calendar, return today."""
        mock_cal = _make_mock_calendar(["20260527", "20260528"])
        with patch(
            "scripts.ops.sync_csi800_daily.StockDataStore.get_calendar",
            return_value=mock_cal,
        ), patch(
            "scripts.ops.sync_csi800_daily.datetime"
        ) as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "20260528"
            result = _resolve_target_date(None)
            assert result == "20260528"

    def test_before_open_nearest_trading_day(self):
        """When today is not in calendar but a past day is, return the past day."""
        mock_cal = _make_mock_calendar(["20260527"])
        with patch(
            "scripts.ops.sync_csi800_daily.StockDataStore.get_calendar",
            return_value=mock_cal,
        ), patch(
            "scripts.ops.sync_csi800_daily.datetime"
        ) as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "20260528"
            result = _resolve_target_date(None)
            assert result == "20260527"

    def test_empty_calendar_fallsback_to_today(self):
        """When the calendar table is empty, return today."""
        mock_cal = pd.DataFrame()
        with patch(
            "scripts.ops.sync_csi800_daily.StockDataStore.get_calendar",
            return_value=mock_cal,
        ), patch(
            "scripts.ops.sync_csi800_daily.datetime"
        ) as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "20260528"
            result = _resolve_target_date(None)
            assert result == "20260528"

    def test_calendar_missing_is_open_column_fallsback_to_today(self):
        """Graceful degradation when calendar has unexpected columns."""
        bad_cal = pd.DataFrame({"cal_date": ["20260528"]})
        with patch(
            "scripts.ops.sync_csi800_daily.StockDataStore.get_calendar",
            return_value=bad_cal,
        ), patch(
            "scripts.ops.sync_csi800_daily.datetime"
        ) as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "20260528"
            result = _resolve_target_date(None)
            assert result == "20260528"

    def test_calendar_exception_handled_gracefully(self):
        """Any exception from get_calendar falls back to today."""
        with patch(
            "scripts.ops.sync_csi800_daily.StockDataStore.get_calendar",
            side_effect=RuntimeError("DB unavailable"),
        ), patch(
            "scripts.ops.sync_csi800_daily.datetime"
        ) as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "20260528"
            result = _resolve_target_date(None)
            assert result == "20260528"


class TestCrossYear:
    def test_new_year_not_in_calendar_returns_today(self):
        """When calendar has no entries for current year (e.g. Jan 2027),
        return today so pre_check can decide."""
        mock_cal = _make_mock_calendar([
            "20261230", "20261231",
        ])
        with patch(
            "scripts.ops.sync_csi800_daily.StockDataStore.get_calendar",
            return_value=mock_cal,
        ), patch(
            "scripts.ops.sync_csi800_daily.datetime"
        ) as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "20270104"
            result = _resolve_target_date(None)
            assert result == "20270104"

    def test_last_day_of_year_resolves_to_december(self):
        """Dec 31 2026 should resolve normally (same year)."""
        mock_cal = _make_mock_calendar([
            "20261230", "20261231",
        ])
        with patch(
            "scripts.ops.sync_csi800_daily.StockDataStore.get_calendar",
            return_value=mock_cal,
        ), patch(
            "scripts.ops.sync_csi800_daily.datetime"
        ) as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "20261231"
            result = _resolve_target_date(None)
            assert result == "20261231"
