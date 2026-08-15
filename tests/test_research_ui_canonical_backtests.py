from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from qsys.research_ui.assembler import ResearchCockpitRepository


def _write_canonical_backtest(root: Path) -> str:
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


def _write_canonical_backtest_with_executions(root: Path) -> str:
    """Canonical run that records an executions artifact plus a multi-month daily curve."""
    strategy_run_id = "posterior_confirmed_top5_financial_rc_50_50__stablehash"
    backtest_id = "bt_2021-01-04_2026-07-31_stablehash"
    run_dir = root / "data" / "research" / "backtests" / strategy_run_id / backtest_id
    run_dir.mkdir(parents=True)
    manifest = {
        "artifact_type": "backtest_run",
        "strategy_run_id": strategy_run_id,
        "backtest_id": backtest_id,
        "strategy_template_id": "posterior_confirmed_top5",
        "signal_id": "financial_rc_60d_180d_50_50__daily_zscore",
        "signal_run_id": "blend__stablehash",
        "effective_start_date": "2021-01-04",
        "effective_end_date": "2026-07-31",
        "allocation_params": {"top_n": 5, "max_weight": None},
        "rebalance_freq": "weekly",
        "execution_price": "open",
        "mtm_price": "close",
        "use_adjusted_price": True,
        "git_commit": "c9b3a514",
        "initial_capital": 10_000_000,
        "commission_bp": 0.0003,
        "stamp_duty_bp": 0.0005,
        "total_return": 0.25,
        "trading_day_count": 2,
        "artifacts": {
            "executions": {
                "path": "executions.csv",
                "complete": True,
                "schema_version": "backtest_executions_v1",
            }
        },
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_dir / "metrics.json").write_text(
        json.dumps(
            {
                "total_return": 0.25,
                "trading_day_count": 252,
                "order_count_total": 7,
                "turnover_total": 100000,
                "filled_count_total": 3,
                "trading_day_count_with_orders": 3,
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "backtest_result.json").write_text(
        json.dumps({"status": "completed", "backtest_id": backtest_id}),
        encoding="utf-8",
    )
    (run_dir / "daily_summary.csv").write_text(
        "trade_date,total_value_after,turnover,order_count,status\n"
        "2021-01-04,9000000,1000000,5,success\n"
        "2021-01-08,9200000,400000,3,success\n"
        "2021-01-20,9500000,500000,2,success\n"
        "2021-02-01,12000000,300000,1,success\n"
        "2026-07-31,12500000,500000,2,success\n",
        encoding="utf-8",
    )
    (run_dir / "executions.csv").write_text(
        "execution_id,trade_date,sequence,instrument,side,execution_phase,trade_reason,"
        "requested_qty,requested_price,execution_price_mode,reference_price,status,"
        "filled_qty,deal_price,gross_amount,commission,tax,total_fee,rejection_reason\n"
        "2021-01-04:000000:600000.SH:buy,2021-01-04,0,600000.SH,buy,entry,top_n_entry,100,10.0,open,10.0,filled,100,10.1,1010.0,0.303,0.0,0.303,\n"
        "2021-01-04:000001:600001.SH:buy,2021-01-04,1,600001.SH,buy,entry,top_n_entry,200,20.0,open,20.0,filled,200,20.2,4040.0,1.212,0.0,1.212,\n"
        "2026-07-31:000000:600000.SH:sell,2026-07-31,0,600000.SH,sell,exit,score_delta_exit,100,30.0,open,30.0,filled,100,30.3,3030.0,0.909,0.0,0.909,\n",
        encoding="utf-8",
    )
    return f"canonical__{strategy_run_id}__{backtest_id}"


def test_canonical_orders_load_from_executions(tmp_path: Path) -> None:
    run_id = _write_canonical_backtest_with_executions(tmp_path)
    repo = ResearchCockpitRepository(project_root=tmp_path)

    orders = repo.get_backtest_orders(run_id)
    assert len(orders) == 3
    first = orders[0]
    assert first["date"] == "2021-01-04"
    assert first["symbol"] == "600000.SH"
    assert first["side"] == "buy"
    assert first["filled_amount"] == 100
    assert first["deal_price"] == pytest.approx(10.1)
    assert first["status"] == "filled"

    by_date = repo.get_backtest_orders(run_id, trade_date="2021-01-04")
    assert [row["symbol"] for row in by_date] == ["600000.SH", "600001.SH"]

    by_instrument = repo.get_backtest_orders(run_id, instrument_id="600001.SH")
    assert len(by_instrument) == 1
    assert by_instrument[0]["symbol"] == "600001.SH"

    limited = repo.get_backtest_orders(run_id, trade_date="2021-01-04", limit=1)
    assert len(limited) == 1


def test_canonical_orders_empty_when_no_executions_artifact(tmp_path: Path) -> None:
    run_id = _write_canonical_backtest(tmp_path)  # base fixture has no executions artifact
    repo = ResearchCockpitRepository(project_root=tmp_path)
    assert repo.get_backtest_orders(run_id) == []


def test_canonical_sections_derive_monthly_weekly_and_cost(tmp_path: Path) -> None:
    run_id = _write_canonical_backtest_with_executions(tmp_path)
    repo = ResearchCockpitRepository(project_root=tmp_path)

    payload = repo.get_backtest_sections(run_id)
    names = [section["name"] for section in payload["sections"]]
    assert "Performance" in names
    assert "Cost Analysis" in names

    monthly = payload["artifacts"]["monthly_returns"]
    assert monthly == [
        {"month": "2021-01", "return": pytest.approx(9500000 / 9000000 - 1)},
        {"month": "2021-02", "return": pytest.approx(0.0)},
        {"month": "2026-07", "return": pytest.approx(0.0)},
    ]

    weekly = payload["artifacts"]["weekly_returns"]
    assert weekly[0] == {"week": "2021-01-04", "return": pytest.approx(9200000 / 9000000 - 1)}
    assert len(weekly) == 4

    cost = next(section for section in payload["sections"] if section["name"] == "Cost Analysis")
    exact_fees = 0.303 + 1.212 + 0.909
    assert cost["metrics"]["total_fees"] == pytest.approx(exact_fees)
    assert cost["metrics"]["annualized_turnover"] == pytest.approx(0.01)
    assert cost["metrics"]["fee_ratio"] == pytest.approx(exact_fees / 100000)
    assert cost["metrics"]["fees_as_pct_of_initial"] == pytest.approx(exact_fees / 10_000_000)


def test_canonical_cost_estimate_without_executions_uses_decimal_fee_rates(tmp_path: Path) -> None:
    """Without an executions artifact, fees are estimated from decimal fee rates.

    Manifest fee rates are fractions (0.0003 = 3bp), so a stale basis-point
    re-scaling would understate fees by orders of magnitude.
    """
    run_id = _write_canonical_backtest(tmp_path)
    run_dir = next((tmp_path / "data" / "research" / "backtests").glob("*/*"))
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["commission_bp"] = 0.0003
    manifest["stamp_duty_bp"] = 0.001
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    metrics_path = run_dir / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["turnover_total"] = 100_000
    metrics["trading_day_count"] = 252
    metrics_path.write_text(json.dumps(metrics), encoding="utf-8")

    repo = ResearchCockpitRepository(project_root=tmp_path)
    payload = repo.get_backtest_sections(run_id)
    cost = next(section for section in payload["sections"] if section["name"] == "Cost Analysis")
    expected_fees = 100_000 * (0.0003 + 0.001 * 0.5)
    assert cost["metrics"]["total_fees"] == pytest.approx(expected_fees)
    assert cost["metrics"]["fee_ratio"] == pytest.approx(expected_fees / 100_000)


def test_canonical_positions_derive_from_executions(tmp_path: Path) -> None:
    """Positions are reconstructed exactly from the executions artifact."""
    run_id = _write_canonical_backtest_with_executions(tmp_path)
    repo = ResearchCockpitRepository(project_root=tmp_path)

    # As of the first trade date the 600000.SH sell (2026-07-31) has not
    # happened yet, so both buys are still held.
    early = repo.get_backtest_positions(run_id, trade_date="2021-01-04")
    by_symbol = {row["instrument"]: row for row in early}
    assert set(by_symbol) == {"600000.SH", "600001.SH"}
    assert by_symbol["600000.SH"]["qty"] == pytest.approx(100)
    assert by_symbol["600000.SH"]["avg_cost"] == pytest.approx((100 * 10.1 + 0.303) / 100)
    assert by_symbol["600001.SH"]["qty"] == pytest.approx(200)
    assert by_symbol["600001.SH"]["avg_cost"] == pytest.approx((200 * 20.2 + 1.212) / 200)
    assert all(row["realized_pnl"] == pytest.approx(0) for row in early)

    # After the sell the 600000.SH position is closed (qty 0) and drops out of
    # the holdings view; the never-sold 600001.SH remains.
    final = repo.get_backtest_positions(run_id)
    assert [row["instrument"] for row in final] == ["600001.SH"]
    assert final[0]["qty"] == pytest.approx(200)
    assert final[0]["realized_pnl"] == pytest.approx(0)

    # The closed position still carries its realized gain in the derivation.
    positions = repo._derive_positions_from_executions(
        repo._read_canonical_executions(repo._get_canonical_backtest_source(run_id))
    )
    closed = positions["600000.SH"]
    assert closed["qty"] == pytest.approx(0)
    assert closed["realized_pnl"] == pytest.approx((30.3 - (100 * 10.1 + 0.303) / 100) * 100 - 0.909)


def test_canonical_positions_empty_when_no_executions(tmp_path: Path) -> None:
    run_id = _write_canonical_backtest(tmp_path)  # base fixture has no executions artifact
    repo = ResearchCockpitRepository(project_root=tmp_path)
    assert repo.get_backtest_positions(run_id) == []


def test_canonical_positions_partial_sell_reduces_cost_basis(tmp_path: Path) -> None:
    """A partial sell keeps the remaining lot's average cost and books realized pnl."""
    strategy_run_id = "partial_sell_top5__stablehash"
    backtest_id = "bt_2021-01-04_2026-07-31_stablehash"
    run_dir = tmp_path / "data" / "research" / "backtests" / strategy_run_id / backtest_id
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "artifact_type": "backtest_run",
                "strategy_run_id": strategy_run_id,
                "backtest_id": backtest_id,
                "effective_start_date": "2021-01-04",
                "effective_end_date": "2026-07-31",
                "artifacts": {"executions": {"path": "executions.csv", "complete": True, "schema_version": "backtest_executions_v1"}},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "executions.csv").write_text(
        "execution_id,trade_date,sequence,instrument,side,execution_phase,trade_reason,"
        "requested_qty,requested_price,execution_price_mode,reference_price,status,"
        "filled_qty,deal_price,gross_amount,commission,tax,total_fee,rejection_reason\n"
        "buy,2021-01-04,0,600000.SH,buy,entry,top_n_entry,100,10.0,open,10.0,filled,100,10.0,1000.0,1.0,0.0,1.0,\n"
        "sell,2021-02-01,0,600000.SH,sell,exit,score_delta_exit,50,12.0,open,12.0,filled,50,12.0,600.0,1.0,0.0,1.0,\n",
        encoding="utf-8",
    )
    repo = ResearchCockpitRepository(project_root=tmp_path)
    run_id = f"canonical__{strategy_run_id}__{backtest_id}"

    rows = repo.get_backtest_positions(run_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["qty"] == pytest.approx(50)
    avg_cost = (100 * 10.0 + 1.0) / 100
    assert row["avg_cost"] == pytest.approx(avg_cost)
    assert row["realized_pnl"] == pytest.approx((12.0 - avg_cost) * 50 - 1.0)


def _raw_daily_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": ["2024-01-02", "2024-01-03", "2024-01-04"],
            "open": [10.0, 12.0, 14.0],
            "high": [11.0, 13.0, 15.0],
            "low": [9.0, 11.0, 13.0],
            "close": [10.5, 12.5, 14.5],
            "volume": [1000.0, 1100.0, 1200.0],
            "factor": [1.0, 1.5, 2.0],
        }
    )


