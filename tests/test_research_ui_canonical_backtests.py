from __future__ import annotations

import json
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from qsys.research_ui.assembler import ResearchCockpitRepository


def _write_canonical_backtest(
    root: Path, *, with_executions: bool = False
) -> str:
    strategy_run_id = "rank_weight_top20__financial_rc_60d_180d_50_50__stablehash"
    backtest_id = "bt_2021-01-04_2026-07-31_stablehash"
    run_dir = root / "data" / "research" / "backtests" / strategy_run_id / backtest_id
    run_dir.mkdir(parents=True)
    manifest = {
        "artifact_type": "backtest_run",
        "strategy_run_id": strategy_run_id,
        "backtest_id": backtest_id,
        "strategy_template_id": "rank_weight_top20",
        "signal_id": "financial_rc_60d_180d_50_50__daily_zscore",
        "signal_run_id": "rolling__financial_rc_60d_180d_50_50__20210104_20260731",
        "effective_start_date": "2021-01-04",
        "effective_end_date": "2026-07-31",
        "allocation_params": {"top_n": 20, "max_weight": None},
        "rebalance_freq": "weekly",
        "execution_price": "open",
        "mtm_price": "close",
        "use_adjusted_price": True,
        "git_commit": "c9b3a514",
        "initial_capital": 10_000_000,
        "total_return": 0.25,
        "trading_day_count": 2,
    }
    if with_executions:
        executions_path = run_dir / "executions.csv"
        executions_path.write_text(
            "execution_id,trade_date,sequence,instrument,side,execution_phase,trade_reason,requested_qty,requested_price,execution_price_mode,reference_price,status,filled_qty,deal_price,gross_amount,commission,tax,total_fee,rejection_reason\n"
            "2021-01-04:000000:000001.SZ:buy,2021-01-04,0,000001.SZ,buy,rebalance,rebalance_to_target_weight,100,10.0,open,10.0,filled,100,10.01,1001.0,5.0,0.0,5.0,\n"
            "2026-07-31:000001:000001.SZ:sell,2026-07-31,1,000001.SZ,sell,exit,score_delta,100,12.0,open,12.0,filled,100,11.988,1198.8,5.0,1.1988,6.1988,\n",
            encoding="utf-8",
        )
        manifest["artifacts"] = {
            "executions": {
                "path": "executions.csv",
                "schema_version": "backtest_executions_v1",
                "sha256": hashlib.sha256(executions_path.read_bytes()).hexdigest(),
                "row_count": 2,
                "complete": True,
            }
        }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_dir / "metrics.json").write_text(
        json.dumps({"total_return": 0.25, "trading_day_count": 2, "order_count_total": 7}),
        encoding="utf-8",
    )
    (run_dir / "backtest_result.json").write_text(
        json.dumps({"status": "completed", "backtest_id": backtest_id}),
        encoding="utf-8",
    )
    (run_dir / "daily_summary.csv").write_text(
        "trade_date,total_value_after,turnover,order_count,status\n"
        "2021-01-04,9000000,1000000,5,success\n"
        "2026-07-31,12500000,500000,2,success\n",
        encoding="utf-8",
    )
    return f"canonical__{strategy_run_id}__{backtest_id}"


def test_canonical_backtest_is_listed_and_loaded_by_explicit_identity(tmp_path: Path) -> None:
    run_id = _write_canonical_backtest(tmp_path)
    repo = ResearchCockpitRepository(project_root=tmp_path)

    runs = repo.list_backtest_runs(limit=10)
    matching = [item for item in runs if item.run_id == run_id]
    assert len(matching) == 1
    assert matching[0].test_range == {"start": "2021-01-04", "end": "2026-07-31"}
    assert matching[0].top_k == 20
    assert matching[0].metrics["max_drawdown"] == "-10.00%"
    assert matching[0].manifest_ref.endswith("/manifest.json")

    with patch.object(repo, "_load_benchmark_points", return_value=[]):
        daily = repo.get_backtest_daily_points(run_id)
    assert [item.trade_date for item in daily] == ["2021-01-04", "2026-07-31"]
    assert [item.equity for item in daily] == [9_000_000.0, 12_500_000.0]
    assert [item.trade_count for item in daily] == [5, 2]
    assert daily[0].drawdown == pytest.approx(-0.1)
    assert daily[-1].daily_return == pytest.approx((12_500_000 / 9_000_000) - 1.0)
    assert daily[-1].drawdown == 0.0


