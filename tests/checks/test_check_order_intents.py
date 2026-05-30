"""Tests for scripts/checks/check_order_intents.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parents[2]
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from scripts.checks.check_order_intents import check_order_intents


def _valid_intents(tmp_path: Path) -> Path:
    d = {
        "artifact_type": "order_intents",
        "execution_date": "2026-05-22",
        "account_name": "shadow",
        "intents": [
            {"intent_id": "i1", "symbol": "000001.SZ", "side": "buy",
             "amount": 100, "price": 10.0, "est_value": 1000.0, "status": "planned"}
        ],
    }
    p = tmp_path / "order_intents.json"
    p.write_text(json.dumps(d))
    return p


class TestCheckOrderIntents:
    def test_valid_passes(self, tmp_path: Path) -> None:
        r = check_order_intents(_valid_intents(tmp_path))
        assert r["status"] == "passed"
        assert r["intent_count"] == 1

    def test_missing_path_fails(self, tmp_path: Path) -> None:
        r = check_order_intents(tmp_path / "not_exists.json")
        assert r["status"] == "failed"
        assert "not found" in r["errors"][0]

    def test_missing_top_keys_fails(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text(json.dumps({"bad": True}))
        r = check_order_intents(p)
        assert r["status"] == "failed"
        assert r["errors"][0]

    def test_missing_intent_keys_fails(self, tmp_path: Path) -> None:
        d = {
            "artifact_type": "order_intents",
            "execution_date": "2026-05-22",
            "account_name": "shadow",
            "intents": [{"intent_id": "i1"}],
        }
        p = tmp_path / "bad_intent.json"
        p.write_text(json.dumps(d))
        r = check_order_intents(p)
        assert r["status"] == "failed"
        assert any("symbol" in e for e in r["errors"])

    def test_malformed_json(self, tmp_path: Path) -> None:
        p = tmp_path / "garbage.json"
        p.write_text("not json")
        r = check_order_intents(p)
        assert r["status"] == "failed"
        assert "unreadable" in r["errors"][0]

    def test_non_numeric_est_value_fails(self, tmp_path: Path) -> None:
        d = {
            "artifact_type": "order_intents",
            "execution_date": "2026-05-22",
            "account_name": "shadow",
            "intents": [
                {"intent_id": "i1", "symbol": "000001.SZ", "side": "buy",
                 "amount": 100, "price": 10.0, "est_value": "abc", "status": "planned"}
            ],
        }
        p = tmp_path / "bad_est.json"
        p.write_text(json.dumps(d))
        r = check_order_intents(p)
        assert r["status"] == "failed"
        assert any("est_value" in e and "abc" in e for e in r["errors"])
