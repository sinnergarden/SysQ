"""Strategy base interfaces — Protocol and ABC.

``IStrategy`` is the legacy ABC used by the backtest engine.
``StrategyCandidate`` is a runtime Protocol describing what the DailyRunner
needs to know about a strategy for daily ops (preopen/postclose/train).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Protocol, runtime_checkable

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd
    from pathlib import Path

    from qsys.ops.run_context import DailyRunContext


class IStrategy(ABC):
    """Legacy strategy interface — backtest engine."""

    @abstractmethod
    def generate_orders(self, signals, current_portfolio):
        # Convert signals (scores) to target portfolio weights/quantities
        pass


@runtime_checkable
class StrategyCandidate(Protocol):
    """Runtime protocol for daily-ops strategy resolution.

    This is a **runtime adapter interface** — every strategy adapter must
    implement all members (``@runtime_checkable`` checks the full protocol
    body). The DailyRunner calls these methods during preopen / postclose /
    notify-only stages; no strategy-specific imports, strings, or path
    conventions should leak into the runner.

    Identity + config properties and lifecycle hook methods that the
    DailyRunner calls during preopen / postclose / notify-only stages.
    Every strategy adapter must implement all members (``@runtime_checkable``
    checks the full protocol body).
    """

    # ── Identity ──────────────────────────────────────────────────────

    @property
    def strategy_id(self) -> str: ...

    @property
    def account_id(self) -> str: ...

    @property
    def display_name(self) -> str:
        """Human-readable name for notifications (e.g. 'Alpha V1')."""

    # ── Configuration ──────────────────────────────────────────────────

    @property
    def universe(self) -> str: ...

    @property
    def feature_set(self) -> str: ...

    @property
    def model_version(self) -> str: ...

    @property
    def signal_version(self) -> str: ...

    @property
    def rebalance_policy(self) -> dict[str, Any]: ...

    # ── Data ───────────────────────────────────────────────────────────

    def resolve_data_date(self, trade_date: str) -> str:
        """Return nearest trading day with available data for *trade_date*.

        Default semantic is **asof**: a trading day resolves to itself;
        a non-trading day (weekend/holiday) rolls back to the previous
        trading day.

        Strategies should only override this when they have explicitly
        documented data-availability constraints that differ from the
        framework default.  Use :class:`~qsys.strategy.runtime_base.BaseStrategyAdapter`
        to inherit the default implementation.
        """

    def resolve_preopen_data_date(self, trade_date: str) -> str:
        """Return the last trading day strictly before *trade_date*.

        Preopen predictions must use data from the most recently completed
        trading day, not the as-of date, to avoid leaking forward data when
        replaying historical preopen runs after the daily data sync.
        """

    def resolve_postclose_data_date(self, trade_date: str) -> str:
        """Return the as-of trading day for postclose processing.

        Identical to ``resolve_data_date`` (asof).  Exists as a named
        counterpart to ``resolve_preopen_data_date`` for symmetry.
        """

    def get_stock_name(self, ts_code: str) -> str:
        """Return human-readable name for a stock code."""

    def load_model(self) -> None:
        """Load strategy-specific model(s) — prints summary, stores internally."""

    def fetch_data(self, data_date: str) -> Any:
        """Fetch feature data for *data_date* — prints row count, returns opaque data."""

    # ── Predict + Plan ─────────────────────────────────────────────────

    def generate_predictions(self, data: Any) -> Any:
        """Run inference on *data* using internally stored model — returns predictions DataFrame."""

    def print_predictions_summary(self, predictions: Any) -> None:
        """Print top picks summary to console (e.g. top 5 with scores)."""

    def should_rebalance(self, trade_date: str) -> bool:
        """Check whether rebalancing should occur (e.g. weekly frequency)."""

    def build_plan(self, predictions: Any, target_dir: Any) -> bool:
        """Build trading plan from predictions into *target_dir*.
        Returns True if a plan was written, False if skipped.
        """

    def load_plan_instruments(self, plan_dir: Any) -> list[str]:
        """Return instrument codes from the plan at *plan_dir*."""

    def save_predictions(self, predictions: Any, run_root: Any, trade_date: str) -> None:
        """Persist predictions to strategy-specific shared location."""

    def fetch_open_prices(self, trade_date: str, instruments: list[str]) -> dict[str, float]:
        """Fetch open prices for *instruments* on *trade_date*.
        Returns dict[instrument → open_price].
        """

    # ── Execute + MTM ──────────────────────────────────────────────────

    def execute_plan(self, context: Any) -> Any:
        """Execute the trading plan — returns ShadowRebalanceArtifacts."""

    def commit_execution(self, context: Any, staging_dir: Any) -> None:
        """Commit execution artifacts from staging to production paths."""

    def mark_to_market(self, context: Any) -> dict | None:
        """Compute MTM snapshot — returns dict or None."""

    def load_artifacts_for_notification(self, context: Any) -> Any | None:
        """Load execution artifacts for postclose notification."""

    # ── Notifications ──────────────────────────────────────────────────

    def build_preopen_message(
        self, context: Any, rebalance_skipped: bool, predictions: Any
    ) -> str:
        """Format preopen notification text."""

    def build_postclose_message(
        self,
        context: Any,
        mtm: dict | None = None,
        artifacts: Any = None,
        stale_check: dict | None = None,
        execution_committed: bool = False,
        execution_skipped: bool = False,
        idempotent_skip: bool = False,
    ) -> str:
        """Format postclose notification text."""

    # ── Training ──────────────────────────────────────────────────────────

    def train(self, context: Any) -> Any:
        """Run strategy-specific training.

        The DailyRunner delegates to this method during ``train`` mode.
        Different model families (LightGBM, Transformer, etc.) are free to
        implement completely different training logic behind this single
        hook — the runner does not know the model type.

        Returns a ``TrainingResult``-like object (see ``qsys.model.training``).
        """

    def send_notification(self, text: str) -> None:
        """Send *text* via Telegram (or configured channel)."""
