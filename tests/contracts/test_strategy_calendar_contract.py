"""Contract tests: every strategy adapter's resolve_data_date follows asof semantics.

These tests use a monkeypatched calendar provider so they do not need qlib.
They verify that all registered strategies resolve dates consistently.
"""

from __future__ import annotations

import pytest

from qsys.data.calendar import set_calendar_provider
from qsys.strategy.registry import create_strategy

SAMPLE_CALENDAR = [
    "2026-05-15",
    "2026-05-18",
    "2026-05-19",
    "2026-05-20",
    "2026-05-21",
    "2026-05-22",
]


def _mock_calendar(start: str, end: str) -> list[str]:
    return [d for d in SAMPLE_CALENDAR if start <= d <= end]


@pytest.fixture(autouse=True)
def _patch_calendar():
    set_calendar_provider(_mock_calendar)
    yield
    set_calendar_provider(None)


STRATEGY_IDS = ["alpha_v1", "alpha_v2"]


class TestStrategyCalendarContract:
    @pytest.mark.parametrize("strategy_id", STRATEGY_IDS)
    def test_resolve_data_date_returns_string(self, strategy_id):
        strategy = create_strategy(strategy_id)
        result = strategy.resolve_data_date("2026-05-18")
        assert isinstance(result, str)

    @pytest.mark.parametrize("strategy_id", STRATEGY_IDS)
    def test_trading_day_returns_same(self, strategy_id):
        strategy = create_strategy(strategy_id)
        result = strategy.resolve_data_date("2026-05-18")
        assert result == "2026-05-18"

    @pytest.mark.parametrize("strategy_id", STRATEGY_IDS)
    def test_weekend_rolls_back(self, strategy_id):
        strategy = create_strategy(strategy_id)
        result = strategy.resolve_data_date("2026-05-17")  # Sunday
        assert result == "2026-05-15"

    @pytest.mark.parametrize("strategy_id", STRATEGY_IDS)
    def test_non_trading_day_rolls_back(self, strategy_id):
        strategy = create_strategy(strategy_id)
        result = strategy.resolve_data_date("2026-05-16")  # Saturday
        assert result <= "2026-05-16"

    @pytest.mark.parametrize("strategy_id", STRATEGY_IDS)
    def test_never_greater_than_trade_date(self, strategy_id):
        strategy = create_strategy(strategy_id)
        for day in SAMPLE_CALENDAR:
            result = strategy.resolve_data_date(day)
            assert result <= day
