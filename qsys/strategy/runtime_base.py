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
        """Calendar asof semantics — the most recent trading date up to
        and including *trade_date*.

        Delegates to :func:`qsys.data.calendar.resolve_data_date` with
        ``mode='asof'``.  Use ``resolve_preopen_data_date`` instead when
        constructing features for preopen prediction, to avoid leaking
        data that was not observable before market open on *trade_date*.
        """
        from qsys.data.calendar import resolve_data_date

        return resolve_data_date(trade_date, mode="asof")

    def resolve_preopen_data_date(self, trade_date: str) -> str:
        """Previous-close semantics — the last trading day strictly before
        *trade_date*.

        Preopen predictions must use data from the most recently completed
        trading day, not the as-of date, to avoid leaking future data when
        replaying historical preopen runs after the daily data sync has run.
        """
        from qsys.data.calendar import resolve_data_date

        return resolve_data_date(trade_date, mode="previous")

    def resolve_postclose_data_date(self, trade_date: str) -> str:
        """Postclose data date — identical to calendar-asof semantics.

        Returns the most recent trading day up to and including *trade_date*.
        Equivalent to ``resolve_data_date`` (asof), provided as a named
        counterpart to ``resolve_preopen_data_date`` for symmetry.
        """
        return self.resolve_data_date(trade_date)