def test_load_bars_falls_back_to_raw_store_when_qlib_lacks_ohlc(tmp_path: Path) -> None:
    """Instruments whose qlib bin only carries `volume` get OHLC from the raw store."""
    run_id = _write_canonical_backtest_with_executions(tmp_path)
    repo = ResearchCockpitRepository(project_root=tmp_path)
    instrument_id = "600000.SH"
    raw = _raw_daily_frame()
    sparse = pd.DataFrame(
        {"volume": raw["volume"].values},
        index=pd.MultiIndex.from_tuples(
            [(d, instrument_id) for d in raw["trade_date"]],
            names=["trade_date", "ts_code"],
        ),
    )

    with patch.object(repo.research_view, "get_feature", return_value=sparse), patch.object(repo.store, "load_daily", return_value=raw):
        bars = repo._load_bars(instrument_id=instrument_id, trade_date="2024-01-04", price_mode="fq", start_date="2024-01-02", end_date="2024-01-04")

    assert len(bars) == 3
    # Forward adjustment matches ResearchDataView: ratio = factor / latest_factor.
    assert bars[0]["adj_close"] == pytest.approx(10.5 * (1.0 / 2.0))
    assert bars[0]["adj_open"] == pytest.approx(10.0 * (1.0 / 2.0))
    assert bars[2]["adj_close"] == pytest.approx(14.5)
    assert bars[0]["volume"] == pytest.approx(1000)


