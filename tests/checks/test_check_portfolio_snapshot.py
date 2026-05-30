"""Tests for scripts/checks/check_portfolio_snapshot.py."""
from __future__ import annotations

import json, sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parents[2]
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from scripts.checks.check_portfolio_snapshot import check_portfolio_snapshot


def _valid_snapshot(tmp_path: Path) -> Path:
    d = {
        "execution_date": "2026-05-22",
        "archive_root": "/tmp",
        "stages": {
            "pre_open": {"stage_root": "/tmp/pre", "artifacts": {"sig": {"category": "signals", "path": "/tmp/sig.csv"}}},
            "post_close": {"stage_root": "/tmp/post", "artifacts": {}},
        },
    }
    p = tmp_path / "snapshot.json"
    p.write_text(json.dumps(d))
    return p


class TestCheckPortfolioSnapshot:
    def test_valid_passes(self, tmp_path: Path) -> None:
        r = check_portfolio_snapshot(_valid_snapshot(tmp_path))
        assert r["status"] == "passed"

    def test_missing_path_fails(self, tmp_path: Path) -> None:
        r = check_portfolio_snapshot(tmp_path / "nope.json")
        assert r["status"] == "failed"

    def test_missing_top_keys_fails(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text(json.dumps({"bad": True}))
        r = check_portfolio_snapshot(p)
        assert r["status"] == "failed"

    def test_malformed_json_fails(self, tmp_path: Path) -> None:
        p = tmp_path / "garbage.json"
        p.write_text("{{{")
        r = check_portfolio_snapshot(p)
        assert r["status"] == "failed"
