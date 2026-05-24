"""BaseStrategyAdapter — reusable defaults for StrategyCandidate implementations.

``StrategyCandidate`` is a :py:class:`Protocol` — it does not support default
implementations.  ``BaseStrategyAdapter`` provides a concrete base class that
adapters *can* inherit from to get sensible defaults for non-strategy-specific
methods such as ``resolve_data_date``.

Usage::

    class MyAdapter(BaseStrategyAdapter):
        ...
"""

from __future__ import annotations


class BaseStrategyAdapter:
    """Base class with default implementations for common adapter methods.

    Strategies should only override a method when they need different semantics
    (e.g. custom data availability constraints).
    """

    def resolve_data_date(self, trade_date: str) -> str:
        """Default: asof semantics — trading date resolves to itself.

        Delegates to :func:`qsys.data.calendar.resolve_data_date` with
        ``mode='asof'``.  Override only when the strategy has documented
        data-availability constraints that differ from the framework default.
        """
        from qsys.data.calendar import resolve_data_date

        return resolve_data_date(trade_date, mode="asof")
