"""Tests for scripts/checks/check_no_lookahead.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

_PROJ = Path(__file__).resolve().parents[2]
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from scripts.checks.check_no_lookahead import check_no_lookahead


def _write_signal_csv(path: Path, rows: list[list]) -> Path:
    df = pd.DataFrame(
        rows,
        columns=["trade_date", "data_date", "instrument", "signal_id", "signal_run_id", "score"],
    )
    df.to_csv(path, index=False)
    return path


class TestCheckNoLookahead:
    # Use known-distance dates: 2026-05-18 (Mon), 2026-05-15 (Fri),
    # 2026-05-19 (Tue) — safe from Chinese holidays.
    _MON = "2026-05-18"
    _FRI = "2026-05-15"
    _TUE = "2026-05-19"
    _THU = "2026-05-21"
    _PREV_THU = "2026-05-20"  # previous trading day of 2026-05-21

    def test_valid_rows_pass(self, tmp_path: Path) -> None:
        csv = _write_signal_csv(tmp_path / "good.csv", [
            [self._MON, self._FRI, "000001.SZ", "a", "r1", 0.5],
            [self._TUE, self._MON, "000001.SZ", "a", "r1", 0.6],
        ])
        result = check_no_lookahead(csv)
        assert result["status"] == "passed"
        assert result["violations"] == 0

    def test_same_day_violation_fails(self, tmp_path: Path) -> None:
        csv = _write_signal_csv(tmp_path / "bad.csv", [
            [self._MON, self._MON, "000001.SZ", "a", "r1", 0.5],
        ])
        result = check_no_lookahead(csv)
        assert result["status"] == "failed"
        assert result["violations"] >= 1

    def test_future_data_date_fails(self, tmp_path: Path) -> None:
        csv = _write_signal_csv(tmp_path / "bad2.csv", [
            [self._MON, self._TUE, "000001.SZ", "a", "r1", 0.5],
        ])
        result = check_no_lookahead(csv)
        assert result["status"] == "failed"
        assert result["violations"] >= 1

    def test_allow_same_day_passes(self, tmp_path: Path) -> None:
        csv = _write_signal_csv(tmp_path / "ok.csv", [
            [self._MON, self._MON, "000001.SZ", "a", "r1", 0.5],
        ])
        result = check_no_lookahead(csv, allow_same_day=True)
        assert result["status"] == "passed"
        assert result["violations"] == 0

    def test_weekday_rollback_works(self, tmp_path: Path) -> None:
        # Monday's data_date on Friday — clearly before Monday's prev trading day
        csv = _write_signal_csv(tmp_path / "weekday.csv", [
            [self._MON, self._FRI, "000001.SZ", "a", "r1", 0.5],
        ])
        result = check_no_lookahead(csv)
        assert result["status"] == "passed"

    def test_returns_json_status(self, tmp_path: Path) -> None:
        csv = _write_signal_csv(tmp_path / "good.csv", [
            [self._MON, self._FRI, "000001.SZ", "a", "r1", 0.5],
        ])
        result = check_no_lookahead(csv)
        parsed = json.loads(json.dumps(result))
        assert "status" in parsed

    def test_fallback_calendar_mode(self, tmp_path: Path) -> None:
        csv = _write_signal_csv(tmp_path / "fallback.csv", [
            [self._MON, self._FRI, "000001.SZ", "a", "r1", 0.5],
        ])
        result = check_no_lookahead(csv)
        assert result["calendar_mode"] in ("qsys", "fallback_bday")

    def test_missing_columns_no_error(self, tmp_path: Path) -> None:
        df = pd.DataFrame(
            [[self._MON]], columns=["trade_date"],
        )
        csv = tmp_path / "bad_cols.csv"
        df.to_csv(csv, index=False)
        result = check_no_lookahead(csv)
        # missing columns means no check runs → degraded
        assert "errors" in result
        assert len(result["errors"]) >= 1
