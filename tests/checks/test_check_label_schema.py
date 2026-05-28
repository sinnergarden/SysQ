"""Tests for scripts/checks/check_label_schema.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

_PROJ = Path(__file__).resolve().parents[2]
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from scripts.checks.check_label_schema import check_label_schema


def _write_csv(path: Path, columns: list[str], rows: list[list]) -> Path:
    df = pd.DataFrame(rows, columns=columns)
    df.to_csv(path, index=False)
    return path


class TestCheckLabelSchema:
    def test_valid_label_passes(self, tmp_path: Path) -> None:
        csv = _write_csv(
            tmp_path / "valid.csv",
            ["trade_date", "instrument", "label_id", "horizon", "label_value"],
            [["2026-05-01", "000001.SZ", "fr_5d", 5, 0.03]],
        )
        result = check_label_schema(csv)
        assert result["status"] == "passed"
        assert result["checked_files"] == 1
        assert result["checked_rows"] == 1

    def test_missing_label_value_fails(self, tmp_path: Path) -> None:
        csv = _write_csv(
            tmp_path / "bad.csv",
            ["trade_date", "instrument", "label_id", "horizon"],
            [["2026-05-01", "000001.SZ", "fr_5d", 5]],
        )
        result = check_label_schema(csv)
        assert result["status"] == "failed"
        assert "label_value" in result["missing_columns"][0]

    def test_missing_horizon_fails(self, tmp_path: Path) -> None:
        csv = _write_csv(
            tmp_path / "bad2.csv",
            ["trade_date", "instrument", "label_id", "label_value"],
            [["2026-05-01", "000001.SZ", "fr_5d", 0.03]],
        )
        result = check_label_schema(csv)
        assert result["status"] == "failed"
        assert "horizon" in result["missing_columns"][0]

    def test_empty_label_passes_schema(self, tmp_path: Path) -> None:
        csv = _write_csv(
            tmp_path / "empty.csv",
            ["trade_date", "instrument", "label_id", "horizon", "label_value"],
            [],
        )
        result = check_label_schema(csv)
        assert result["status"] == "passed"

    def test_returns_json_status_field(self, tmp_path: Path) -> None:
        csv = _write_csv(
            tmp_path / "valid.csv",
            ["trade_date", "instrument", "label_id", "horizon", "label_value"],
            [["2026-05-01", "000001.SZ", "fr_5d", 5, 0.03]],
        )
        result = check_label_schema(csv)
        parsed = json.loads(json.dumps(result))
        assert parsed["status"] == "passed"
