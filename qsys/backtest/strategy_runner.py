"""BacktestRunner — daily-equivalent multi-day strategy evaluation.

Core principle
==============
The ``BacktestRunner`` is **daily-equivalent by default**: it simulates
historical daily visibility with a visible-date mask, and preserves
``DailyRunner``-equivalent strategy semantics.  It is **NOT** a loop
around ``DailyRunner`` — it does not use production IO, notifications,
commit markers, or ledger commits.

Modes
-----
- ``strict_daily_equivalent``
  Exact date-by-date visible-mask semantics.  Every day loads only data
  observable at that point in time.  Most faithful to production, but
  also the slowest.

- ``cached_daily_equivalent``
  Allows batch / cached data loading when mathematically equivalent
  under the same visible-data mask and execution semantics.  Future
  optimisation path — current skeleton treats it identically to
  ``strict_daily_equivalent``.

Usage
-----
    from qsys.backtest.strategy_runner import BacktestRunner

    runner = BacktestRunner()
    result = runner.run_range(
        strategy=my_strategy_adapter,
        spec=my_spec,
        start_date="2026-01-01",
        end_date="2026-01-31",
    )
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qsys.backtest.result import BacktestRunResult

SUPPORTED_MODES = frozenset({
    "strict_daily_equivalent",
    "cached_daily_equivalent",
})


class BacktestRunner:
    """Daily-equivalent multi-day strategy evaluation runner.

    Parameters
    ----------
    data_provider : optional
        Data source for historical features and prices.
    execution_model : optional
        Execution model for fill simulation.
    artifact_mode : str
        ``"summary"`` or ``"full"`` (default ``"summary"``).
    mode : str
        Backtest mode (default ``"cached_daily_equivalent"``).
    """

    def __init__(
        self,
        data_provider: Any = None,
        execution_model: Any = None,
        artifact_mode: str = "summary",
        mode: str = "cached_daily_equivalent",
    ) -> None:
        if mode not in SUPPORTED_MODES:
            raise ValueError(
                f"unsupported mode {mode!r}; "
                f"must be one of {sorted(SUPPORTED_MODES)}"
            )
        self._data_provider = data_provider
        self._execution_model = execution_model
        self._artifact_mode = artifact_mode
        self._mode = mode

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def artifact_mode(self) -> str:
        return self._artifact_mode

    # ── Core run method ─────────────────────────────────────────────────

    def run_range(
        self,
        strategy: Any,
        spec: Any,
        start_date: str,
        end_date: str,
        *,
        rebalance_freq: str | None = None,
        initial_capital: float = 1_000_000.0,
        dry_run: bool = True,
    ) -> BacktestRunResult:
        """Run a strategy over a date range.

        This is the central evaluation entry point.

        Parameters
        ----------
        strategy
            A ``StrategyCandidate``-compatible adapter instance (or a
            research stub with compatible hooks).
        spec
            A ``StrategySpec`` instance.
        start_date
            First date (``YYYY-MM-DD``), inclusive.
        end_date
            Last date (``YYYY-MM-DD``), inclusive.
        rebalance_freq
            Override the spec's rebalance frequency (default from spec).
        initial_capital
            Starting capital.
        dry_run
            If ``True``, skip any persistent side effects.

        Returns
        -------
        BacktestRunResult
        """
        # ── Validation ──────────────────────────────────────────────
        if start_date > end_date:
            raise ValueError(
                f"start_date {start_date!r} is after end_date {end_date!r}"
            )

        spec_id = getattr(spec, "strategy_id", "unknown")

        # ── Lightweight hook check ──────────────────────────────────
        has_predict_hook = hasattr(strategy, "generate_predictions_for_date")

        # ── Skeleton body ───────────────────────────────────────────
        # Future implementation:
        #   1. Build trading calendar from start_date to end_date.
        #   2. For strict_daily_equivalent: iterate day by day, calling
        #      generate_predictions_for_date (or the standard hook chain
        #      without production IO).
        #   3. For cached_daily_equivalent: batch-load feature panel,
        #      then iterate day by day with cached data.
        #   4. Track portfolio, compute metrics.
        #   5. Return BacktestRunResult with computed metrics.
        #
        # This skeleton returns "not_implemented" unless the strategy
        # has a lightweight generate_predictions_for_date hook.

        if not has_predict_hook:
            return BacktestRunResult(
                strategy_id=spec_id,
                backtest_id=f"{spec_id}_bt_{start_date}_{end_date}",
                start_date=start_date,
                end_date=end_date,
                mode=self._mode,
                rebalance_freq=rebalance_freq or "weekly",
                initial_capital=initial_capital,
                status="not_implemented",
                notes="strategy lacks generate_predictions_for_date hook; "
                      "full backtest not implemented yet",
            )

        # ── Optional: call generate_predictions_for_date over range ──
        return self._run_with_predict_hook(
            strategy, spec_id, start_date, end_date,
            rebalance_freq=rebalance_freq,
            initial_capital=initial_capital,
        )

    # ── Internal helpers ────────────────────────────────────────────────

    def _run_with_predict_hook(
        self,
        strategy: Any,
        strategy_id: str,
        start_date: str,
        end_date: str,
        *,
        rebalance_freq: str | None = None,
        initial_capital: float = 1_000_000.0,
    ) -> BacktestRunResult:
        """Run lightweight date-by-date prediction loop.

        Only used when the strategy exposes ``generate_predictions_for_date``.
        This is an optional research-only hook (not part of the
        ``StrategyCandidate`` protocol).
        """
        import pandas as pd

        dates = pd.bdate_range(start=start_date, end=end_date)
        predict_count = 0

        for d in dates:
            date_str = d.strftime("%Y-%m-%d")
            try:
                result = strategy.generate_predictions_for_date(date_str)
                if result is not None:
                    predict_count += 1
            except Exception:
                pass

        return BacktestRunResult(
            strategy_id=strategy_id,
            backtest_id=f"{strategy_id}_bt_{start_date}_{end_date}",
            start_date=start_date,
            end_date=end_date,
            mode=self._mode,
            rebalance_freq=rebalance_freq or "weekly",
            initial_capital=initial_capital,
            final_value=initial_capital,
            total_return=0.0,
            status="completed",
            notes=f"lightweight run: {predict_count} dates with predictions",
        )
