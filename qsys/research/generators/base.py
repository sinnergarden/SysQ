"""Base protocol for per-window signal generation.

This is the **single canonical definition** of ``RollingSignalGenerator``.
All generators in this package implement it.  Do not redefine this
Protocol elsewhere — import it from here.
"""

from __future__ import annotations

from typing import Protocol

import pandas as pd


class RollingSignalGenerator(Protocol):
    """Protocol for per-window signal generation.

    Implementations must return a SignalStore-compatible DataFrame with
    columns: trade_date, data_date, instrument, signal_id, signal_run_id, score.

    See ``docs/GENERATOR_DEV_GUIDE.md`` for the full generator development guide.
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