def test_unknown_explicit_canonical_identity_does_not_fallback(tmp_path: Path) -> None:
    _write_canonical_backtest(tmp_path)
    repo = ResearchCockpitRepository(project_root=tmp_path)

    with pytest.raises(FileNotFoundError, match="Unknown backtest run_id"):
        repo.get_backtest_summary("canonical__missing_strategy__missing_backtest")


def test_manifest_identity_must_match_canonical_directory(tmp_path: Path) -> None:
    run_id = _write_canonical_backtest(tmp_path)
    manifest_path = next((tmp_path / "data" / "research" / "backtests").glob("*/*/manifest.json"))
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["backtest_id"] = "bt_tampered"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    repo = ResearchCockpitRepository(project_root=tmp_path)
    assert run_id not in {item.run_id for item in repo.list_backtest_runs(limit=10)}


def test_canonical_execution_artifact_supports_date_and_instrument_filters(
    tmp_path: Path,
) -> None:
    run_id = _write_canonical_backtest(tmp_path, with_executions=True)
    repo = ResearchCockpitRepository(project_root=tmp_path)

    status = repo.get_backtest_execution_artifact_status(run_id)
    assert status["status"] == "available"
    assert status["complete"] is True
    assert status["row_count"] == 2
    rows = repo.get_backtest_orders(
        run_id, instrument_id="000001.SZ", limit=10
    )
    assert [row["side"] for row in rows] == ["buy", "sell"]
    assert rows[-1]["trade_reason"] == "score_delta"
    selected = repo.get_backtest_orders(
        run_id, trade_date="2026-07-31", limit=10
    )
    assert len(selected) == 1
    assert selected[0]["deal_price"] == 11.988


def test_old_canonical_run_reports_execution_detail_unavailable(
    tmp_path: Path,
) -> None:
    run_id = _write_canonical_backtest(tmp_path)
    repo = ResearchCockpitRepository(project_root=tmp_path)
    status = repo.get_backtest_execution_artifact_status(run_id)
    assert status == {
        "status": "unavailable",
        "reason": "canonical_run_did_not_persist_executions",
        "complete": False,
    }
    assert repo.get_backtest_orders(run_id) == []


def test_tampered_canonical_execution_artifact_fails_closed(
    tmp_path: Path,
) -> None:
    run_id = _write_canonical_backtest(tmp_path, with_executions=True)
    path = next(
        (tmp_path / "data/research/backtests").glob("*/*/executions.csv")
    )
    path.write_text(path.read_text() + "tampered\n", encoding="utf-8")
    repo = ResearchCockpitRepository(project_root=tmp_path)
    assert repo.get_backtest_execution_artifact_status(run_id)["status"] == "corrupt"
    with pytest.raises(ValueError, match="sha256_mismatch"):
        repo.get_backtest_orders(run_id)


def test_canonical_sections_derive_returns_and_report_unavailable_signal_metrics(
    tmp_path: Path,
) -> None:
    run_id = _write_canonical_backtest(tmp_path, with_executions=True)
    repo = ResearchCockpitRepository(project_root=tmp_path)
    payload = repo.get_backtest_sections(run_id)
    statuses = {item["name"]: item["status"] for item in payload["sections"]}
    assert statuses["Performance"] == "success"
    assert statuses["Monthly Returns"] == "success"
    assert statuses["Rolling Windows"] == "success"
    assert statuses["Cost Analysis"] == "success"
    assert statuses["Trade Detail"] == "available"
    assert statuses["Signal Metrics"] == "unavailable"
    assert payload["artifacts"]["monthly_returns"]
    assert payload["artifacts"]["rolling_metrics"]


def test_frontend_exposes_execution_fields_and_section_availability() -> None:
    root = Path(__file__).resolve().parents[1]
    app = (root / "qsys/research_ui/web/app.js").read_text(encoding="utf-8")
    index = (root / "qsys/research_ui/web/index.html").read_text(
        encoding="utf-8"
    )
    assert "backtest-section-status" in index
    assert "Trade detail unavailable" in app
    assert "row.filled_qty" in app
    assert "row.trade_reason" in app
    assert "item.deal_price" in app
