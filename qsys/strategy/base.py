"""Strategy base interfaces — Protocol and ABC.

``IStrategy`` is the legacy ABC used by the backtest engine.
``StrategyCandidate`` is a runtime Protocol describing what the DailyRunner
needs to know about a strategy for daily ops (preopen/postclose/train).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Protocol, runtime_checkable


class IStrategy(ABC):
    """Legacy strategy interface — backtest engine."""

    @abstractmethod
    def generate_orders(self, signals, current_portfolio):
        # Convert signals (scores) to target portfolio weights/quantities
        pass


@runtime_checkable
class StrategyCandidate(Protocol):
    """Runtime protocol for daily-ops strategy resolution.

    Required properties every strategy candidate must expose to the
    DailyRunner.  Optional lifecycle hooks (``on_preopen``,
    ``on_postclose``, ``on_train``) are recognised via
    ``hasattr(obj, hook_name)`` — they are documented below but
    intentionally omitted from the protocol body so that
    ``isinstance(obj, StrategyCandidate)`` does not require them.
    """

    # ── Identity ──────────────────────────────────────────────────────

    @property
    def strategy_id(self) -> str: ...

    @property
    def account_id(self) -> str: ...

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

    # ── Optional lifecycle hooks (not in protocol body) ──────────────
    #
    #   def on_preopen(self, context: Any) -> None:
    #       """Called before pre-open inference."""
    #   def on_postclose(self, context: Any) -> None:
    #       """Called after post-close reconciliation."""
    #   def on_train(self, context: Any) -> None:
    #       """Called before training."""
