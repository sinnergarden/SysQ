"""Tests for scripts/checks/check_signal_schema.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

# Ensure project root is on path for script imports
_PROJ = Path(__file__).resolve().parents[2]
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from scripts.checks.check_signal_schema import check_signal_schema


def _write_csv(path: Path, columns: list[str], rows: list[list]) -> Path:
    df = pd.DataFrame(rows, columns=columns)
    df.to_csv(path, index=False)
    return path


class TestCheckSignalSchema:
    def test_valid_signal_passes(self, tmp_path: Path) -> None:
        csv = _write_csv(
            tmp_path / "valid.csv",
            ["trade_date", "data_date", "instrument", "signal_id", "signal_run_id", "score"],
            [["2026-05-01", "2026-04-30", "000001.SZ", "alpha_v1", "rolling_20260501_a1b2", 1.5]],
        )
        result = check_signal_schema(csv)
        assert result["status"] == "passed"
        assert result["checked_files"] == 1
        assert result["checked_rows"] == 1

    def test_missing_required_column_fails(self, tmp_path: Path) -> None:
        csv = _write_csv(
            tmp_path / "bad.csv",
            ["trade_date", "data_date", "instrument", "score"],
            [["2026-05-01", "2026-04-30", "000001.SZ", 1.5]],
        )
        result = check_signal_schema(csv)
        assert result["status"] == "failed"
        assert len(result["missing_columns"]) == 2
        assert any("signal_id" in m for m in result["missing_columns"])
        assert any("signal_run_id" in m for m in result["missing_columns"])

    def test_missing_signal_run_id_fails(self, tmp_path: Path) -> None:
        csv = _write_csv(
            tmp_path / "no_runid.csv",
            ["trade_date", "data_date", "instrument", "signal_id", "score"],
            [["2026-05-01", "2026-04-30", "000001.SZ", "alpha_v1", 1.5]],
        )
        result = check_signal_schema(csv)
        assert result["status"] == "failed"
        assert "signal_run_id" in result["missing_columns"][0]

    def test_multiple_missing_columns(self, tmp_path: Path) -> None:
        csv = _write_csv(
            tmp_path / "bad2.csv",
            ["trade_date", "value"],
            [["2026-05-01", 1.5]],
        )
        result = check_signal_schema(csv)
        assert result["status"] == "failed"
        assert len(result["missing_columns"]) >= 3

    def test_empty_csv_passes_schema(self, tmp_path: Path) -> None:
        csv = _write_csv(
            tmp_path / "empty.csv",
            ["trade_date", "data_date", "instrument", "signal_id", "signal_run_id", "score"],
            [],
        )
        result = check_signal_schema(csv)
        assert result["status"] == "passed"
        assert result["checked_rows"] == 0

    def test_directory_with_valid_and_invalid(self, tmp_path: Path) -> None:
        _write_csv(
            tmp_path / "good.csv",
            ["trade_date", "data_date", "instrument", "signal_id", "signal_run_id", "score"],
            [["2026-05-01", "2026-04-30", "000001.SZ", "a", "run1", 0.5]],
        )
        _write_csv(
            tmp_path / "bad.csv",
            ["trade_date", "value"],
            [["2026-05-01", 0.5]],
        )
        result = check_signal_schema(tmp_path)
        assert result["status"] == "failed"
        assert result["checked_files"] == 2

    def test_returns_json_status_field(self, tmp_path: Path) -> None:
        csv = _write_csv(
            tmp_path / "valid.csv",
            ["trade_date", "data_date", "instrument", "signal_id", "signal_run_id", "score"],
            [["2026-05-01", "2026-04-30", "000001.SZ", "a", "run1", 0.5]],
        )
        result = check_signal_schema(csv)
        output = json.dumps(result)
        parsed = json.loads(output)
        assert "status" in parsed
        assert parsed["status"] == "passed"
