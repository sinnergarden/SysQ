"""Base protocol for per-window signal generation.

Matches the existing ``RollingSignalGenerator`` protocol used in
``rolling_runner.py`` so that generators in this package are
interchangeable with ``FixtureSignalGenerator``.
"""

from __future__ import annotations

from typing import Protocol

import pandas as pd


class RollingSignalGenerator(Protocol):
    """Protocol for per-window signal generation.

    Implementations must return a SignalStore-compatible DataFrame with
    columns: trade_date, data_date, instrument, signal_id, signal_run_id, score.
    """

    def generate(
        self,
        *,
        train_start: str,
        train_end: str,
        predict_start: str,
        predict_end: str,
        signal_id: str,
        signal_run_id: str,
    ) -> pd.DataFrame:
        ...
