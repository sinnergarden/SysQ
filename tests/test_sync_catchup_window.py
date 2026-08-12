from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from scripts.ops.sync_csi800_daily import _resolve_catchup_start


class _Store:
    def __init__(self, calendar: pd.DataFrame) -> None:
        self.calendar = calendar

    def get_calendar(self) -> pd.DataFrame:
        return self.calendar


def _adapter(tmp_path: Path, latest: str) -> SimpleNamespace:
    path = tmp_path / "calendars"
    path.mkdir(parents=True)
    (path / "day.txt").write_text(latest + "\n", encoding="utf-8")
    return SimpleNamespace(qlib_dir=tmp_path)


def test_uses_canonical_calendar_to_find_first_missing_session(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, "2026-08-07")
    calendar = pd.DataFrame({
        "cal_date": ["20260808", "20260809", "20260810", "20260811"],
        "is_open": [0, 0, 1, 1],
    })
    assert _resolve_catchup_start(adapter, _Store(calendar), "20260811") == "20260810"


def test_up_to_date_qlib_uses_target_only(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, "2026-08-11")
    assert _resolve_catchup_start(adapter, _Store(pd.DataFrame()), "20260811") == "20260811"


def test_missing_canonical_session_fails_closed(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, "2026-08-07")
    calendar = pd.DataFrame({"cal_date": ["20260808"], "is_open": [0]})
    with pytest.raises(ValueError, match="Cannot resolve catch-up window"):
        _resolve_catchup_start(adapter, _Store(calendar), "20260811")