def test_load_bars_raw_mode_uses_unadjusted_ohlc_from_raw_store(tmp_path: Path) -> None:
    run_id = _write_canonical_backtest_with_executions(tmp_path)
    repo = ResearchCockpitRepository(project_root=tmp_path)
    instrument_id = "600000.SH"
    raw = _raw_daily_frame()
    sparse = pd.DataFrame(
        {"volume": raw["volume"].values},
        index=pd.MultiIndex.from_tuples(
            [(d, instrument_id) for d in raw["trade_date"]],
            names=["trade_date", "ts_code"],
        ),
    )

    with patch.object(repo.research_view, "get_feature", return_value=sparse), patch.object(repo.store, "load_daily", return_value=raw):
        bars = repo._load_bars(instrument_id=instrument_id, trade_date="2024-01-04", price_mode="raw", start_date="2024-01-02", end_date="2024-01-04")

    assert bars[0]["close"] == pytest.approx(10.5)
    assert bars[0]["open"] == pytest.approx(10.0)
    assert "adj_close" not in bars[0]


def test_behavior_episodes_assembler_layer(tmp_path: Path) -> None:
    """Episode diagnostics derive from the immutable executions artifact."""
    run_id = _write_canonical_backtest_with_executions(tmp_path)
    repo = ResearchCockpitRepository(project_root=tmp_path)
    with patch.object(repo.store, "load_daily", return_value=_raw_daily_frame()):
        payload = repo.get_behavior_episodes(run_id)
    assert payload["summary"]["total_episodes"] == 2  # 600000 closed, 600001 open
    by_symbol = {e["symbol"]: e for e in payload["episodes"]}
    assert by_symbol["600000.SH"]["exit_reason"] == "score_delta_exit"
    assert by_symbol["600001.SH"]["exit_reason"] == "open"
    # realized return is cash-weighted, fees included
    closed = by_symbol["600000.SH"]
    assert closed["realized_return"] == pytest.approx((100 * 30.3 - 0.909) / (100 * 10.1 + 0.303) - 1)


def test_behavior_episodes_empty_when_no_executions(tmp_path: Path) -> None:
    run_id = _write_canonical_backtest(tmp_path)  # base fixture has no executions artifact
    repo = ResearchCockpitRepository(project_root=tmp_path)
    with patch.object(repo.store, "load_daily", return_value=_raw_daily_frame()):
        payload = repo.get_behavior_episodes(run_id)
    assert payload["episodes"] == []
    assert payload["summary"]["total_episodes"] == 0


def test_behavior_episodes_404_unknown_run(tmp_path: Path) -> None:
    repo = ResearchCockpitRepository(project_root=tmp_path)
    with pytest.raises(FileNotFoundError, match="Unknown backtest run_id"):
        repo.get_behavior_episodes("canonical__zzz__zzz")
