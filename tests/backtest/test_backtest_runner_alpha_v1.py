"""Tests for BacktestRunner alpha_v1 path — full daily loop with mocks.

Covers the complete ``run_range`` flow when the strategy implements the
``generate_predictions_for_date`` and ``build_plan_for_backtest`` backtest
hooks (as ``AlphaV1StrategyAdapter`` does).

All external dependencies (qlib, market data, order building, matching,
positions) are mocked so these tests need no real infrastructure.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from qsys.backtest.strategy_runner import (
    SUPPORTED_ARTIFACT_MODES,
    SUPPORTED_EXECUTION_PRICE_MODES,
    SUPPORTED_MODES,
    BacktestRunner,
    _resolve_trading_dates,
)
from qsys.backtest.result import BacktestRunResult
from qsys.trader.account import Account


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_market_status_df(instruments: list[str]) -> pd.DataFrame:
    """Build a minimal market-status DataFrame indexed by instrument."""
    return pd.DataFrame(
        {
            "is_suspended": [False] * len(instruments),
            "is_limit_up": [False] * len(instruments),
            "is_limit_down": [False] * len(instruments),
        },
        index=instruments,
    )


def _make_target_weights_csv(path: Path) -> None:
    """Write a minimal target_weights.csv into *path*."""
    pd.DataFrame(
        {
            "instrument": ["000001", "000002"],
            "target_weight": [0.06, 0.04],
        }
    ).to_csv(path / "target_weights.csv", index=False)


def _make_rebalance_audit_csv(path: Path) -> None:
    """Write a minimal rebalance_audit.csv into *path*."""
    pd.DataFrame(
        {
            "instrument": ["000001", "000002"],
            "score": [0.5, -0.3],
            "action": ["buy", "sell"],
        }
    ).to_csv(path / "rebalance_audit.csv", index=False)


def _make_order_intents_csv(path: Path) -> None:
    """Write a minimal order_intents.csv into *path*."""
    pd.DataFrame(
        {
            "instrument": ["000001"],
            "side": ["buy"],
            "requested_qty": [100],
        }
    ).to_csv(path / "order_intents.csv", index=False)


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def spec() -> Any:
    """Minimal spec-like object exposing ``strategy_id``."""
    from types import SimpleNamespace

    return SimpleNamespace(strategy_id="alpha_v1")


@pytest.fixture
def strategy() -> MagicMock:
    """Fully mocked strategy with both backtest hooks present."""
    s = MagicMock()
    s.generate_predictions_for_date = MagicMock()
    s.build_plan_for_backtest = MagicMock()
    s.resolve_preopen_data_date = MagicMock(return_value="2026-01-01")
    return s


@pytest.fixture
def predictions_df() -> pd.DataFrame:
    """Realistic non-empty predictions DataFrame."""
    return pd.DataFrame(
        {
            "instrument": ["000001", "000002", "000003"],
            "score": [0.5, -0.3, 0.1],
            "trade_date": ["2026-01-02", "2026-01-02", "2026-01-02"],
        }
    )


@pytest.fixture
def plan_dir() -> Path:
    """Temporary directory pre-populated with plan artifacts."""
    d = Path(tempfile.mkdtemp())
    _make_target_weights_csv(d)
    _make_rebalance_audit_csv(d)
    _make_order_intents_csv(d)
    return d


@pytest.fixture
def runner() -> BacktestRunner:
    """Default ``BacktestRunner`` instance."""
    return BacktestRunner()


# ── Validation & initialisation ──────────────────────────────────────────


class TestRunRangeValidation:
    """Boundary and configuration checks that never hit the daily loop."""

    def test_run_range_not_implemented(self, runner: BacktestRunner, spec: Any) -> None:
        """Strategy without backtest hooks returns status='not_implemented'."""
        strategy = MagicMock(spec=[])  # no methods / attributes at all
        result = runner.run_range(strategy, spec, "2026-01-01", "2026-01-05")

        assert result.status == "not_implemented"
        assert result.strategy_id == "alpha_v1"
        assert "lacks generate_predictions_for_date" in (result.notes or "")

    def test_run_range_start_after_end(
        self, runner: BacktestRunner, spec: Any, strategy: MagicMock
    ) -> None:
        """Raises ``ValueError`` when *start_date* is after *end_date*."""
        with pytest.raises(ValueError, match="start_date.*after.*end_date"):
            runner.run_range(strategy, spec, "2026-02-01", "2026-01-01")

    def test_unsupported_mode_raises(self) -> None:
        """``ValueError`` for a mode outside ``SUPPORTED_MODES``."""
        with pytest.raises(ValueError, match="unsupported mode"):
            BacktestRunner(mode="no_such_mode")

    def test_unsupported_artifact_mode_raises(self) -> None:
        """``ValueError`` for an artifact_mode outside ``SUPPORTED_ARTIFACT_MODES``."""
        with pytest.raises(ValueError, match="unsupported artifact_mode"):
            BacktestRunner(artifact_mode="no_such_artifact_mode")

    def test_supported_modes_accepted(self) -> None:
        """All modes in ``SUPPORTED_MODES`` are accepted at init."""
        for mode in SUPPORTED_MODES:
            r = BacktestRunner(mode=mode)
            assert r.mode == mode

    def test_supported_artifact_modes_accepted(self) -> None:
        """All modes in ``SUPPORTED_ARTIFACT_MODES`` are accepted at init."""
        for am in SUPPORTED_ARTIFACT_MODES:
            r = BacktestRunner(artifact_mode=am)
            assert r.artifact_mode == am

    def test_default_execution_price_mode(self) -> None:
        """Default ``execution_price_mode`` is ``"open"`` (DailyRunner-equiv)."""
        r = BacktestRunner()
        assert r.execution_price_mode == "open"

    def test_supported_execution_price_modes(self) -> None:
        """All values in ``SUPPORTED_EXECUTION_PRICE_MODES`` are accepted."""
        for pm in SUPPORTED_EXECUTION_PRICE_MODES:
            r = BacktestRunner(execution_price_mode=pm)
            assert r.execution_price_mode == pm

    def test_invalid_execution_price_mode_raises(self) -> None:
        """Invalid ``execution_price_mode`` raises ``ValueError``."""
        with pytest.raises(ValueError, match="unsupported execution_price_mode"):
            BacktestRunner(execution_price_mode="vwap")


# ── Daily-loop execution ─────────────────────────────────────────────────


class TestRunRangeExecution:
    """Tests that exercise the daily predict-plan-execute-record loop."""

    # ── Tests ─────────────────────────────────────────────────────────

    @patch("qsys.backtest.strategy_runner._resolve_trading_dates")
    def test_run_range_empty_predictions(
        self,
        mock_resolve: MagicMock,
        runner: BacktestRunner,
        spec: Any,
        strategy: MagicMock,
    ) -> None:
        """Strategy returning empty predictions yields ``"no_predictions"``."""
        mock_resolve.return_value = ["2026-01-02", "2026-01-03"]
        strategy.generate_predictions_for_date.return_value = pd.DataFrame()
        strategy.resolve_preopen_data_date.return_value = "2026-01-01"

        result = runner.run_range(
            strategy, spec, "2026-01-01", "2026-01-05"
        )

        assert result.status == "completed"
        assert len(result.daily_summary) == 2
        for day in result.daily_summary:
            assert day["status"] == "no_predictions"

    @patch("qsys.backtest.strategy_runner.positions_frame")
    @patch("qsys.backtest.strategy_runner.MatchEngine")
    @patch("qsys.backtest.strategy_runner.build_order_intents")
    @patch("qsys.backtest.strategy_runner.fetch_market_snapshot")
    @patch("qsys.backtest.strategy_runner._resolve_trading_dates")
    def test_run_range_success(
        self,
        mock_resolve: MagicMock,
        mock_fetch_snapshot: MagicMock,
        mock_build_intents: MagicMock,
        mock_match_engine_cls: MagicMock,
        mock_positions: MagicMock,
        runner: BacktestRunner,
        spec: Any,
        strategy: MagicMock,
        predictions_df: pd.DataFrame,
        plan_dir: Path,
    ) -> None:
        """Full successful run returns ``BacktestRunResult`` with completed status."""
        # ── Trading dates ────────────────────────────────────────────
        mock_resolve.return_value = ["2026-01-02"]

        # ── Strategy hooks ───────────────────────────────────────────
        strategy.generate_predictions_for_date.return_value = predictions_df
        strategy.resolve_preopen_data_date.return_value = "2026-01-01"
        strategy.build_plan_for_backtest.return_value = plan_dir

        # ── Market snapshot ──────────────────────────────────────────
        prices = {"000001": 10.0, "000002": 20.0, "000003": 30.0}
        mock_fetch_snapshot.return_value = (
            prices,
            _make_market_status_df(list(prices)),
        )

        # ── Order intents ────────────────────────────────────────────
        mock_build_intents.return_value = (
            [{"symbol": "000001", "side": "buy", "amount": 100}],
            pd.DataFrame(),
            pd.DataFrame(),
            1_000_000.0,  # cash_before
            0.0,  # market_value_before
            1_000_000.0,  # total_value_before
        )

        # ── Match engine ─────────────────────────────────────────────
        mock_matcher = MagicMock()
        mock_match_engine_cls.return_value = mock_matcher
        mock_matcher.match.return_value = [
            {"status": "filled", "filled_amount": 100, "deal_price": 10.0},
        ]

        # ── Positions after execution ────────────────────────────────
        # Before-state returns empty (fresh account), after-state returns 1000
        mock_positions.side_effect = [
            pd.DataFrame({"instrument": [], "market_value": []}),  # before
            pd.DataFrame({"instrument": ["000001"], "market_value": [1000.0]}),  # after
        ]

        # ── Execute ──────────────────────────────────────────────────
        result = runner.run_range(
            strategy, spec, "2026-01-01", "2026-01-05"
        )

        # ── Assertions ───────────────────────────────────────────────
        assert isinstance(result, BacktestRunResult)
        assert result.status == "completed"
        assert result.strategy_id == "alpha_v1"
        assert result.start_date == "2026-01-01"
        assert result.end_date == "2026-01-05"
        assert result.mode == "cached_daily_equivalent"
        assert result.initial_capital == 1_000_000.0
        assert len(result.daily_summary) == 1

        day = result.daily_summary[0]
        assert day["status"] == "success"
        assert day["trade_date"] == "2026-01-02"
        assert day["order_count"] == 1
        assert day["buy_count"] == 1
        assert day["sell_count"] == 0
        assert day["data_date"] == "2026-01-01"
        assert day["total_value_before"] == 1_000_000.0

        # Check the strategy hooks were called correctly
        strategy.resolve_preopen_data_date.assert_called_once_with("2026-01-02")
        strategy.generate_predictions_for_date.assert_called_once_with(
            "2026-01-02", data_date="2026-01-01"
        )
        strategy.build_plan_for_backtest.assert_called_once()

    @patch("qsys.backtest.strategy_runner.positions_frame")
    @patch("qsys.backtest.strategy_runner.MatchEngine")
    @patch("qsys.backtest.strategy_runner.build_order_intents")
    @patch("qsys.backtest.strategy_runner.fetch_market_snapshot")
    @patch("qsys.backtest.strategy_runner._resolve_trading_dates")
    def test_run_range_respects_output_dir(
        self,
        mock_resolve: MagicMock,
        mock_fetch_snapshot: MagicMock,
        mock_build_intents: MagicMock,
        mock_match_engine_cls: MagicMock,
        mock_positions: MagicMock,
        runner: BacktestRunner,
        spec: Any,
        strategy: MagicMock,
        predictions_df: pd.DataFrame,
        plan_dir: Path,
    ) -> None:
        """When ``output_dir`` is provided, summary files are written."""
        mock_resolve.return_value = ["2026-01-02"]
        strategy.generate_predictions_for_date.return_value = predictions_df
        strategy.resolve_preopen_data_date.return_value = "2026-01-01"
        strategy.build_plan_for_backtest.return_value = plan_dir

        prices = {"000001": 10.0, "000002": 20.0, "000003": 30.0}
        mock_fetch_snapshot.return_value = (
            prices,
            _make_market_status_df(list(prices)),
        )
        mock_build_intents.return_value = (
            [{"symbol": "000001", "side": "buy", "amount": 100}],
            pd.DataFrame(),
            pd.DataFrame(),
            1_000_000.0,
            0.0,
            1_000_000.0,
        )
        mock_matcher = MagicMock()
        mock_match_engine_cls.return_value = mock_matcher
        mock_matcher.match.return_value = [
            {"status": "filled", "filled_amount": 100, "deal_price": 10.0},
        ]
        mock_positions.return_value = pd.DataFrame(
            {"instrument": ["000001"], "market_value": [1000.0]}
        )

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "bt_output"

            result = runner.run_range(
                strategy,
                spec,
                "2026-01-01",
                "2026-01-05",
                output_dir=output_dir,
            )

            # Files should exist
            assert (output_dir / "backtest_result.json").exists()
            assert (output_dir / "daily_summary.csv").exists()

            # Verify JSON content
            with open(output_dir / "backtest_result.json") as f:
                summary = json.load(f)
            assert summary["strategy_id"] == "alpha_v1"
            assert summary["status"] == "completed"
            assert summary["daily_count"] == 1

            # Verify CSV content
            csv_df = pd.read_csv(output_dir / "daily_summary.csv")
            assert len(csv_df) == 1
            assert csv_df.iloc[0]["status"] == "success"

    @patch("qsys.backtest.strategy_runner.positions_frame")
    @patch("qsys.backtest.strategy_runner.MatchEngine")
    @patch("qsys.backtest.strategy_runner.build_order_intents")
    @patch("qsys.backtest.strategy_runner.fetch_market_snapshot")
    @patch("qsys.backtest.strategy_runner._resolve_trading_dates")
    def test_run_range_debug_artifacts(
        self,
        mock_resolve: MagicMock,
        mock_fetch_snapshot: MagicMock,
        mock_build_intents: MagicMock,
        mock_match_engine_cls: MagicMock,
        mock_positions: MagicMock,
        spec: Any,
        strategy: MagicMock,
        predictions_df: pd.DataFrame,
        plan_dir: Path,
    ) -> None:
        """When ``artifact_mode="debug"``, per-day debug files are written."""
        debug_runner = BacktestRunner(artifact_mode="debug")

        mock_resolve.return_value = ["2026-01-02"]
        strategy.generate_predictions_for_date.return_value = predictions_df
        strategy.resolve_preopen_data_date.return_value = "2026-01-01"
        strategy.build_plan_for_backtest.return_value = plan_dir

        prices = {"000001": 10.0, "000002": 20.0, "000003": 30.0}
        mock_fetch_snapshot.return_value = (
            prices,
            _make_market_status_df(list(prices)),
        )
        mock_build_intents.return_value = (
            [{"symbol": "000001", "side": "buy", "amount": 100}],
            pd.DataFrame(),
            pd.DataFrame(),
            1_000_000.0,
            0.0,
            1_000_000.0,
        )
        mock_matcher = MagicMock()
        mock_match_engine_cls.return_value = mock_matcher
        mock_matcher.match.return_value = [
            {"status": "filled", "filled_amount": 100, "deal_price": 10.0},
        ]
        mock_positions.return_value = pd.DataFrame(
            {"instrument": ["000001"], "market_value": [1000.0]}
        )

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "bt_debug"

            result = debug_runner.run_range(
                strategy,
                spec,
                "2026-01-01",
                "2026-01-05",
                output_dir=output_dir,
            )

            assert result.status == "completed"

            # Summary files should still be written
            assert (output_dir / "backtest_result.json").exists()
            assert (output_dir / "daily_summary.csv").exists()

            # Per-day debug artifacts
            daily_dir = output_dir / "daily" / "2026-01-02"
            assert daily_dir.exists()
            assert (daily_dir / "execution_summary.json").exists()
            assert (daily_dir / "positions_after.csv").exists()
            assert (daily_dir / "account_after.json").exists()
            assert (daily_dir / "predictions.csv").exists()

            # Spot-check debug JSON content
            with open(daily_dir / "execution_summary.json") as f:
                exec_summary = json.load(f)
            assert exec_summary["status"] == "success"
            assert exec_summary["trade_date"] == "2026-01-02"

            # account_after.json schema must match run_range contract
            with open(daily_dir / "account_after.json") as f:
                acc = json.load(f)
            for key in ("trade_date", "cash", "available_cash",
                        "market_value", "total_value",
                        "last_run_id", "initial_capital"):
                assert key in acc, f"account_after.json missing key: {key}"
            assert acc["cash"] == acc["available_cash"]

    @patch("qsys.backtest.strategy_runner.positions_frame")
    @patch("qsys.backtest.strategy_runner.MatchEngine")
    @patch("qsys.backtest.strategy_runner.build_order_intents")
    @patch("qsys.backtest.strategy_runner.fetch_market_snapshot")
    @patch("qsys.backtest.strategy_runner._resolve_trading_dates")
    def test_run_range_initial_account(
        self,
        mock_resolve: MagicMock,
        mock_fetch_snapshot: MagicMock,
        mock_build_intents: MagicMock,
        mock_match_engine_cls: MagicMock,
        mock_positions: MagicMock,
        runner: BacktestRunner,
        spec: Any,
        strategy: MagicMock,
        predictions_df: pd.DataFrame,
        plan_dir: Path,
    ) -> None:
        """Pre-configured ``Account`` is passed through to the daily loop."""
        mock_resolve.return_value = ["2026-01-02"]
        strategy.generate_predictions_for_date.return_value = predictions_df
        strategy.resolve_preopen_data_date.return_value = "2026-01-01"
        strategy.build_plan_for_backtest.return_value = plan_dir

        prices = {"000001": 10.0, "000002": 20.0, "000003": 30.0}
        mock_fetch_snapshot.return_value = (
            prices,
            _make_market_status_df(list(prices)),
        )
        mock_build_intents.return_value = (
            [],
            pd.DataFrame(),
            pd.DataFrame(),
            500_000.0,  # cash_before — reflects custom account
            0.0,
            500_000.0,
        )
        mock_matcher = MagicMock()
        mock_match_engine_cls.return_value = mock_matcher
        mock_matcher.match.return_value = []
        mock_positions.return_value = pd.DataFrame(
            {"instrument": [], "market_value": []}
        )

        # Create a pre-configured account with non-default cash
        custom_account = Account(init_cash=500_000.0)
        custom_account.cash = 500_000.0

        result = runner.run_range(
            strategy,
            spec,
            "2026-01-01",
            "2026-01-05",
            initial_account=custom_account,
        )

        assert result.status == "completed"

        # The account passed to build_plan_for_backtest should be our
        # custom account (identity check).
        args, _ = strategy.build_plan_for_backtest.call_args
        passed_account = args[1]
        assert passed_account is custom_account, (
            f"Expected custom_account but got {passed_account}"
        )

    @patch("qsys.backtest.strategy_runner.positions_frame")
    @patch("qsys.backtest.strategy_runner.MatchEngine")
    @patch("qsys.backtest.strategy_runner.build_order_intents")
    @patch("qsys.backtest.strategy_runner.fetch_market_snapshot")
    @patch("qsys.backtest.strategy_runner._resolve_trading_dates")
    def test_open_mode_fetches_open_prices(
        self,
        mock_resolve: MagicMock,
        mock_fetch_snapshot: MagicMock,
        mock_build_intents: MagicMock,
        mock_match_engine_cls: MagicMock,
        mock_positions: MagicMock,
        spec: Any,
        strategy: MagicMock,
        predictions_df: pd.DataFrame,
        plan_dir: Path,
    ) -> None:
        """In open mode, the first ``fetch_market_snapshot`` call uses ``price_col="open"``."""
        open_runner = BacktestRunner(execution_price_mode="open")
        mock_resolve.return_value = ["2026-01-02"]
        strategy.generate_predictions_for_date.return_value = predictions_df
        strategy.resolve_preopen_data_date.return_value = "2026-01-01"
        strategy.build_plan_for_backtest.return_value = plan_dir

        prices = {"000001": 10.0, "000002": 20.0, "000003": 30.0}
        mock_fetch_snapshot.return_value = (
            prices,
            _make_market_status_df(list(prices)),
        )
        mock_build_intents.return_value = (
            [{"symbol": "000001", "side": "buy", "amount": 100}],
            pd.DataFrame(),
            pd.DataFrame(),
            1_000_000.0,
            0.0,
            1_000_000.0,
        )
        mock_matcher = MagicMock()
        mock_match_engine_cls.return_value = mock_matcher
        mock_matcher.match.return_value = []
        mock_positions.return_value = pd.DataFrame(
            {"instrument": [], "market_value": []}
        )

        with tempfile.TemporaryDirectory() as tmp:
            open_runner.run_range(
                strategy, spec, "2026-01-01", "2026-01-05",
                output_dir=Path(tmp) / "bt_open",
            )

        # First call should use price_col="open"
        first_call = mock_fetch_snapshot.call_args_list[0]
        assert first_call.kwargs.get("price_col") == "open", (
            f"Expected price_col='open' but got {first_call}"
        )

    @patch("qsys.backtest.strategy_runner.positions_frame")
    @patch("qsys.backtest.strategy_runner.MatchEngine")
    @patch("qsys.backtest.strategy_runner.build_order_intents")
    @patch("qsys.backtest.strategy_runner.fetch_market_snapshot")
    @patch("qsys.backtest.strategy_runner._resolve_trading_dates")
    def test_close_mode_fetches_close_prices(
        self,
        mock_resolve: MagicMock,
        mock_fetch_snapshot: MagicMock,
        mock_build_intents: MagicMock,
        mock_match_engine_cls: MagicMock,
        mock_positions: MagicMock,
        spec: Any,
        strategy: MagicMock,
        predictions_df: pd.DataFrame,
        plan_dir: Path,
    ) -> None:
        """In close mode, ``fetch_market_snapshot`` is called without ``price_col``."""
        close_runner = BacktestRunner(execution_price_mode="close")
        mock_resolve.return_value = ["2026-01-02"]
        strategy.generate_predictions_for_date.return_value = predictions_df
        strategy.resolve_preopen_data_date.return_value = "2026-01-01"
        strategy.build_plan_for_backtest.return_value = plan_dir

        prices = {"000001": 10.0, "000002": 20.0, "000003": 30.0}
        mock_fetch_snapshot.return_value = (
            prices,
            _make_market_status_df(list(prices)),
        )
        mock_build_intents.return_value = (
            [{"symbol": "000001", "side": "buy", "amount": 100}],
            pd.DataFrame(),
            pd.DataFrame(),
            1_000_000.0,
            0.0,
            1_000_000.0,
        )
        mock_matcher = MagicMock()
        mock_match_engine_cls.return_value = mock_matcher
        mock_matcher.match.return_value = []
        mock_positions.return_value = pd.DataFrame(
            {"instrument": [], "market_value": []}
        )

        with tempfile.TemporaryDirectory() as tmp:
            close_runner.run_range(
                strategy, spec, "2026-01-01", "2026-01-05",
                output_dir=Path(tmp) / "bt_close",
            )

        # In close mode, fetch_market_snapshot is called once without price_col
        mock_fetch_snapshot.assert_called_once()
        call_kwargs = mock_fetch_snapshot.call_args[1]
        assert "price_col" not in call_kwargs, (
            f"Expected no price_col kwarg but got {call_kwargs}"
        )


# ── _resolve_trading_dates ───────────────────────────────────────────────


class TestResolveTradingDates:
    """Tests for the module-level ``_resolve_trading_dates`` helper."""

    @patch("qsys.data.adapter.QlibAdapter")
    @patch("qlib.data.D")
    def test_resolve_trading_dates_qlib_path(
        self,
        mock_qlib_D: MagicMock,
        mock_adapter_cls: MagicMock,
    ) -> None:
        """qlib calendar path returns formatted date strings."""
        mock_qlib_D.calendar.return_value = [
            pd.Timestamp("2026-01-02"),
            pd.Timestamp("2026-01-03"),
            pd.Timestamp("2026-01-05"),
        ]

        dates = _resolve_trading_dates("2026-01-01", "2026-01-05")
        assert dates == ["2026-01-02", "2026-01-03", "2026-01-05"]
        mock_adapter_cls.return_value.init_qlib.assert_called_once()

    @patch("qsys.data.adapter.QlibAdapter")
    @patch("qlib.data.D")
    def test_resolve_trading_dates_filters_in_range(
        self,
        mock_qlib_D: MagicMock,
        mock_adapter_cls: MagicMock,
    ) -> None:
        """Dates outside [start_date, end_date] are filtered out by ``run_range``.

        (The function itself returns everything qlib gives — filtering
        happens in ``run_range``.)
        """
        mock_qlib_D.calendar.return_value = [
            pd.Timestamp("2025-12-31"),  # outside range
            pd.Timestamp("2026-01-02"),
        ]

        dates = _resolve_trading_dates("2026-01-01", "2026-01-05")
        # _resolve_trading_dates itself does not filter — it returns all
        # qlib calendar entries in the window.
        assert "2025-12-31" in dates
        assert "2026-01-02" in dates

    @patch("qsys.data.adapter.QlibAdapter")
    @patch("qlib.data.D")
    def test_resolve_trading_dates_qlib_exception_falls_back(
        self,
        mock_qlib_D: MagicMock,
        mock_adapter_cls: MagicMock,
    ) -> None:
        """When qlib raises, the function falls back to ``pd.bdate_range``."""
        mock_qlib_D.calendar.side_effect = RuntimeError("qlib unavailable")
        mock_adapter_cls.side_effect = RuntimeError("adapter fail")

        dates = _resolve_trading_dates("2026-01-05", "2026-01-10")
        assert len(dates) >= 4  # at least Mon-Fri business days
        assert dates[0] >= "2026-01-05"
        assert dates[-1] <= "2026-01-10"

    def test_resolve_trading_dates_fallback_no_qlib(self) -> None:
        """Without any qlib mocking, the import fails and falls back."""
        dates = _resolve_trading_dates("2026-01-05", "2026-01-10")
        assert len(dates) >= 4
        assert dates[0] >= "2026-01-05"
        assert dates[-1] <= "2026-01-10"


# ── Edge cases ────────────────────────────────────────────────────────────


class TestRunRangeEdgeCases:
    """Additional edge cases for the backtest runner."""

    @patch("qsys.backtest.strategy_runner._resolve_trading_dates")
    def test_run_range_no_trading_days(
        self,
        mock_resolve: MagicMock,
        runner: BacktestRunner,
        spec: Any,
        strategy: MagicMock,
    ) -> None:
        """When there are no trading days, the result has an empty daily_summary."""
        mock_resolve.return_value = []

        result = runner.run_range(strategy, spec, "2099-01-01", "2099-01-01")

        assert result.status == "completed"
        assert len(result.daily_summary) == 0

    @patch("qsys.backtest.strategy_runner._resolve_trading_dates")
    def test_run_range_none_predictions(
        self,
        mock_resolve: MagicMock,
        runner: BacktestRunner,
        spec: Any,
        strategy: MagicMock,
    ) -> None:
        """Strategy returning ``None`` predictions is treated as no_predictions."""
        mock_resolve.return_value = ["2026-01-02"]
        strategy.resolve_preopen_data_date.return_value = "2026-01-01"
        strategy.generate_predictions_for_date.return_value = None

        result = runner.run_range(strategy, spec, "2026-01-01", "2026-01-05")

        assert result.status == "completed"
        assert result.daily_summary[0]["status"] == "no_predictions"

    @patch("qsys.backtest.strategy_runner._resolve_trading_dates")
    def test_run_range_has_build_plan_no_generate(
        self,
        mock_resolve: MagicMock,
        runner: BacktestRunner,
        spec: Any,
    ) -> None:
        """Strategy with only one of the two hooks is reported not_implemented."""
        strategy = MagicMock()
        strategy.build_plan_for_backtest = MagicMock()
        # Deliberately missing generate_predictions_for_date
        del strategy.generate_predictions_for_date

        result = runner.run_range(strategy, spec, "2026-01-01", "2026-01-05")
        assert result.status == "not_implemented"

    @patch("qsys.backtest.strategy_runner._resolve_trading_dates")
    def test_run_range_no_market_data_fallback(
        self,
        mock_resolve: MagicMock,
        runner: BacktestRunner,
        spec: Any,
        strategy: MagicMock,
        predictions_df: pd.DataFrame,
        plan_dir: Path,
    ) -> None:
        """When ``fetch_market_snapshot`` raises, the day gets a descriptive status."""
        mock_resolve.return_value = ["2026-01-02"]
        strategy.generate_predictions_for_date.return_value = predictions_df
        strategy.resolve_preopen_data_date.return_value = "2026-01-01"
        strategy.build_plan_for_backtest.return_value = plan_dir

        with patch(
            "qsys.backtest.strategy_runner.fetch_market_snapshot",
            side_effect=RuntimeError("market data not available"),
        ):
            result = runner.run_range(
                strategy, spec, "2026-01-01", "2026-01-05"
            )

        assert result.status == "completed"
        assert "no_market_data" in result.daily_summary[0]["status"]
