"""Tests for scripts/checks/check_daily_read_model.py."""
from __future__ import annotations

import json, sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parents[2]
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from scripts.checks.check_daily_read_model import check_daily_read_model


def _valid_manifest(tmp_path: Path) -> Path:
    d = {
        "execution_date": "2026-05-22",
        "updated_at": "2026-05-22T10:00:00",
        "stages": {
            "pre_open": {"status": "ready", "report_path": "r.json", "summary": {"shadow_plan": {"status": "ready"}}},
        },
    }
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(d))
    return p


class TestCheckDailyReadModel:
    def test_valid_passes(self, tmp_path: Path) -> None:
        r = check_daily_read_model(_valid_manifest(tmp_path))
        assert r["status"] == "passed"

    def test_missing_path_fails(self, tmp_path: Path) -> None:
        r = check_daily_read_model(tmp_path / "nope.json")
        assert r["status"] == "failed"

    def test_missing_top_keys_fails(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text(json.dumps({"bad": True}))
        r = check_daily_read_model(p)
        assert r["status"] == "failed"

    def test_malformed_json_fails(self, tmp_path: Path) -> None:
        p = tmp_path / "garbage.json"
        p.write_text("{{broken")
        r = check_daily_read_model(p)
        assert r["status"] == "failed"
