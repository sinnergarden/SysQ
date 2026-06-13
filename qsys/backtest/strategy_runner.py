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
    ) -> BacktestRunResult:
        """Backtest from a saved SignalRun (no model inference).

        Parameters
        ----------
        signal_id, signal_run_id:
            Identifies the saved SignalRun in SignalStore.
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

        Returns
        -------
        BacktestRunResult
        """
        self._artifact_mode = artifact_mode
        if start_date > end_date:
            raise ValueError(
                f"start_date {start_date!r} is after end_date {end_date!r}"
            )

        # ── Build run/backtest IDs ────────────────────────────────────────
        # Cached signal semantics:
        # - signal.trade_date = intended_execution_date
        # - allocation before open, execution at open, MTM at close
        # - Preopen-equivalent, NOT BacktestEngine next-open convention
        hash_input = f"{strategy_template_id}_{signal_id}_{signal_run_id}_{allocation_method}_{top_n}_{max_weight}_{commission}_{stamp_duty}_{min_commission}_{slippage}_{rebalance_freq}_{start_date}_{end_date}_{initial_capital}"  # noqa: E501
        short_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:8]
        strategy_run_id = f"{strategy_template_id}__{signal_id}__{signal_run_id}__{short_hash}"
        backtest_id = f"bt_{start_date}_{end_date}_{short_hash}"

        # ── Resolve output path ───────────────────────────────────────────
        if output_dir is None:
            output_dir = Path("data/research/backtests") / strategy_run_id / backtest_id
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

        # ── Signal store ──────────────────────────────────────────────────
        signal_store = SignalStore(str(research_root))

        # ── Account ───────────────────────────────────────────────────────
        account = Account(init_cash=initial_capital)

        # ── Daily loop ────────────────────────────────────────────────────
        daily_summaries: list[dict[str, Any]] = []
        daily_debug_dir = output_dir / "daily" if artifact_mode == "debug" else None
        self._last_prices = {}
        self._last_trade_date = None

        for trade_date in trading_dates:
            # 1. Weekly rebalance check
            if should_skip_weekly_rebalance(rebalance_freq, trade_date, self._last_trade_date):
                # Same ISO week — MTM at close, skip trading
                if account.positions:
                    insts = list(account.positions.keys())
                    try:
                        if self._execution_price_mode == "open":
                            mtm_prices, _ = fetch_market_snapshot(trade_date, insts, price_col="close")
                        else:
                            mtm_prices, _ = fetch_market_snapshot(trade_date, insts)
                        self._last_prices = mtm_prices
                        pos_frame = positions_frame(account, mtm_prices)
                        mv = float(pos_frame["market_value"].sum()) if not pos_frame.empty else 0.0
                        tv = float(account.cash + mv)
                    except Exception:
                        daily_summaries.append(self._empty_day(
                            trade_date, trade_date, account, "no_market_data"
                        ))
                        self._last_trade_date = trade_date
                        continue
                else:
                    mv = 0.0
                    tv = float(account.cash)
                self._last_trade_date = trade_date
                daily_summaries.append({
                    "trade_date": trade_date,
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
                })
                continue

            # 2. Load signal for this date
            day_signal = signal_store.load_signal_for_date(signal_id, signal_run_id, trade_date)
            if day_signal.empty:
                daily_summaries.append(self._empty_day(
                    trade_date, trade_date, account, "no_signal_data"
                ))
                self._last_trade_date = trade_date
                continue

            # 2b. No-lookahead check (preopen semantics: data_date < trade_date)
            if "data_date" in day_signal.columns and "trade_date" in day_signal.columns:
                # Normalize to YYYY-MM-DD string (handles pd.Timestamp, datetime64)
                day_signal["_dd"] = pd.to_datetime(day_signal["data_date"]).dt.strftime("%Y-%m-%d")
                day_signal["_td"] = pd.to_datetime(day_signal["trade_date"]).dt.strftime("%Y-%m-%d")
                _v = day_signal[day_signal["_dd"] >= day_signal["_td"]]
                if len(_v) > 0:
                    _ex = _v.head(3)[["trade_date", "data_date"]].to_dict(orient="records")
                    raise ValueError(
                        f"Signal lookahead violation at {trade_date}: "
                        f"{len(_v)} rows have data_date >= trade_date. "
                        f"Examples: {_ex}"
                    )
                day_signal.drop(columns=["_dd", "_td"], inplace=True)

            # 3. Build target_weights via allocation boundary
            targets = build_rank_weight_targets(
                day_signal,
                trade_date=trade_date,
                score_column=score_column,
                top_n=top_n,
                max_weight=max_weight,
                normalize=True,
                allocation_method=allocation_method,
                strategy_id=strategy_template_id,
                signal_id=signal_id,
                signal_run_id=signal_run_id,
            )
            # 4. Resolve execution prices
            instruments = sorted(set(targets["instrument"]) | set(account.positions.keys()))

            try:
                if self._execution_price_mode == "open":
                    exec_prices, market_status = fetch_market_snapshot(
                        trade_date, instruments, price_col="open",
                    )
                    mtm_prices, _ = fetch_market_snapshot(
                        trade_date, instruments, price_col="close",
                    )
                else:
                    exec_prices, market_status = fetch_market_snapshot(trade_date, instruments)
                    mtm_prices = exec_prices
            except Exception as exc:
                daily_summaries.append(self._empty_day(
                    trade_date, trade_date, account, f"no_market_data: {exc}"
                ))
                continue

            # 4. Before-state
            pos_before = positions_frame(account, mtm_prices)
            cash_before = float(account.cash)
            mv_before = float(pos_before["market_value"].sum()) if not pos_before.empty else 0.0
            tv_before = cash_before + mv_before

            # 5. Build order intents and execute with costs
            orders, _, _, _, _, _ = build_order_intents(
                account, day_signal, targets.set_index("instrument")["target_weight"].to_dict(),
                exec_prices, trade_date,
            )

            # 6. Execute, settle, MTM via shared kernel
            from qsys.backtest._execution import execute_trade_day

            day_result = execute_trade_day(
                account, orders, exec_prices, market_status, mtm_prices, trade_date,
                commission=commission, stamp_duty=stamp_duty,
                min_commission=min_commission, slippage=slippage,
                execution_price_mode=self._execution_price_mode,
            )
            self._last_prices = mtm_prices
            self._last_trade_date = trade_date
            daily_summaries.append(day_result)

            # 8. Debug artifacts
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

        # ── Write manifest ────────────────────────────────────────────────
        from qsys.research.manifest import with_standard_metadata, write_manifest

        manifest = with_standard_metadata({
            "artifact_type": "backtest_run",
            "backtest_id": backtest_id,
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
            "commission_bp": commission,
            "stamp_duty_bp": stamp_duty,
            "min_commission": min_commission,
            "slippage": slippage,
            "rebalance_freq": rebalance_freq,
            "data_cutoff_policy": "preopen_previous",
        })
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
            notes=f"cached-signal backtest over {len(trading_dates)} trading dates; "
                  f"signal_id={signal_id}; signal_run_id={signal_run_id}; "
                  f"top_n={top_n}; max_weight={max_weight}; "
                  f"rebalance_freq={rebalance_freq}; "
                  f"slippage={slippage}",
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
