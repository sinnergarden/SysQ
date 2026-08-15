from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

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
