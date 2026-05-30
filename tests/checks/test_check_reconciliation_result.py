"""Tests for scripts/checks/check_reconciliation_result.py."""
from __future__ import annotations

import sys, pandas as pd
from pathlib import Path

_PROJ = Path(__file__).resolve().parents[2]
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from scripts.checks.check_reconciliation_result import check_reconciliation_result


def _write_csv(path: Path, columns: list[str], rows: list[list]) -> None:
    df = pd.DataFrame(rows, columns=columns)
    df.to_csv(path, index=False)


def _valid_recon_dir(tmp_path: Path) -> Path:
    d = tmp_path / "recon"
    d.mkdir()
    _write_csv(d / "reconcile_summary_20260522.csv",
               ["metric", "real", "shadow", "diff"],
               [["cash", 0, 10000, -10000]])
    _write_csv(d / "reconcile_positions_20260522.csv",
               ["symbol", "real_amount", "shadow_amount", "amount_diff",
                "real_market_value", "shadow_market_value", "market_value_diff"],
               [["000001.SZ", 100, 100, 0, 1000, 1000, 0]])
    _write_csv(d / "reconcile_real_trades_20260522.csv",
               ["symbol", "side", "amount", "price"],
               [["000001.SZ", "buy", 100, 10.0]])
    return d


class TestCheckReconciliationResult:
    def test_valid_passes(self, tmp_path: Path) -> None:
        r = check_reconciliation_result(_valid_recon_dir(tmp_path))
        assert r["status"] == "passed"
        assert r["summary_count"] == 1
        assert r["position_count"] == 1
        assert r["trade_count"] == 1

    def test_missing_path_fails(self, tmp_path: Path) -> None:
        r = check_reconciliation_result(tmp_path / "nope")
        assert r["status"] == "failed"

    def test_empty_dir_yields_no_fail_no_file(self, tmp_path: Path) -> None:
        d = tmp_path / "empty"
        d.mkdir()
        r = check_reconciliation_result(d)
        assert r["status"] == "passed"
        assert r["files_found"] == []
        assert len(r["warnings"]) >= 1

    def test_missing_columns_fails(self, tmp_path: Path) -> None:
        d = tmp_path / "bad"
        d.mkdir()
        _write_csv(d / "reconcile_summary_20260522.csv",
                   ["metric", "real"],
                   [["cash", 0]])
        r = check_reconciliation_result(d)
        assert r["status"] == "failed"
