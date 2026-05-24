"""Tests for qsys/data/calendar.py — trading calendar resolution.

Uses monkeypatched calendar provider to avoid qlib dependency.
"""

from __future__ import annotations

import pytest

from qsys.data.calendar import (
    get_trading_calendar,
    resolve_data_date,
    resolve_previous_trading_date,
    set_calendar_provider,
)

SAMPLE_CALENDAR = [
    "2026-05-15",
    "2026-05-18",
    "2026-05-19",
    "2026-05-20",
    "2026-05-21",
    "2026-05-22",
]


def _mock_calendar(start: str, end: str) -> list[str]:
    """Filter SAMPLE_CALENDAR to [start, end] range."""
    return [d for d in SAMPLE_CALENDAR if start <= d <= end]


@pytest.fixture(autouse=True)
def _patch_calendar():
    set_calendar_provider(_mock_calendar)
    yield
    set_calendar_provider(None)


# ── get_trading_calendar ────────────────────────────────────────────────


class TestGetTradingCalendar:
    def test_returns_sorted_strings(self):
        cal = get_trading_calendar("2026-05-18", "2026-05-20")
        assert cal == ["2026-05-18", "2026-05-19", "2026-05-20"]
        for d in cal:
            assert isinstance(d, str)
            assert len(d) == 10

    def test_range_excludes_outside(self):
        cal = get_trading_calendar("2026-05-16", "2026-05-17")
        assert cal == []

    def test_single_day(self):
        cal = get_trading_calendar("2026-05-18", "2026-05-18")
        assert cal == ["2026-05-18"]


# ── resolve_data_date (asof) ────────────────────────────────────────────


class TestResolveDataDateAsof:
    def test_trading_day_returns_same(self):
        assert resolve_data_date("2026-05-18", mode="asof") == "2026-05-18"

    def test_weekend_rolls_back(self):
        # 2026-05-17 is Sunday
        assert resolve_data_date("2026-05-17", mode="asof") == "2026-05-15"

    def test_holiday_rolls_back(self):
        # A date not in the calendar
        assert resolve_data_date("2026-05-16", mode="asof") in [
            "2026-05-15"
        ]

    def test_never_greater_than_input(self):
        for day in SAMPLE_CALENDAR:
            result = resolve_data_date(day, mode="asof")
            assert result <= day, f"{result} > {day}"

    def test_default_mode_is_asof(self):
        assert resolve_data_date("2026-05-18") == "2026-05-18"


# ── resolve_data_date (previous) ────────────────────────────────────────


class TestResolveDataDatePrevious:
    def test_trading_day_returns_previous(self):
        assert resolve_data_date("2026-05-18", mode="previous") == "2026-05-15"

    def test_weekend_returns_previous(self):
        # 2026-05-18 Monday → previous is 2026-05-15
        assert resolve_data_date("2026-05-18", mode="previous") == "2026-05-15"

    def test_previous_of_first_date(self):
        with pytest.raises(ValueError, match="cannot resolve previous"):
            resolve_data_date("2026-05-15", mode="previous")


# ── resolve_previous_trading_date ───────────────────────────────────────


class TestResolvePreviousTradingDate:
    def test_shorthand(self):
        assert resolve_previous_trading_date("2026-05-18") == "2026-05-15"


# ── Error cases ─────────────────────────────────────────────────────────


class TestErrors:
    def test_invalid_mode(self):
        with pytest.raises(ValueError, match="unsupported calendar mode"):
            resolve_data_date("2026-05-18", mode="future")

    def test_no_trading_dates_available(self):
        empty_provider = lambda s, e: []
        set_calendar_provider(empty_provider)
        with pytest.raises(ValueError, match="no trading dates available"):
            resolve_data_date("2026-05-18")


# ── Cleanup ─────────────────────────────────────────────────────────────


def teardown_module():
    set_calendar_provider(None)
