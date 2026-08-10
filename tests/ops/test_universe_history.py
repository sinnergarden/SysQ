from __future__ import annotations

from pathlib import Path

import pandas as pd

from qsys.ops.universe_history import inspect_universe_history


def _write(path: Path, dates: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"trade_date": dates}).to_feather(path)


def test_detects_members_whose_history_starts_at_index_inclusion(tmp_path: Path) -> None:
    canonical = tmp_path / "data" / "canonical" / "daily"
    _write(
        canonical / "FULL.SZ.feather",
        ["20220801", "20260807"],
    )
    _write(
        canonical / "TRUNC.SZ.feather",
        ["20260605", "20260807"],
    )
    result = inspect_universe_history(
        project_root=tmp_path,
        symbols=["FULL.SZ", "TRUNC.SZ"],
        as_of_date="2026-08-07",
        lookback_calendar_days=1461,
    )
    assert result["status"] == "fail"
    assert result["deficient_symbols"] == ["TRUNC.SZ"]


def test_missing_canonical_member_is_deficient(tmp_path: Path) -> None:
    result = inspect_universe_history(
        project_root=tmp_path,
        symbols=["MISSING.SZ"],
        as_of_date="2026-08-07",
        lookback_calendar_days=1461,
    )
    assert result["deficient_count"] == 1


def test_old_first_row_cannot_hide_large_calendar_gap(tmp_path: Path) -> None:
    calendar = tmp_path / "data" / "qlib_bin" / "calendars" / "day.txt"
    calendar.parent.mkdir(parents=True)
    sessions = pd.date_range("2026-01-01", "2026-01-30", freq="B")
    calendar.write_text(
        "\n".join(value.strftime("%Y-%m-%d") for value in sessions) + "\n",
        encoding="utf-8",
    )
    _write(
        tmp_path / "data" / "canonical" / "daily" / "GAPPED.SZ.feather",
        [sessions[0].strftime("%Y%m%d"), sessions[-1].strftime("%Y%m%d")],
    )
    result = inspect_universe_history(
        project_root=tmp_path,
        symbols=["GAPPED.SZ"],
        as_of_date="2026-01-30",
        lookback_calendar_days=29,
    )
    assert result["deficient_symbols"] == ["GAPPED.SZ"]
    assert result["details"][0]["session_coverage"] < 0.95
