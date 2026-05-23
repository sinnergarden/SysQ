"""Backtest result dataclass — outcome of a single backtest run.

Distinct from the legacy ``qsys.backtest.engine.BacktestResult`` (which
stores raw daily/trade DataFrames).  This module defines the contract for
the new ``BacktestRunner``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class BacktestRunResult:
    """Outcome of a single ``BacktestRunner.run_range`` call.

    Fields mirror ``qsys.research.reports.BacktestResult`` for consistency,
    but this dataclass lives in the backtest package — it is the **internal
    return type** of ``BacktestRunner.run_range``, not a persisted report.
    """

    strategy_id: str
    backtest_id: str
    start_date: str
    end_date: str
    mode: str
    rebalance_freq: str
    initial_capital: float = 1_000_000.0
    final_value: float | None = None
    total_return: float | None = None
    annualized_return: float | None = None
    max_drawdown: float | None = None
    sharpe: float | None = None
    turnover: float | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    status: str = "success"
    notes: str | None = None
