from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from qsys.research.portfolio_analytics import (
    _return_metrics,
    _window_max_drawdown,
    write_portfolio_analytics,
)
from qsys.research.manifest import write_manifest
from qsys.signal.store import SignalStore


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_drawdown_is_anchored_at_initial_capital() -> None:
    returns = pd.Series([-0.10, 0.0])
    metrics = _return_metrics(
        returns,
        start_date=pd.Timestamp("2024-01-02"),
        end_date=pd.Timestamp("2024-01-03"),
    )
    assert metrics["total_return"] == pytest.approx(-0.10)
    assert metrics["max_drawdown"] == pytest.approx(-0.10)
    assert _window_max_drawdown(returns.to_numpy()) == pytest.approx(-0.10)


def test_portfolio_analytics_binds_sources_and_computes_required_metrics(
    tmp_path: Path,
) -> None:
    research_root = tmp_path / "research"
    signal_id = "quality_signal"
    signal_run_id = "confirmation_2023_2024"
    dates = pd.bdate_range("2024-01-02", periods=125)
    predictions = pd.DataFrame([
        {
            "trade_date": date.strftime("%Y-%m-%d"),
            "data_date": (date - pd.offsets.BDay(1)).strftime("%Y-%m-%d"),
            "instrument": instrument,
            "signal_id": signal_id,
            "signal_run_id": signal_run_id,
            "score": float(rank),
        }
        for date in dates
        for rank, instrument in enumerate(("A", "B", "C", "D", "E", "F"), 1)
    ])
    SignalStore(research_root).save_signal_run(
        signal_id, signal_run_id, predictions, check_no_lookahead=False
    )
    signal_manifest_path = (
        research_root / "signals" / signal_id / signal_run_id / "manifest.json"
    )
    signal_manifest = json.loads(signal_manifest_path.read_text())

    output = research_root / "backtests" / "stage_c" / "top20_base"
    output.mkdir(parents=True)
    equity_path = [
        1_000_000.0 + index * 1_000.0 - (30_000.0 if 40 <= index < 50 else 0.0)
        for index in range(len(dates))
    ]
    daily = pd.DataFrame({
        "trade_date": dates.strftime("%Y-%m-%d"),
        "cash_after": [100_000.0] * len(dates),
        "market_value_after": [value - 100_000.0 for value in equity_path],
        "total_value_after": equity_path,
        "position_count": [2] * len(dates),
        "turnover": [10_000.0] * len(dates),
    })
    daily.to_csv(output / "daily_summary.csv", index=False)
    executions = pd.DataFrame({
        "status": ["filled", "rejected"],
        "gross_amount": [100_000.0, 0.0],
        "commission": [30.0, 0.0],
        "tax": [0.0, 0.0],
        "total_fee": [30.0, 0.0],
        "participation_rate": [0.01, 0.2],
    })
    executions.to_csv(output / "executions.csv", index=False)
    valuation = pd.DataFrame([
        {
            "trade_date": date.strftime("%Y-%m-%d"),
            "instrument": instrument,
            "market_value": value,
        }
        for date in dates
        for instrument, value in (("A", 500_000.0), ("B", 400_000.0))
    ])
    valuation.to_csv(output / "valuation_ledger.csv", index=False)
    write_manifest(output / "metrics.json", {
        "turnover_total": 1_250_000.0,
        "turnover_annualized": 1.25,
        "rebalance_due_day_count": 7,
        "rebalance_executed_day_count": 7,
    })
    manifest = {
        "artifact_type": "backtest_run",
        "backtest_id": "bt_test",
        "strategy_run_id": "stage_c_top20_base",
        "signal_id": signal_id,
        "signal_run_id": signal_run_id,
        "score_column": "score",
        "start_date": dates.min().strftime("%Y-%m-%d"),
        "end_date": dates.max().strftime("%Y-%m-%d"),
        "initial_capital": 1_000_000.0,
        "allocation_params": {"top_n": 20},
        "rebalance_freq": "20d",
        "commission_bp": 0.0003,
        "stamp_duty_bp": 0.001,
        "min_commission": 5.0,
        "slippage": 0.001,
        "accounting": {"schema_version": "accounting_v1"},
    }
    write_manifest(output / "manifest.json", manifest)
    benchmark = pd.DataFrame({
        "trade_date": dates.strftime("%Y%m%d"),
        "open": [100.0 + index * 0.1 for index in range(len(dates))],
        "close": [100.05 + index * 0.1 for index in range(len(dates))],
    })
    benchmark_path = tmp_path / "000906.SH.csv"
    benchmark.to_csv(benchmark_path, index=False)

    result = write_portfolio_analytics(
        backtest_dir=output,
        research_root=research_root,
        benchmark_id="csi800",
        benchmark_csv=benchmark_path,
        holdout_start="2025-01-02",
    )

    analytics = json.loads((output / "portfolio_analytics.json").read_text())
    analytics_manifest = json.loads(
        (output / "portfolio_analytics_manifest.json").read_text()
    )
    assert analytics["performance"]["calmar"] is not None
    assert (
        analytics["performance"]["historical_cvar_95_daily"]
        <= analytics["performance"]["historical_var_95_daily"]
    )
    assert {row["year"] for row in analytics["annual_returns"]} == {2024}
    assert set(analytics["rolling"]) == {"60", "120"}
    assert analytics["exposure_and_concentration"]["gross_exposure"]["mean"] > 0
    assert set(analytics["topn_selection_stability"]["by_k"]) == {"5"}
    assert analytics["regime_contract"]["information_lag_sessions"] == 1
    assert analytics["benchmark"]["capm"]["observations"] == len(dates)
    assert analytics["benchmark"]["capm"]["beta"] is not None
    assert analytics["benchmark"]["capm_contract"] == (
        "daily_ols_zero_risk_free_rate_v1"
    )
    assert analytics["holdout_consumed"] is False
    assert analytics["inputs"]["benchmark_sha256"] == _sha256(benchmark_path)
    assert analytics["inputs"]["predictions_sha256"] == signal_manifest["predictions_sha256"]
    assert analytics_manifest["outputs"]["portfolio_analytics.json"]["sha256"] == _sha256(
        output / "portfolio_analytics.json"
    )
    assert result["portfolio_analytics_identity_sha256"] == analytics_manifest[
        "portfolio_analytics_identity_sha256"
    ]

    named = write_portfolio_analytics(
        backtest_dir=output,
        research_root=research_root,
        benchmark_id="csi1800_proxy",
        benchmark_csv=benchmark_path,
        holdout_start="2025-01-02",
        output_name="csi1800_proxy",
    )
    named_dir = output / "portfolio_analytics" / "csi1800_proxy"
    assert Path(named["analytics"]) == named_dir / "portfolio_analytics.json"
    assert (named_dir / "portfolio_analytics_manifest.json").is_file()
    assert (output / "portfolio_analytics.json").is_file()


def test_portfolio_analytics_rejects_unsafe_output_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="safe path segment"):
        write_portfolio_analytics(
            backtest_dir=tmp_path,
            research_root=tmp_path,
            benchmark_id="benchmark",
            benchmark_csv=tmp_path / "benchmark.csv",
            holdout_start="2025-01-02",
            output_name="../escape",
        )
