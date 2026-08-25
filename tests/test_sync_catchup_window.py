from __future__ import annotations

import pytest

from scripts.ops.sync_csi800_daily import _resolve_sync_window


def test_normal_daily_window_ignores_stale_qlib_watermark() -> None:
    assert _resolve_sync_window("20260811") == {
        "mode": "daily_single_day",
        "start_date": "20260811",
        "target_date": "20260811",
    }


def test_explicit_repair_window_is_the_only_historical_mode() -> None:
    assert _resolve_sync_window("20260811", "20260810") == {
        "mode": "explicit_historical_repair",
        "start_date": "20260810",
        "target_date": "20260811",
    }


def test_explicit_repair_equal_to_target_remains_single_day() -> None:
    assert _resolve_sync_window("20260811", "2026-08-11") == {
        "mode": "daily_single_day",
        "start_date": "20260811",
        "target_date": "20260811",
    }


@pytest.mark.parametrize(
    "repair_start", ["20260812", "2026081", "not-a-date", "20260230"]
)
def test_repair_window_fails_closed_for_invalid_or_future_date(repair_start: str) -> None:
    with pytest.raises(ValueError, match="repair-start-date"):
        _resolve_sync_window("20260811", repair_start)
