"""BacktestRunner — daily-equivalent multi-day strategy evaluation.

Core principle
==============
The ``BacktestRunner`` is **daily-equivalent by default**: it simulates
historical daily visibility with a visible-date mask, and preserves
``DailyRunner``-equivalent strategy semantics.  It is **NOT** a loop
around ``DailyRunner`` — it does not use production IO, notifications,
commit markers, or ledger commits.

Execution price modes
---------------------
``open`` (default)
  DailyRunner-equivalent: plan at close → execute at T-day open →
  MTM at T-day close.  This matches the preopen/postclose two-phase
  pipeline.

``close``
  Legacy ``run_shadow_rebalance``-equivalent: plan and execute both
  use T-day close prices.  Single-phase, no open-close spread effect.

Modes
-----
- ``strict_daily_equivalent``
  Exact date-by-date visible-mask semantics.  Every day loads only data
  observable at that point in time.  Most faithful to production, but
  also the slowest.

- ``cached_daily_equivalent``
  Allows batch / cached data loading when mathematically equivalent
  under the same visible-data mask and execution semantics.  Future
  optimisation path — current implementation calls the same code path
  as ``strict_daily_equivalent``.

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

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.backtest.result import BacktestRunResult
from qsys.backtest.daily_kernel import should_skip_weekly_rebalance
from qsys.backtest._execution import (
    EXECUTION_ARTIFACT_COLUMNS,
    EXECUTION_ARTIFACT_SCHEMA_VERSION,
)
from qsys.backtest.posterior_policy import (
    PosteriorPolicyConfig,
    PosteriorPolicyState,
    prepare_posterior_signal_views,
    run_posterior_policy_day,
)
from qsys.ops.market_snapshot import fetch_market_snapshot
from qsys.ops.plan_builder import build_order_intents
from qsys.ops.shadow_execution import positions_frame
from qsys.signal.store import SignalStore
from qsys.strategy.allocation.rank_weight import build_rank_weight_targets
from qsys.trader.account import Account
from qsys.trader.matcher import MatchEngine

SUPPORTED_MODES = frozenset({
    "strict_daily_equivalent",
    "cached_daily_equivalent",
})

SUPPORTED_ARTIFACT_MODES = frozenset({"summary", "debug"})

SUPPORTED_EXECUTION_PRICE_MODES = frozenset({"open", "close"})


def _resolve_trading_dates(start_date: str, end_date: str) -> list[str]:
    """Resolve trading dates in [*start_date*, *end_date*] (inclusive).

    Delegates to ``qsys.data.calendar.get_trading_calendar`` which handles
    qlib resolution with ``pd.bdate_range`` fallback.
    """
    from qsys.data.calendar import get_trading_calendar

    return get_trading_calendar(start_date, end_date)


class BacktestRunner:
    """Daily-equivalent multi-day strategy evaluation runner.

    Parameters
    ----------
    data_provider : optional
        Data source for historical features and prices.
    execution_model : optional
        Execution model for fill simulation.
    artifact_mode : str
        ``"summary"`` or ``"debug"`` (default ``"summary"``).
    mode : str
        Backtest mode (default ``"cached_daily_equivalent"``).
    execution_price_mode : str
        ``"open"`` (default) for DailyRunner-equivalent open-execution /
        close-MTM, or ``"close"`` for legacy ``run_shadow_rebalance``-
        equivalent close-price execution.
    """

    def __init__(
        self,
        data_provider: Any = None,
        execution_model: Any = None,
        artifact_mode: str = "summary",
        mode: str = "cached_daily_equivalent",
        execution_price_mode: str = "open",
    ) -> None:
        if mode not in SUPPORTED_MODES:
            raise ValueError(
                f"unsupported mode {mode!r}; "
                f"must be one of {sorted(SUPPORTED_MODES)}"
            )
        if artifact_mode not in SUPPORTED_ARTIFACT_MODES:
            raise ValueError(
                f"unsupported artifact_mode {artifact_mode!r}; "
                f"must be one of {sorted(SUPPORTED_ARTIFACT_MODES)}"
            )
        if execution_price_mode not in SUPPORTED_EXECUTION_PRICE_MODES:
            raise ValueError(
                f"unsupported execution_price_mode {execution_price_mode!r}; "
                f"must be one of {sorted(SUPPORTED_EXECUTION_PRICE_MODES)}"
            )
        self._data_provider = data_provider
        self._execution_model = execution_model
        self._artifact_mode = artifact_mode
        self._mode = mode
        self._execution_price_mode = execution_price_mode
        self._last_trade_date: str | None = None
        self._rebalance_freq: str = "daily"

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def artifact_mode(self) -> str:
        return self._artifact_mode

    @property
    def execution_price_mode(self) -> str:
        return self._execution_price_mode

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
        output_dir: str | Path | None = None,
        initial_account: Account | None = None,
    ) -> BacktestRunResult:
        """Run a strategy over a date range.

        This is the central evaluation entry point.

        Parameters
        ----------
        strategy
            A ``StrategyCandidate``-compatible adapter instance.  Must expose
            ``generate_predictions_for_date`` and ``build_plan_for_backtest``
            for full backtest support.
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
        output_dir
            Optional directory for per-run output.  If provided, writes
            ``backtest_result.json`` and (when ``artifact_mode="debug"``)
            per-day artifacts.
        initial_account
            Optional pre-configured ``Account``.  If omitted, creates an
            empty account with *initial_capital*.

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
        backtest_id = f"{spec_id}_bt_{start_date}_{end_date}"
        output_path = Path(output_dir) if output_dir else None

        if output_path:
            output_path.mkdir(parents=True, exist_ok=True)

        # ── Lightweight hook check ──────────────────────────────────
        has_bt_hooks = (
            hasattr(strategy, "generate_predictions_for_date")
            and callable(getattr(strategy, "generate_predictions_for_date", None))
            and hasattr(strategy, "build_plan_for_backtest")
            and callable(getattr(strategy, "build_plan_for_backtest", None))
        )
        if not has_bt_hooks:
            return self._return_not_implemented(
                spec_id, backtest_id, start_date, end_date,
                rebalance_freq=rebalance_freq,
                initial_capital=initial_capital,
            )

        # ── Resolve trading dates ───────────────────────────────────
        trading_dates = _resolve_trading_dates(start_date, end_date)
        trading_dates = [d for d in trading_dates if start_date <= d <= end_date]

        # ── Initial account ─────────────────────────────────────────
        account = initial_account or Account(init_cash=initial_capital)

        # ── Per-run state ───────────────────────────────────────────
        self._last_prices: dict[str, float] = {}
        self._tmp_dirs: list[tempfile.TemporaryDirectory] = []
        self._last_trade_date = None
        self._rebalance_freq = (
            rebalance_freq
            or (isinstance(getattr(spec, "portfolio", None), dict) and spec.portfolio.get("rebalance_freq"))
            or "daily"
        )

        # ── Daily loop ──────────────────────────────────────────────
        daily_summaries: list[dict[str, Any]] = []
        daily_debug_dir = (
            output_path / "daily" if output_path and self._artifact_mode == "debug"
            else None
        )

        try:
            for trade_date in trading_dates:
                day_result = self._run_one_day(
                    strategy=strategy,
                    account=account,
                    trade_date=trade_date,
                    backtest_id=backtest_id,
                    debug_dir=daily_debug_dir,
                )
                daily_summaries.append(day_result)
        finally:
            for td in self._tmp_dirs:
                td.cleanup()
            self._tmp_dirs.clear()

        # ── Compute final metrics ───────────────────────────────────
        final_mv = account.get_market_value({})
        # Need prices for accurate market value — use last day's close
        if daily_summaries:
            last = daily_summaries[-1]
            final_value = last.get("total_value_after", account.cash + final_mv)
        else:
            final_value = account.cash + final_mv

        total_return = (
            (final_value / initial_capital) - 1.0 if initial_capital > 0 else 0.0
        )

        result = BacktestRunResult(
            strategy_id=spec_id,
            backtest_id=backtest_id,
            start_date=start_date,
            end_date=end_date,
            mode=self._mode,
            rebalance_freq=self._rebalance_freq,
            initial_capital=initial_capital,
            final_value=final_value,
            total_return=total_return,
            status="completed",
            daily_summary=daily_summaries,
            notes=f"backtest over {len(trading_dates)} trading days; "
                  f"mode={self._mode}; artifact_mode={self._artifact_mode}; "
                  f"execution_price={self._execution_price_mode}",
        )

        # ── Write output ────────────────────────────────────────────
        if output_path:
            self._write_summary(result, output_path)

        return result

    # ── Per-day execution ──────────────────────────────────────────────

    def _run_one_day(
        self,
        *,
        strategy: Any,
        account: Account,
        trade_date: str,
        backtest_id: str,
        current_prices: dict[str, float] | None = None,
        debug_dir: Path | None = None,
    ) -> dict[str, Any]:
        """Execute one trading day: predict → plan → execute → record.

        Price mode is controlled by ``self._execution_price_mode``:

        ``"open"`` (default)
          DailyRunner-equivalent: execute at T open, MTM at T close.
          ``build_plan_for_backtest`` (called before this method) uses
          close prices for plan construction.

        ``"close"``
          Legacy ``run_shadow_rebalance``-equivalent: execute and MTM
          both use T close prices.
        """
        # Resolve data date (previous trading day — preopen semantics)
        data_date = strategy.resolve_preopen_data_date(trade_date)

        # ── Rebalance frequency check ───────────────────────────────
        # For weekly rebalance, only execute on the first trading day of
        # each ISO week.  Subsequent days MTM at close without trading.
        if should_skip_weekly_rebalance(self._rebalance_freq, trade_date, self._last_trade_date):
            # Same ISO week — skip execution but still MTM at close,
            # matching DR's postclose behavior on non-rebalance days.
            if account.positions:
                instruments = list(account.positions.keys())
                try:
                    mtm_prices, _ = fetch_market_snapshot(
                        trade_date, instruments, price_col="close",
                    )
                    self._last_prices = mtm_prices
                    pos_frame = positions_frame(account, mtm_prices)
                    mv = float(pos_frame["market_value"].sum()) if not pos_frame.empty else 0.0
                    tv = float(account.cash + mv)
                except Exception:
                    return self._empty_day(trade_date, data_date, account, "weekly_rebalance_skip")
            else:
                mv = 0.0
                tv = float(account.cash)
            self._last_trade_date = trade_date
            return {
                "trade_date": trade_date,
                "data_date": data_date,
                "execution_price_mode": self._execution_price_mode,
                "cash_before": float(account.cash),
                "market_value_before": mv,
                "total_value_before": tv,
                "cash_after": float(account.cash),
                "market_value_after": mv,
                "total_value_after": tv,
                "order_count": 0, "buy_count": 0, "sell_count": 0,
                "filled_count": 0, "rejected_count": 0, "turnover": 0.0,
                "position_count": len(account.positions),
                "status": "weekly_rebalance_skip",
            }

        # 1. Generate predictions
        predictions = strategy.generate_predictions_for_date(
            trade_date, data_date=data_date,
        )
        if predictions is None or (isinstance(predictions, pd.DataFrame) and predictions.empty):
            return self._empty_day(trade_date, data_date, account, "no_predictions")

        # 2. Build plan via strategy hook (writes target_weights/order_intents to plan_dir)
        if debug_dir:
            day_out = debug_dir / trade_date
        else:
            _td = tempfile.TemporaryDirectory()
            self._tmp_dirs.append(_td)
            day_out = Path(_td.name)
        day_out.mkdir(parents=True, exist_ok=True)

        plan_dir = strategy.build_plan_for_backtest(
            predictions, account, trade_date, output_dir=day_out,
        )

        # 3. Read plan artifacts
        target_df = pd.read_csv(
            plan_dir / "target_weights.csv",
            dtype={"instrument": str},
        )
        target_weights = dict(zip(target_df["instrument"], target_df["target_weight"]))
        rebalance_audit = pd.read_csv(
            plan_dir / "rebalance_audit.csv",
            dtype={"instrument": str},
        )

        instruments = sorted(
            set(predictions["instrument"].astype(str))
            | set(account.positions.keys())
        )

        # 4. Fetch execution prices and execute
        try:
            if self._execution_price_mode == "open":
                exec_prices, market_status = fetch_market_snapshot(
                    trade_date, instruments, price_col="open",
                )
            else:
                exec_prices, market_status = fetch_market_snapshot(
                    trade_date, instruments,
                )
        except Exception as exc:
            return self._empty_day(
                trade_date, data_date, account,
                f"no_market_data: {exc}",
            )

        # 5. Execute — DailyRunner-equivalent vs legacy path
        if self._execution_price_mode == "open":
            mtm_prices, _ = fetch_market_snapshot(
                trade_date, instruments, price_col="close",
            )
            saved_intents = pd.read_csv(
                plan_dir / "order_intents.csv",
                dtype={"instrument": str},
            )
            orders = []
            for _, row in saved_intents.iterrows():
                inst = str(row["instrument"])
                side = str(row["side"])
                qty = int(float(row.get("requested_qty", 0)))
                if qty <= 0:
                    continue
                price = float(exec_prices.get(inst, 0.0))
                if price <= 0.0:
                    continue
                orders.append({
                    "symbol": inst, "side": side, "amount": qty,
                    "price": price, "order_type": "market",
                })
            mtm_pr = mtm_prices
            exec_pr = exec_prices
            mtm_st = market_status
        else:
            orders = build_order_intents(
                account, predictions, target_weights, exec_prices, trade_date,
            )[0]
            mtm_pr = exec_prices
            exec_pr = exec_prices
            mtm_st = market_status

        from qsys.backtest._execution import execute_trade_day
        day_result = execute_trade_day(
            account, orders, exec_pr, mtm_st, mtm_pr, trade_date,
            slippage=0.0,
            execution_price_mode=self._execution_price_mode,
        )
        day_result["data_date"] = data_date
        self._last_prices = mtm_pr
        self._last_trade_date = trade_date

        # Write debug artifacts
        if debug_dir:
            with open(day_out / "execution_summary.json", "w") as f:
                json.dump(day_result, f, indent=2, default=str)
            from qsys.ops.shadow_execution import positions_frame
            positions_frame(account, self._last_prices).to_csv(day_out / "positions_after.csv", index=False)
            from qsys.utils.json_io import write_json

            write_json(day_out / "account_after.json", {
                "trade_date": trade_date,
                "cash": day_result["cash_after"],
                "available_cash": day_result["cash_after"],
                "market_value": day_result["market_value_after"],
                "total_value": day_result["total_value_after"],
                "last_run_id": backtest_id,
                "initial_capital": account.init_cash,
            })
            if predictions is not None and not predictions.empty:
                predictions.to_csv(day_out / "predictions.csv", index=False)

        return day_result

    # ── Helpers ────────────────────────────────────────────────────────

    def _empty_day(
        self, trade_date: str, data_date: str, account: Account, reason: str,
    ) -> dict[str, Any]:
        """Record a non-trading day.

        Existing positions are valued at last known prices when available
        (from ``self._last_prices``).  If no prior prices exist, positions
        are valued at 0 and the status suffix ``_no_valuation`` is appended.
        """
        if self._last_prices and account.positions:
            pos_frame = positions_frame(account, self._last_prices)
            status_reason = reason
        else:
            pos_frame = pd.DataFrame(columns=["instrument", "market_value"])
            status_reason = (
                f"{reason}_no_valuation"
                if account.positions else reason
            )

        mv = float(pos_frame["market_value"].sum()) if not pos_frame.empty else 0.0
        return {
            "trade_date": trade_date,
            "data_date": data_date,
            "execution_price_mode": self._execution_price_mode,
            "cash_before": float(account.cash),
            "market_value_before": mv,
            "total_value_before": float(account.cash + mv),
            "cash_after": float(account.cash),
            "market_value_after": mv,
            "total_value_after": float(account.cash + mv),
            "order_count": 0,
            "buy_count": 0,
            "sell_count": 0,
            "filled_count": 0,
            "rejected_count": 0,
            "turnover": 0.0,
            "position_count": len(account.positions),
            "status": status_reason,
        }

    def _return_not_implemented(
        self,
        spec_id: str,
        backtest_id: str,
        start_date: str,
        end_date: str,
        *,
        rebalance_freq: str | None = None,
        initial_capital: float = 1_000_000.0,
    ) -> BacktestRunResult:
        return BacktestRunResult(
            strategy_id=spec_id,
            backtest_id=backtest_id,
            start_date=start_date,
            end_date=end_date,
            mode=self._mode,
            rebalance_freq=rebalance_freq or "weekly",
            initial_capital=initial_capital,
            status="not_implemented",
            notes="strategy lacks generate_predictions_for_date and/or "
                  "build_plan_for_backtest hooks; full backtest not implemented",
        )

    def run_accumulate(
        self,
        *,
        signal_id: str,
        signal_run_id: str,
        start_date: str,
        end_date: str,
        initial_capital: float = 10_000_000.0,
        score_column: str = "score",
        top_n: int = 20,
        commission: float = 0.0003,
        stamp_duty: float = 0.001,
        min_commission: float = 5.0,
        slippage: float = 0.001,
        rebalance_freq: str = "weekly",
        strategy_template_id: str = "rank_weight_top20",
        output_dir: Path | None = None,
        artifact_mode: str = "summary",
        overwrite: bool = False,
        research_root: str | Path = "data/research",
        stop_loss: float | None = None,
        trailing_stop: float | None = None,
        use_adjusted_price: bool = False,
        signal_id_2: str | None = None,
        signal_run_id_2: str | None = None,
        blend_weight: float = 1.0,
        maxdd_signal_id: str | None = None,
        maxdd_signal_run_id: str | None = None,
        maxdd_threshold: float | None = None,
        maxdd_percentile: float | None = None,
    ) -> BacktestRunResult:
        """Accumulate-mode backtest: never sell based on signal decay.

        Only buys to fill up to *top_n*.  Positions exit only via
        stop-loss or trailing-stop.  Equal cash allocation per buy
        (one-shot budget, dev-script compatible).

        Shares price loading, signal loading, blend, no-lookahead check,
        and stop-loss logic with ``run_from_signal_cache`` but uses its
        own trading loop (no ``build_order_intents`` / ``execute_trade_day``).
        """
        self._artifact_mode = artifact_mode
        if start_date > end_date:
            raise ValueError(f"start_date {start_date!r} is after end_date {end_date!r}")
        if (signal_id_2 is None) != (signal_run_id_2 is None):
            raise ValueError(
                "signal_id_2 and signal_run_id_2 must be provided together"
            )
        if not 0.0 <= blend_weight <= 1.0:
            raise ValueError(
                f"blend_weight must be within [0, 1], got {blend_weight}"
            )
        if use_adjusted_price:
            raise ValueError(
                "use_adjusted_price=True is unsafe: synthetic adjusted prices "
                "cannot be used for A-share lot sizing, cash settlement, or "
                "fees. Use raw execution prices; corporate actions remain an "
                "explicit research limitation."
            )

        signal_store = SignalStore(str(research_root))
        primary_identity = signal_store.validate_backtest_source(
            signal_id, signal_run_id
        )
        primary_signal = signal_store.load_signal_run(
            signal_id, signal_run_id, start_date=start_date, end_date=end_date
        )
        primary_by_date = {
            str(date): frame.reset_index(drop=True)
            for date, frame in primary_signal.groupby("trade_date", sort=False)
        }
        secondary_identity: dict[str, Any] | None = None
        secondary_by_date: dict[str, pd.DataFrame] = {}
        if signal_id_2 and signal_run_id_2:
            secondary_identity = signal_store.validate_backtest_source(
                signal_id_2, signal_run_id_2
            )
            secondary_signal = signal_store.load_signal_run(
                signal_id_2,
                signal_run_id_2,
                start_date=start_date,
                end_date=end_date,
            )
            secondary_by_date = {
                str(date): frame.reset_index(drop=True)
                for date, frame in secondary_signal.groupby(
                    "trade_date", sort=False
                )
            }

        hash_input = json.dumps(
            {
                "mode": "accumulate",
                "strategy_template_id": strategy_template_id,
                "top_n": top_n,
                "commission": commission,
                "stamp_duty": stamp_duty,
                "min_commission": min_commission,
                "slippage": slippage,
                "rebalance_freq": rebalance_freq,
                "start_date": start_date,
                "end_date": end_date,
                "initial_capital": initial_capital,
                "score_column": score_column,
                "execution_price_mode": self._execution_price_mode,
                "use_adjusted_price": use_adjusted_price,
                "stop_loss": stop_loss,
                "trailing_stop": trailing_stop,
                "blend_weight": blend_weight,
                "primary_signal": primary_identity,
                "secondary_signal": secondary_identity,
                "maxdd_signal_id": maxdd_signal_id,
                "maxdd_signal_run_id": maxdd_signal_run_id,
                "maxdd_threshold": maxdd_threshold,
                "maxdd_percentile": maxdd_percentile,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        short_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:8]
        strategy_run_id = f"accumulate_{strategy_template_id}__{signal_id}__{short_hash}"
        backtest_id = f"acc_{start_date}_{end_date}_{short_hash}"

        if output_dir is None:
            output_dir = Path(research_root) / "backtests" / strategy_run_id / backtest_id
        output_dir = Path(output_dir)
        if output_dir.exists() and not overwrite:
            raise FileExistsError(f"Backtest output dir exists: {output_dir} (use overwrite=True)")
        output_dir.mkdir(parents=True, exist_ok=True)

        trading_dates = _resolve_trading_dates(start_date, end_date)
        trading_dates = [d for d in trading_dates if start_date <= d <= end_date]
        if not trading_dates:
            raise ValueError(f"No trading dates in range [{start_date}, {end_date}]")

        account = Account(init_cash=initial_capital)
        daily_summaries: list[dict[str, Any]] = []
        self._last_prices = {}
        self._last_trade_date = None
        self._position_peaks: dict[str, float] = {}

        for trade_date in trading_dates:
            mtm_prices: dict[str, float] = {}
            if account.positions:
                try:
                    mtm_prices, _ = fetch_market_snapshot(
                        trade_date, list(account.positions.keys()),
                        price_col="close" if self._execution_price_mode == "open" else "close",
                    )
                except Exception:
                    pass

            # Weekly skip
            if should_skip_weekly_rebalance(rebalance_freq, trade_date, self._last_trade_date):
                cash_before = float(account.cash)
                mv_before = float(sum(max(0, mtm_prices.get(code, 0)) * pos.total_amount for code, pos in account.positions.items())) if mtm_prices else 0.0
                tv_before = cash_before + mv_before
                sl_result = self._stop_loss_check(account, mtm_prices, stop_loss, trailing_stop, slippage, commission, min_commission, stamp_duty)
                stop_events = int(sl_result["stop_events"])
                self._last_trade_date = trade_date
                mv_after = float(sum(max(0, mtm_prices.get(code, 0)) * pos.total_amount for code, pos in account.positions.items())) if mtm_prices else 0.0
                daily_summaries.append({
                    "trade_date": trade_date, "execution_price_mode": self._execution_price_mode,
                    "cash_before": cash_before, "market_value_before": mv_before, "total_value_before": tv_before,
                    "cash_after": float(account.cash), "market_value_after": mv_after,
                    "total_value_after": float(account.cash) + mv_after,
                    "order_count": 0, "buy_count": 0,
                    "sell_count": stop_events,
                    "filled_count": stop_events,
                    "rejected_count": 0,
                    "turnover": sl_result["stop_turnover"],
                    "position_count": len(account.positions), "stop_events": stop_events,
                    "status": "weekly_rebalance_skip",
                })
                continue

            # Load signal
            day_signal = primary_by_date.get(trade_date, pd.DataFrame()).copy()
            if day_signal.empty:
                cash_before = float(account.cash)
                mv_before = float(sum(max(0, mtm_prices.get(code, 0)) * pos.total_amount for code, pos in account.positions.items())) if mtm_prices else 0.0
                sl_result = self._stop_loss_check(account, mtm_prices, stop_loss, trailing_stop, slippage, commission, min_commission, stamp_duty)
                stop_events = int(sl_result["stop_events"])
                self._last_trade_date = trade_date
                mv_after = float(sum(max(0, mtm_prices.get(code, 0)) * pos.total_amount for code, pos in account.positions.items())) if mtm_prices else 0.0
                daily_summaries.append({
                    "trade_date": trade_date, "execution_price_mode": self._execution_price_mode,
                    "cash_before": cash_before, "market_value_before": mv_before,
                    "total_value_before": cash_before + mv_before,
                    "cash_after": float(account.cash), "market_value_after": mv_after,
                    "total_value_after": float(account.cash) + mv_after,
                    "order_count": 0, "buy_count": 0,
                    "sell_count": stop_events,
                    "filled_count": stop_events,
                    "rejected_count": 0,
                    "turnover": sl_result["stop_turnover"],
                    "position_count": len(account.positions),
                    "stop_events": stop_events, "status": "no_signal_data",
                })
                continue

            # No-lookahead check
            if "data_date" in day_signal.columns and "trade_date" in day_signal.columns:
                day_signal["_dd"] = pd.to_datetime(day_signal["data_date"]).dt.strftime("%Y-%m-%d")
                day_signal["_td"] = pd.to_datetime(day_signal["trade_date"]).dt.strftime("%Y-%m-%d")
                _v = day_signal[day_signal["_dd"] >= day_signal["_td"]]
                if len(_v) > 0:
                    raise ValueError(f"Signal lookahead at {trade_date}: {len(_v)} rows")
                day_signal.drop(columns=["_dd", "_td"], inplace=True)

            # Optional blend
            if signal_id_2 and signal_run_id_2 and blend_weight < 1.0:
                day_signal_2 = secondary_by_date.get(
                    trade_date, pd.DataFrame()
                ).copy()
                if day_signal_2.empty:
                    raise ValueError(
                        f"Secondary SignalRun has no rows for rebalance date "
                        f"{trade_date}; refusing to degrade the fixed blend"
                    )
                if "data_date" in day_signal_2.columns and "trade_date" in day_signal_2.columns:
                    day_signal_2["_dd"] = pd.to_datetime(day_signal_2["data_date"]).dt.strftime("%Y-%m-%d")
                    day_signal_2["_td"] = pd.to_datetime(day_signal_2["trade_date"]).dt.strftime("%Y-%m-%d")
                    if len(day_signal_2[day_signal_2["_dd"] >= day_signal_2["_td"]]) > 0:
                        raise ValueError(f"Signal-2 lookahead at {trade_date}")
                    day_signal_2.drop(columns=["_dd", "_td"], inplace=True)
                sc2 = score_column + "_2"
                join_keys = ["trade_date", "data_date", "instrument"]
                day_signal_2 = day_signal_2[join_keys + [score_column]].rename(columns={score_column: sc2})
                day_signal = day_signal.merge(day_signal_2, on=join_keys, how="inner")
                if day_signal.empty:
                    raise ValueError(
                        f"Signal blend has no common rows for {trade_date}"
                    )
                day_signal[score_column] = blend_weight * day_signal[score_column] + (1 - blend_weight) * day_signal[sc2]
                day_signal = day_signal.drop(columns=[sc2])

            # Fetch prices
            instruments = sorted(set(day_signal["instrument"].astype(str)) | set(account.positions.keys()))
            try:
                if self._execution_price_mode == "open":
                    exec_prices_raw, market_status = fetch_market_snapshot(trade_date, instruments, price_col="open")
                    mtm_prices_raw, _ = fetch_market_snapshot(trade_date, instruments, price_col="close")
                else:
                    exec_prices_raw, market_status = fetch_market_snapshot(trade_date, instruments)
                    mtm_prices_raw = exec_prices_raw
                exec_prices, mtm_prices = exec_prices_raw, mtm_prices_raw
            except Exception as exc:
                daily_summaries.append(dict(status="no_market_data"))
                continue

            # Before-state
            cash_before = float(account.cash)
            mv_before = float(sum(max(0, mtm_prices.get(c, 0)) * pos.total_amount for c, pos in account.positions.items()))
            tv_before = cash_before + mv_before

            # Buy to fill up to top_n (equal cash allocation, one-shot budget)
            orders: list[dict] = []
            if len(account.positions) < top_n and account.cash > 0:
                sorted_scores = day_signal.sort_values(score_column, ascending=False)
                held_set = set(account.positions.keys())
                slot_count = top_n - len(account.positions)
                buy_codes = sorted_scores[~sorted_scores["instrument"].isin(held_set)].head(slot_count if maxdd_percentile is None else 50)["instrument"].tolist()
                
                # ── MaxDD risk filter (absolute threshold or percentile) ──
                if (maxdd_threshold is not None or maxdd_percentile is not None) and maxdd_signal_id and buy_codes:
                    try:
                        maxdd_sig = signal_store.load_signal_for_date(maxdd_signal_id, maxdd_signal_run_id, trade_date)
                        maxdd_sig = None if maxdd_sig.empty else maxdd_sig
                    except Exception:
                        maxdd_sig = None

                    if maxdd_sig is not None:
                        # No-lookahead check — violation raises, not swallowed
                        if "data_date" in maxdd_sig.columns and "trade_date" in maxdd_sig.columns:
                            _dd = pd.to_datetime(maxdd_sig["data_date"]).dt.strftime("%Y-%m-%d")
                            _td = pd.to_datetime(maxdd_sig["trade_date"]).dt.strftime("%Y-%m-%d")
                            _v = maxdd_sig[_dd >= _td]
                            if len(_v) > 0:
                                raise ValueError(f"Maxdd signal lookahead at {trade_date}: {len(_v)} rows")

                        maxdd_map = dict(zip(maxdd_sig["instrument"], maxdd_sig["score"]))
                        if maxdd_threshold is not None:
                            buy_codes = [c for c in buy_codes if maxdd_map.get(c, 0) < maxdd_threshold]
                        else:
                            scores = maxdd_sig["score"].dropna()
                            threshold = float(scores.quantile(maxdd_percentile)) if len(scores) > 1 else 1.0
                            buy_codes = [c for c in buy_codes if maxdd_map.get(c, 0) < threshold]
                    # maxdd_sig is None → proceed without filter
                
                if buy_codes:
                    alloc_once = account.cash / len(buy_codes)
                    for code in buy_codes:
                        px = exec_prices.get(code)
                        if px is None or px <= 0:
                            continue
                        buy_px = px * (1 + slippage)
                        qty = int(alloc_once / buy_px / 100) * 100
                        while qty > 0:
                            total = qty * buy_px + max(min_commission, qty * buy_px * commission)
                            if total <= account.cash:
                                break
                            qty -= 100
                        if qty <= 0:
                            continue
                        fee = max(min_commission, qty * buy_px * commission)
                        account.update_after_deal(code, qty, buy_px, fee, "buy")
                        orders.append({"symbol": code, "side": "buy", "amount": qty, "price": buy_px})

            # MTM
            mv = float(sum(max(0, mtm_prices.get(c, 0)) * p.total_amount for c, p in account.positions.items()))
            day_result = {
                "trade_date": trade_date, "execution_price_mode": self._execution_price_mode,
                "cash_before": cash_before, "market_value_before": mv_before, "total_value_before": tv_before,
                "cash_after": float(account.cash), "market_value_after": mv,
                "total_value_after": float(account.cash) + mv,
                "order_count": len(orders), "buy_count": sum(1 for o in orders if o["side"] == "buy"),
                "sell_count": 0, "filled_count": len(orders), "rejected_count": 0,
                "turnover": sum(o["amount"] * o["price"] for o in orders),
                "position_count": len(account.positions), "stop_events": 0, "status": "success",
            }
            self._last_prices = mtm_prices
            self._last_trade_date = trade_date

            # Stop-loss
            sl_result = self._stop_loss_check(account, mtm_prices, stop_loss, trailing_stop, slippage, commission, min_commission, stamp_duty)
            stop_events = int(sl_result["stop_events"])
            day_result["stop_events"] = stop_events
            if stop_events > 0:
                mv2 = float(sum(max(0, mtm_prices.get(c, 0)) * p.total_amount for c, p in account.positions.items()))
                day_result.update({"cash_after": float(account.cash), "market_value_after": mv2,
                                   "total_value_after": float(account.cash) + mv2, "position_count": len(account.positions)})
            day_result["sell_count"] = day_result.get("sell_count", 0) + stop_events
            day_result["filled_count"] = day_result.get("filled_count", 0) + stop_events
            day_result["turnover"] = day_result.get("turnover", 0.0) + sl_result["stop_turnover"]
            daily_summaries.append(day_result)

        # Final metrics
        final_mv = sum(max(0, mtm_prices.get(c, 0)) * p.total_amount for c, p in account.positions.items()) if mtm_prices else 0
        final_value = daily_summaries[-1].get("total_value_after", account.cash + final_mv) if daily_summaries else account.cash
        total_return = (final_value / initial_capital) - 1.0 if initial_capital > 0 else 0.0

        # Manifest
        from qsys.research.manifest import with_standard_metadata, write_manifest
        manifest = with_standard_metadata({
            "artifact_type": "backtest_run", "backtest_id": backtest_id,
            "strategy_run_id": strategy_run_id, "strategy_template_id": strategy_template_id,
            "signal_id": signal_id, "signal_run_id": signal_run_id, "score_column": score_column,
            "allocation_method": "accumulate_equal_weight",
            "allocation_params": {"top_n": top_n, "mode": "accumulate"},
            "start_date": start_date, "end_date": end_date,
            "trading_day_count": len(trading_dates),
            "initial_capital": initial_capital, "final_value": final_value,
            "total_return": total_return,
            "stop_loss": stop_loss, "trailing_stop": trailing_stop,
            "use_adjusted_price": use_adjusted_price,
            "signal_id_2": signal_id_2, "signal_run_id_2": signal_run_id_2, "blend_weight": blend_weight,
            "signal_sources": [
                identity
                for identity in (primary_identity, secondary_identity)
                if identity is not None
            ],
            "price_adjustment_policy": "raw_execution_and_mtm",
            "corporate_action_policy": "not_modeled",
            "research_limitations": [
                "cash dividends, splits, and other corporate actions are not modeled",
                "current-universe historical runs contain survivorship bias unless the source declares PIT membership",
            ],
        })
        write_manifest(output_dir / "manifest.json", manifest)
        if daily_summaries:
            pd.DataFrame(daily_summaries).to_csv(output_dir / "daily_summary.csv", index=False)

        result = BacktestRunResult(
            strategy_id=strategy_template_id, backtest_id=backtest_id,
            start_date=start_date, end_date=end_date,
            mode="cached_signal", rebalance_freq=rebalance_freq,
            initial_capital=initial_capital, final_value=final_value,
            total_return=total_return, status="completed",
            daily_summary=daily_summaries,
            notes=f"accumulate backtest over {len(trading_dates)} dates",
        )
        self._write_summary(result, output_dir)
        return result


    @staticmethod
    def _write_summary(result: BacktestRunResult, output_path: Path) -> None:
        """Write backtest result summary JSON."""
        from qsys.utils.json_io import write_json

        summary = {
            "strategy_id": result.strategy_id,
            "backtest_id": result.backtest_id,
            "start_date": result.start_date,
            "end_date": result.end_date,
            "mode": result.mode,
            "rebalance_freq": result.rebalance_freq,
            "initial_capital": result.initial_capital,
            "final_value": result.final_value,
            "total_return": result.total_return,
            "status": result.status,
            "daily_count": len(result.daily_summary),
        }
        write_json(output_path / "backtest_result.json", summary)

        if result.daily_summary:
            pd.DataFrame(result.daily_summary).to_csv(
                output_path / "daily_summary.csv", index=False,
            )

    # ── PR109: cached-signal backtest ─────────────────────────────────────
    # ── Helpers for run_from_signal_cache ───────────────────────────────

    def _stop_loss_check(self, account: Account, mtm_prices: dict[str, float],
                         stop_loss: float | None, trailing_stop: float | None,
                         slippage: float, commission: float, min_commission: float,
                         stamp_duty: float = 0.0) -> dict[str, float]:
        """Check and execute stop-loss/trailing-stop.

        Returns
        -------
        dict with keys: stop_events, stop_turnover, stop_fee, stop_tax
        """
        result = {"stop_events": 0, "stop_turnover": 0.0, "stop_fee": 0.0, "stop_tax": 0.0}
        if (stop_loss is None and trailing_stop is None) or not account.positions:
            return result
        for sym in list(account.positions.keys()):
            pos = account.positions.get(sym)
            if pos is None or pos.total_amount <= 0:
                continue
            px = mtm_prices.get(sym)
            if px is None or px <= 0:
                continue
            cost = pos.avg_cost
            pnl = px / cost - 1
            if sym in self._position_peaks:
                self._position_peaks[sym] = max(self._position_peaks[sym], px)
            else:
                self._position_peaks[sym] = px
            do_sell = False
            if stop_loss is not None and pnl < -abs(stop_loss):
                do_sell = True
            if not do_sell and trailing_stop is not None and pnl > 0 and px < self._position_peaks[sym] * (1 - abs(trailing_stop)):
                do_sell = True
            if do_sell:
                qty = pos.total_amount
                gross = qty * px
                rev = gross * (1 - slippage)
                fee = max(min_commission, rev * commission)
                tax = rev * stamp_duty
                account.cash += rev - fee - tax
                account.positions.pop(sym, None)
                self._position_peaks.pop(sym, None)
                result["stop_events"] += 1
                result["stop_turnover"] += rev
                result["stop_fee"] += fee
                result["stop_tax"] += tax
        return result

    def run_from_signal_cache(
        self,
        *,
        signal_id: str,
        signal_run_id: str,
        start_date: str,
        end_date: str,
        initial_capital: float = 10_000_000.0,
        allocation_method: str = "rank_weight",
        score_column: str = "score",
        top_n: int = 20,
        max_weight: float | None = None,
        commission: float = 0.0003,
        stamp_duty: float = 0.001,
        min_commission: float = 5.0,
        slippage: float = 0.001,
        rebalance_freq: str = "weekly",
        strategy_template_id: str = "rank_weight_top20",
        output_dir: Path | None = None,
        artifact_mode: str = "summary",
        overwrite: bool = False,
        research_root: str | Path = "data/research",
        stop_loss: float | None = None,
        trailing_stop: float | None = None,
        use_adjusted_price: bool = False,
        signal_id_2: str | None = None,
        signal_run_id_2: str | None = None,
        blend_weight: float = 1.0,
        holding_policy: str = "target_rebalance",
        score_delta_lookback: int = 20,
        score_delta_quantile: float = 0.10,
        score_delta_history_days: int = 504,
        score_delta_min_observations: int = 500,
        posterior_stop_loss: float = 0.09,
        winner_activation_return: float = 0.20,
        winner_trailing_stop: float = 0.125,
        stale_after_days: int = 20,
        stale_max_return: float = 0.03,
        replacement_rank_gap: int = 20,
        rank_exit: bool = False,
        exposure_gate_mode: str = "none",
        exposure_gate_scale: float = 0.5,
        exposure_gate_schedule: dict[str, bool] | None = None,
    ) -> BacktestRunResult:
        """Backtest from a saved SignalRun (no model inference).

        Parameters
        ----------
        signal_id, signal_run_id:
            Identifies the saved SignalRun in SignalStore.
        signal_id_2, signal_run_id_2:
            Optional second SignalRun for blended signals.
        blend_weight:
            Weight for primary signal (0.0-1.0). Secondary signal gets (1 - blend_weight).
            Only used when signal_id_2 is provided.
        start_date, end_date:
            Date range (YYYY-MM-DD), inclusive.
        initial_capital:
            Starting capital.
        allocation_method:
            Allocation method (``rank_weight`` only).
        score_column:
            Column used for ranking.
        top_n:
            Number of positions to hold.
        max_weight:
            Optional per-stock weight cap.
        strategy_template_id:
            Template identifier (stored in manifest).
        output_dir:
            Output directory.  When ``None``, constructed under
            ``data/research/backtests/`` or a tmp dir.
        artifact_mode:
            ``"summary"`` or ``"debug"``.
        overwrite:
            When ``False``, raise ``FileExistsError`` if output dir exists.
        stop_loss:
            Stop-loss threshold, e.g. 0.07 = sell if position drops 7% from cost.
            When ``None`` (default), no stop-loss is applied.
        trailing_stop:
            Trailing stop threshold, e.g. 0.10 = sell if price falls 10% from
            peak since entry.  When ``None``, no trailing stop.
        use_adjusted_price:
            Reserved legacy flag. ``True`` is rejected because synthetic
            adjusted prices are invalid for lot sizing and cash settlement.
            Raw prices are used and corporate actions are declared as an
            explicit limitation in the manifest.

        Returns
        -------
        BacktestRunResult
        """
        self._artifact_mode = artifact_mode
        if start_date > end_date:
            raise ValueError(
                f"start_date {start_date!r} is after end_date {end_date!r}"
            )
        if (signal_id_2 is None) != (signal_run_id_2 is None):
            raise ValueError(
                "signal_id_2 and signal_run_id_2 must be provided together"
            )
        if not 0.0 <= blend_weight <= 1.0:
            raise ValueError(
                f"blend_weight must be within [0, 1], got {blend_weight}"
            )
        if use_adjusted_price:
            raise ValueError(
                "use_adjusted_price=True is unsafe: synthetic adjusted prices "
                "cannot be used for A-share lot sizing, cash settlement, or "
                "fees. Use raw execution prices; corporate actions remain an "
                "explicit research limitation."
            )
        if holding_policy not in {"target_rebalance", "posterior_confirmed"}:
            raise ValueError(
                "holding_policy must be 'target_rebalance' or "
                "'posterior_confirmed'"
            )
        if top_n < 1:
            raise ValueError("top_n must be positive")
        posterior_config: PosteriorPolicyConfig | None = None
        if holding_policy == "posterior_confirmed":
            if signal_id_2 is not None:
                raise ValueError(
                    "posterior_confirmed requires a materialized single "
                    "SignalRun; combine inputs before backtesting"
                )
            if stop_loss is not None or trailing_stop is not None:
                raise ValueError(
                    "legacy stop_loss/trailing_stop cannot be combined with "
                    "posterior_confirmed policy controls"
                )
            posterior_config = PosteriorPolicyConfig(
                score_delta_lookback=score_delta_lookback,
                score_delta_quantile=score_delta_quantile,
                score_delta_history_days=score_delta_history_days,
                score_delta_min_observations=score_delta_min_observations,
                posterior_stop_loss=posterior_stop_loss,
                winner_activation_return=winner_activation_return,
                winner_trailing_stop=winner_trailing_stop,
                stale_after_days=stale_after_days,
                stale_max_return=stale_max_return,
                replacement_rank_gap=replacement_rank_gap,
                rank_exit=rank_exit,
                exposure_gate_mode=exposure_gate_mode,
                exposure_gate_scale=exposure_gate_scale,
            )
            posterior_config.validate()

        signal_store = SignalStore(str(research_root))
        primary_identity = signal_store.validate_backtest_source(
            signal_id, signal_run_id
        )
        primary_signal = signal_store.load_signal_run(
            signal_id,
            signal_run_id,
            start_date=None if posterior_config is not None else start_date,
            end_date=end_date,
        )
        primary_by_date = {
            str(date): frame.reset_index(drop=True)
            for date, frame in primary_signal.groupby("trade_date", sort=False)
        }
        secondary_identity: dict[str, Any] | None = None
        secondary_by_date: dict[str, pd.DataFrame] = {}
        if signal_id_2 and signal_run_id_2:
            secondary_identity = signal_store.validate_backtest_source(
                signal_id_2, signal_run_id_2
            )
            secondary_signal = signal_store.load_signal_run(
                signal_id_2,
                signal_run_id_2,
                start_date=start_date,
                end_date=end_date,
            )
            secondary_by_date = {
                str(date): frame.reset_index(drop=True)
                for date, frame in secondary_signal.groupby(
                    "trade_date", sort=False
                )
            }

        # ── Build run/backtest IDs ────────────────────────────────────────
        # Cached signal semantics:
        # - signal.trade_date = intended_execution_date
        # - allocation before open, execution at open, MTM at close
        # - Preopen-equivalent, NOT BacktestEngine next-open convention
        hash_payload = {
            "strategy_template_id": strategy_template_id,
            "allocation_method": allocation_method,
            "top_n": top_n,
            "max_weight": max_weight,
            "commission": commission,
            "stamp_duty": stamp_duty,
            "min_commission": min_commission,
            "slippage": slippage,
            "rebalance_freq": rebalance_freq,
            "start_date": start_date,
            "end_date": end_date,
            "initial_capital": initial_capital,
            "score_column": score_column,
            "execution_price_mode": self._execution_price_mode,
            "use_adjusted_price": use_adjusted_price,
            "stop_loss": stop_loss,
            "trailing_stop": trailing_stop,
            "blend_weight": blend_weight,
            "primary_signal": primary_identity,
            "secondary_signal": secondary_identity,
        }
        if posterior_config is not None:
            hash_payload["holding_policy"] = holding_policy
            hash_payload["posterior_policy"] = posterior_config.to_manifest()
        schedule_digest: str | None = None
        if exposure_gate_schedule is not None:
            schedule_digest = hashlib.sha256(
                json.dumps(
                    exposure_gate_schedule, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest()
            hash_payload["exposure_gate_schedule_sha256"] = schedule_digest
            hash_payload["exposure_gate_days"] = sum(
                1 for active in exposure_gate_schedule.values() if active
            )
            if not all(
                isinstance(d, str) and isinstance(a, bool)
                for d, a in exposure_gate_schedule.items()
            ):
                raise ValueError(
                    "exposure_gate_schedule must map date (YYYY-MM-DD) -> bool"
                )
        hash_input = json.dumps(
            hash_payload, sort_keys=True, separators=(",", ":")
        )
        short_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:8]
        strategy_run_id = f"{strategy_template_id}__{signal_id}__{signal_run_id}__{short_hash}"
        backtest_id = f"bt_{start_date}_{end_date}_{short_hash}"

        # ── Resolve output path ───────────────────────────────────────────
        if output_dir is None:
            output_dir = Path(research_root) / "backtests" / strategy_run_id / backtest_id
        output_dir = Path(output_dir)
        if output_dir.exists() and not overwrite:
            raise FileExistsError(
                f"Backtest output dir exists: {output_dir} (use overwrite=True)"
            )
        output_dir.mkdir(parents=True, exist_ok=True)

        # ── Resolve trading dates ─────────────────────────────────────────
        trading_dates = _resolve_trading_dates(start_date, end_date)
        trading_dates = [d for d in trading_dates if start_date <= d <= end_date]
        if not trading_dates:
            raise ValueError(f"No trading dates in range [{start_date}, {end_date}]")

        # ── Account ───────────────────────────────────────────────────────
        account = Account(init_cash=initial_capital)

        # ── Daily loop ────────────────────────────────────────────────────
        daily_summaries: list[dict[str, Any]] = []
        execution_rows: list[dict[str, Any]] = []
        daily_debug_dir = output_dir / "daily" if artifact_mode == "debug" else None
        self._last_prices = {}
        self._last_trade_date = None
        self._last_rebalance_date: str | None = None
        self._position_peaks: dict[str, float] = {}
        posterior_state: PosteriorPolicyState | None = None
        posterior_views = None
        if posterior_config is not None:
            if "data_date" in primary_signal.columns:
                signal_trade_dates = pd.to_datetime(
                    primary_signal["trade_date"], errors="coerce"
                )
                signal_data_dates = pd.to_datetime(
                    primary_signal["data_date"], errors="coerce"
                )
                invalid = (
                    signal_trade_dates.isna()
                    | signal_data_dates.isna()
                    | signal_data_dates.ge(signal_trade_dates)
                )
                if invalid.any():
                    sample = primary_signal.loc[
                        invalid, ["trade_date", "data_date"]
                    ].head(3).to_dict(orient="records")
                    raise ValueError(
                        "Signal lookahead violation in posterior policy; "
                        f"examples: {sample}"
                    )
            posterior_state = PosteriorPolicyState()
            posterior_view_dates = sorted(
                date for date in primary_by_date if date <= end_date
            )
            posterior_views = prepare_posterior_signal_views(
                primary_by_date,
                posterior_view_dates,
                score_column=score_column,
                config=posterior_config,
            )

        for trading_index, trade_date in enumerate(trading_dates):
            if posterior_config is not None:
                assert posterior_state is not None
                assert posterior_views is not None
                day_signal = primary_by_date.get(
                    trade_date, pd.DataFrame()
                ).copy()
                previous_trade_date = (
                    trading_dates[trading_index - 1]
                    if trading_index > 0 else None
                )
                is_rebalance = not should_skip_weekly_rebalance(
                    rebalance_freq, trade_date, previous_trade_date,
                    trading_dates=trading_dates,
                    last_rebalance_date=self._last_rebalance_date,
                )
                day_result, targets, orders = run_posterior_policy_day(
                    account=account,
                    state=posterior_state,
                    config=posterior_config,
                    views=posterior_views,
                    day_signal=day_signal,
                    trade_date=trade_date,
                    trading_index=trading_index,
                    is_rebalance=is_rebalance,
                    top_n=top_n,
                    commission=commission,
                    stamp_duty=stamp_duty,
                    min_commission=min_commission,
                    slippage=slippage,
                    execution_price_mode=self._execution_price_mode,
                    market_snapshot_fn=fetch_market_snapshot,
                    execution_collector=execution_rows,
                    exposure_gate_schedule=exposure_gate_schedule,
                )
                if is_rebalance:
                    # Cadence anchor for "<n>d" refresh: the next rebalance
                    # counts trading days strictly after this date.
                    self._last_rebalance_date = trade_date
                self._last_trade_date = trade_date
                daily_summaries.append(day_result)
                if daily_debug_dir:
                    day_dir = daily_debug_dir / trade_date
                    day_dir.mkdir(parents=True, exist_ok=True)
                    day_signal.to_csv(day_dir / "signal.csv", index=False)
                    targets.to_csv(day_dir / "target_weights.csv", index=False)
                    if orders:
                        pd.DataFrame(orders).to_csv(
                            day_dir / "order_intents.csv", index=False
                        )
                continue
            # ── Shared: fetch MTM prices for any existing positions ──
            mtm_prices: dict[str, float] = {}
            if account.positions:
                try:
                    mtm_prices, _ = fetch_market_snapshot(
                        trade_date, list(account.positions.keys()),
                        price_col="close" if self._execution_price_mode == "open" else "close",
                    )
                except Exception:
                    pass

            # 1. Rebalance-cadence check (weekly, daily, or "<n>d" trading days)
            is_rebalance = not should_skip_weekly_rebalance(
                rebalance_freq, trade_date, self._last_trade_date,
                trading_dates=trading_dates,
                last_rebalance_date=self._last_rebalance_date,
            )

            if not is_rebalance:
                # Weekly skip — compute before, stop-loss, compute after
                if mtm_prices:
                    pos_before = positions_frame(account, mtm_prices)
                    mv_before = float(pos_before["market_value"].sum()) if not pos_before.empty else 0.0
                else:
                    mv_before = 0.0
                cash_before = float(account.cash)
                tv_before = cash_before + mv_before
                sl_result = self._stop_loss_check(account, mtm_prices, stop_loss, trailing_stop,
                                                         slippage, commission, min_commission, stamp_duty)
                stop_events = int(sl_result["stop_events"])
                self._last_trade_date = trade_date
                if mtm_prices:
                    pos_after = positions_frame(account, mtm_prices)
                    mv_after = float(pos_after["market_value"].sum()) if not pos_after.empty else 0.0
                else:
                    mv_after = 0.0
                daily_summaries.append({
                    "trade_date": trade_date,
                    "execution_price_mode": self._execution_price_mode,
                    "cash_before": cash_before,
                    "market_value_before": mv_before,
                    "total_value_before": tv_before,
                    "cash_after": float(account.cash),
                    "market_value_after": mv_after,
                    "total_value_after": float(account.cash) + mv_after,
                    "order_count": 0, "buy_count": 0,
                    "sell_count": int(sl_result["stop_events"]),
                    "filled_count": int(sl_result["stop_events"]),
                    "rejected_count": 0,
                    "turnover": sl_result["stop_turnover"],
                    "position_count": len(account.positions),
                    "stop_events": int(sl_result["stop_events"]),
                    "status": "weekly_rebalance_skip",
                })
                continue

            # 2. Load signal for this date
            day_signal = primary_by_date.get(trade_date, pd.DataFrame()).copy()
            if day_signal.empty:
                # Before-state, stop-loss, after-state
                if mtm_prices:
                    pos_before = positions_frame(account, mtm_prices)
                    mv_before = float(pos_before["market_value"].sum()) if not pos_before.empty else 0.0
                else:
                    mv_before = 0.0
                cash_before = float(account.cash)
                tv_before = cash_before + mv_before
                sl_result = self._stop_loss_check(account, mtm_prices, stop_loss, trailing_stop,
                                                                     slippage, commission, min_commission, stamp_duty)
                self._last_trade_date = trade_date
                if mtm_prices:
                    pos_after = positions_frame(account, mtm_prices)
                    mv_after = float(pos_after["market_value"].sum()) if not pos_after.empty else 0.0
                else:
                    mv_after = 0.0
                daily_summaries.append({
                    "trade_date": trade_date,
                    "execution_price_mode": self._execution_price_mode,
                    "cash_before": cash_before,
                    "market_value_before": mv_before,
                    "total_value_before": tv_before,
                    "cash_after": float(account.cash),
                    "market_value_after": mv_after,
                    "total_value_after": float(account.cash) + mv_after,
                    "order_count": 0, "buy_count": 0,
                    "sell_count": int(sl_result["stop_events"]),
                    "filled_count": int(sl_result["stop_events"]),
                    "rejected_count": 0,
                    "turnover": sl_result["stop_turnover"],
                    "position_count": len(account.positions),
                    "stop_events": int(sl_result["stop_events"]),
                    "status": "no_signal_data",
                })
                continue

            # 2b. No-lookahead check — primary signal
            if "data_date" in day_signal.columns and "trade_date" in day_signal.columns:
                day_signal["_dd"] = pd.to_datetime(day_signal["data_date"]).dt.strftime("%Y-%m-%d")
                day_signal["_td"] = pd.to_datetime(day_signal["trade_date"]).dt.strftime("%Y-%m-%d")
                _v = day_signal[day_signal["_dd"] >= day_signal["_td"]]
                if len(_v) > 0:
                    _ex = _v.head(3)[["trade_date", "data_date"]].to_dict(orient="records")
                    raise ValueError(f"Signal lookahead violation at {trade_date}: "
                                     f"{len(_v)} rows have data_date >= trade_date. Examples: {_ex}")
                day_signal.drop(columns=["_dd", "_td"], inplace=True)

            # 2c. Optional second signal blend
            if signal_id_2 and signal_run_id_2 and blend_weight < 1.0:
                day_signal_2 = secondary_by_date.get(
                    trade_date, pd.DataFrame()
                ).copy()
                if day_signal_2.empty:
                    raise ValueError(
                        f"Secondary SignalRun has no rows for rebalance date "
                        f"{trade_date}; refusing to degrade the fixed blend"
                    )
                # No-lookahead check on second signal too
                if "data_date" in day_signal_2.columns and "trade_date" in day_signal_2.columns:
                    day_signal_2["_dd"] = pd.to_datetime(day_signal_2["data_date"]).dt.strftime("%Y-%m-%d")
                    day_signal_2["_td"] = pd.to_datetime(day_signal_2["trade_date"]).dt.strftime("%Y-%m-%d")
                    _v2 = day_signal_2[day_signal_2["_dd"] >= day_signal_2["_td"]]
                    if len(_v2) > 0:
                        raise ValueError(f"Signal-2 lookahead at {trade_date}: {len(_v2)} bad rows")
                    day_signal_2.drop(columns=["_dd", "_td"], inplace=True)
                sc2 = score_column + "_2"
                join_keys = ["trade_date", "data_date", "instrument"]
                day_signal_2 = day_signal_2[join_keys + [score_column]].rename(
                    columns={score_column: sc2}
                )
                day_signal = day_signal.merge(
                    day_signal_2, on=join_keys, how="inner"
                )
                if day_signal.empty:
                    raise ValueError(
                        f"Signal blend has no common rows for {trade_date}"
                    )
                day_signal[score_column] = (
                    blend_weight * day_signal[score_column]
                    + (1 - blend_weight) * day_signal[sc2]
                )
                day_signal = day_signal.drop(columns=[sc2])

            # 3. Build target weights
            targets = build_rank_weight_targets(
                day_signal, trade_date=trade_date,
                score_column=score_column, top_n=top_n, max_weight=max_weight,
                normalize=True, allocation_method=allocation_method,
                strategy_id=strategy_template_id, signal_id=signal_id,
                signal_run_id=signal_run_id,
            )
            instruments = sorted(set(targets["instrument"]) | set(account.positions.keys()))

            # 4. Resolve execution prices
            try:
                if self._execution_price_mode == "open":
                    exec_prices_raw, market_status = fetch_market_snapshot(
                        trade_date, instruments, price_col="open",
                    )
                    mtm_prices_raw, _ = fetch_market_snapshot(
                        trade_date, instruments, price_col="close",
                    )
                else:
                    exec_prices_raw, market_status = fetch_market_snapshot(trade_date, instruments)
                    mtm_prices_raw = exec_prices_raw

                exec_prices, mtm_prices = exec_prices_raw, mtm_prices_raw
            except Exception as exc:
                daily_summaries.append(self._empty_day(
                    trade_date, trade_date, account, f"no_market_data: {exc}"
                ))
                continue

            # 5. Before-state
            pos_before = positions_frame(account, mtm_prices)
            cash_before = float(account.cash)
            mv_before = float(pos_before["market_value"].sum()) if not pos_before.empty else 0.0
            tv_before = cash_before + mv_before

            orders: list[dict] = []

            # 6. Build order intents
            orders, _, _, _, _, _ = build_order_intents(
                account, day_signal, targets.set_index("instrument")["target_weight"].to_dict(),
                exec_prices, trade_date,
            )
            for order in orders:
                order["execution_phase"] = "rebalance"
                order["trade_reason"] = "rebalance_to_target_weight"

            # 7. Execute, settle, MTM
            from qsys.backtest._execution import execute_trade_day
            day_result = execute_trade_day(
                account, orders, exec_prices, market_status, mtm_prices, trade_date,
                commission=commission, stamp_duty=stamp_duty,
                min_commission=min_commission, slippage=slippage,
                execution_price_mode=self._execution_price_mode,
                execution_collector=execution_rows,
            )
            self._last_prices = mtm_prices
            self._last_trade_date = trade_date
            self._last_rebalance_date = trade_date

            # 8. Stop-loss / trailing-stop (after MTM, same mtm_prices)
            sl_result = self._stop_loss_check(account, mtm_prices, stop_loss, trailing_stop,
                                              slippage, commission, min_commission, stamp_duty)
            stop_events = int(sl_result["stop_events"])
            day_result["stop_events"] = stop_events
            if stop_events > 0:
                pos_frame2 = positions_frame(account, mtm_prices)
                day_result["cash_after"] = float(account.cash)
                day_result["market_value_after"] = float(pos_frame2["market_value"].sum()) if not pos_frame2.empty else 0.0
                day_result["total_value_after"] = day_result["cash_after"] + day_result["market_value_after"]
                day_result["position_count"] = len(account.positions)
            day_result["sell_count"] = day_result.get("sell_count", 0) + stop_events
            day_result["filled_count"] = day_result.get("filled_count", 0) + stop_events
            day_result["turnover"] = day_result.get("turnover", 0.0) + sl_result["stop_turnover"]
            daily_summaries.append(day_result)

            # 9. Debug artifacts
            if daily_debug_dir:
                day_dir = daily_debug_dir / trade_date
                day_dir.mkdir(parents=True, exist_ok=True)
                day_signal.to_csv(day_dir / "signal.csv", index=False)
                targets.to_csv(day_dir / "target_weights.csv", index=False)
                if len(orders) > 0:
                    pd.DataFrame(orders).to_csv(day_dir / "order_intents.csv", index=False)
                from qsys.utils.json_io import write_json as _wj
                _wj(day_dir / "mtm_snapshot.json", {k: day_result[k] for k in (
                    "trade_date", "cash_after", "market_value_after",
                    "total_value_after", "position_count",
                )})
                _wj(day_dir / "execution_summary.json", day_result)

        # ── Compute final metrics ─────────────────────────────────────────
        final_mv = account.get_market_value({})
        if daily_summaries:
            last = daily_summaries[-1]
            final_value = last.get("total_value_after", account.cash + final_mv)
        else:
            final_value = account.cash + final_mv

        total_return = (final_value / initial_capital) - 1.0 if initial_capital > 0 else 0.0

        executions_path = output_dir / "executions.csv"
        pd.DataFrame(
            execution_rows, columns=EXECUTION_ARTIFACT_COLUMNS
        ).to_csv(executions_path, index=False)
        executions_sha256 = hashlib.sha256(
            executions_path.read_bytes()
        ).hexdigest()

        # ── Write manifest ────────────────────────────────────────────────
        from qsys.research.manifest import with_standard_metadata, write_manifest

        manifest = with_standard_metadata({
            "artifact_type": "backtest_run",
            "backtest_id": backtest_id,
            "stop_loss": stop_loss,
            "trailing_stop": trailing_stop,
            "use_adjusted_price": use_adjusted_price,
            "signal_id_2": signal_id_2,
            "signal_run_id_2": signal_run_id_2,
            "blend_weight": blend_weight,
            "signal_sources": [
                identity
                for identity in (primary_identity, secondary_identity)
                if identity is not None
            ],
            "strategy_run_id": strategy_run_id,
            "strategy_template_id": strategy_template_id,
            "signal_id": signal_id,
            "signal_run_id": signal_run_id,
            "score_column": score_column,
            "allocation_method": allocation_method,
            "allocation_params": {
                "top_n": top_n, "max_weight": max_weight,
            },
            "start_date": start_date,
            "end_date": end_date,
            "effective_start_date": trading_dates[0] if trading_dates else None,
            "effective_end_date": trading_dates[-1] if trading_dates else None,
            "trading_dates": trading_dates,
            "trading_day_count": len(trading_dates),
            "initial_capital": initial_capital,
            "final_value": final_value,
            "total_return": total_return,
            "model_mode": "cached_signal",
            "rolling_train": False,
            "execution_price": self._execution_price_mode,
            "execution_timing": "preopen",
            "signal_trade_date_semantics": "intended_execution_date",
            "mtm_price": "close",
            "price_adjustment_policy": "raw_execution_and_mtm",
            "corporate_action_policy": "not_modeled",
            "research_limitations": [
                "cash dividends, splits, and other corporate actions are not modeled",
                "current-universe historical runs contain survivorship bias unless the source declares PIT membership",
            ],
            "commission_bp": commission,
            "stamp_duty_bp": stamp_duty,
            "min_commission": min_commission,
            "slippage": slippage,
            "rebalance_freq": rebalance_freq,
            "data_cutoff_policy": "preopen_previous",
            "artifacts": {
                "executions": {
                    "path": "executions.csv",
                    "schema_version": EXECUTION_ARTIFACT_SCHEMA_VERSION,
                    "sha256": executions_sha256,
                    "row_count": len(execution_rows),
                    "complete": stop_loss is None and trailing_stop is None,
                }
            },
        })
        if posterior_config is not None:
            manifest["holding_policy"] = holding_policy
            manifest["posterior_policy"] = posterior_config.to_manifest()
            manifest["exposure_gate"] = {
                "mode": posterior_config.exposure_gate_mode,
                "scale": posterior_config.exposure_gate_scale,
            }
            if schedule_digest is not None:
                manifest["exposure_gate"]["schedule_sha256"] = schedule_digest
                manifest["exposure_gate"]["gated_days"] = sum(
                    1 for active in exposure_gate_schedule.values() if active
                )
                manifest["exposure_gate"]["total_days"] = len(
                    exposure_gate_schedule
                )
            manifest["allocation_method"] = "equal_weight_entry_hold_drift"
            manifest["allocation_params"] = {
                "top_n": top_n,
                "initial_entry_weight": 1.0 / top_n,
                "periodic_reweight": False,
                "max_weight": None,
            }
            manifest["posterior_policy_contract"] = {
                "score_delta": "score_t_minus_score_t_minus_N_trading_days",
                "delta_threshold": (
                    "pooled_quantile_from_strictly_prior_execution_dates"
                ),
                "price_decision": "previous_completed_close",
                "execution": "next_execution_date_open",
                "entry_allocation": "equal_weight_one_over_top_n",
                "rank_exit": (
                    "enabled_sell_dropouts_refill_top_n"
                    if posterior_config.rank_exit
                    else "disabled"
                ),
            }
        write_manifest(output_dir / "manifest.json", manifest)

        # ── Write daily_summary + metrics ─────────────────────────────────
        if daily_summaries:
            pd.DataFrame(daily_summaries).to_csv(
                output_dir / "daily_summary.csv", index=False,
            )

        # metrics.json
        _ot = sum(d.get("order_count", 0) for d in daily_summaries)
        _ft = sum(d.get("filled_count", 0) for d in daily_summaries)
        _rt = sum(d.get("rejected_count", 0) for d in daily_summaries)
        _tt = sum(d.get("turnover", 0) for d in daily_summaries)
        _td = len([d for d in daily_summaries if d.get("order_count", 0) > 0])
        _m = {
            "initial_capital": initial_capital, "final_value": final_value,
            "total_return": total_return, "trading_day_count": len(trading_dates),
            "trading_day_count_with_orders": _td,
            "order_count_total": _ot, "filled_count_total": _ft,
            "rejected_count_total": _rt, "avg_order_per_day": round(_ot / max(len(trading_dates), 1), 2),
            "turnover_total": round(_tt, 2), "avg_turnover": round(_tt / max(_td, 1), 2),
        }
        if posterior_config is not None:
            _m["policy_exit_count_total"] = sum(
                d.get("policy_exit_count", 0) for d in daily_summaries
            )
            _m["policy_entry_count_total"] = sum(
                d.get("policy_entry_count", 0) for d in daily_summaries
            )
            for reason in (
                "hard_stop", "score_delta", "winner_trailing",
                "stale_replacement",
            ):
                _m[f"{reason}_exit_count_total"] = sum(
                    d.get(f"{reason}_exit_count", 0)
                    for d in daily_summaries
                )
        write_manifest(output_dir / "metrics.json", with_standard_metadata(_m))

        result = BacktestRunResult(
            strategy_id=strategy_template_id,
            backtest_id=backtest_id,
            start_date=start_date,
            end_date=end_date,
            mode="cached_signal",
            rebalance_freq=rebalance_freq,
            initial_capital=initial_capital,
            final_value=final_value,
            total_return=total_return,
            status="completed",
            daily_summary=daily_summaries,
            artifacts={
                "manifest": str(output_dir / "manifest.json"),
                "daily_summary": str(output_dir / "daily_summary.csv") if daily_summaries else "",
                "metrics": str(output_dir / "metrics.json"),
            },
            notes=(
                f"cached-signal backtest over {len(trading_dates)} trading dates; "
                f"signal_id={signal_id}; signal_run_id={signal_run_id}; "
                f"top_n={top_n}; max_weight={max_weight}; "
                f"rebalance_freq={rebalance_freq}; slippage={slippage}"
                + (
                    f"; holding_policy={holding_policy}"
                    if posterior_config is not None else ""
                )
            ),
        )

        self._write_summary(result, output_dir)
        return result


# ── Legacy compatibility ───────────────────────────────────────────────────────

def _not_implemented_run(
    strategy: Any,
    spec: Any,
    start_date: str,
    end_date: str,
    **kwargs: Any,
) -> BacktestRunResult:
    """Standalone fallback when the strategy has no backtest hooks."""
    runner = BacktestRunner()
    return runner.run_range(strategy, spec, start_date, end_date, **kwargs)
