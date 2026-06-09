"""Fixture signal generators — deterministic test/CI generators.

Extracted from ``rolling_runner.py``.
"""

from __future__ import annotations

import pandas as pd


class FixtureSignalGenerator:
    """Deterministic fixture generator for testing / CI.

    Returns random-shaped signals that are valid for SignalStore.
    """

    def __init__(self, n_instruments: int = 100, seed: int = 42) -> None:
        self._n_inst = n_instruments
        self._seed = seed

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
        import numpy as np
        from datetime import datetime, timedelta

        # Resolve full trading calendar and predict date range
        _all_dates: list[str] = []
        _predict_dates: list[str] = []
        try:
            from qsys.data.calendar import get_trading_calendar
            _all_dates = sorted(get_trading_calendar("2000-01-01", predict_end) or [])
        except Exception:
            pass

        if not _all_dates:
            # Fallback: business days only, never weekend
            _bdate_range = pd.bdate_range(start=predict_start, end=predict_end)
            # Extend backward by 10 business days so the earliest trade_date
            # still gets a valid previous business day as data_date
            _extended_start = pd.bdate_range(
                end=predict_start, periods=11, inclusive="left"
            )
            _all_dates = sorted(
                set(d.strftime("%Y-%m-%d") for d in _extended_start)
                | set(d.strftime("%Y-%m-%d") for d in _bdate_range)
            )

        _predict_dates = [d for d in _all_dates if predict_start <= d <= predict_end]

        # Build lookup: trade_date -> previous business/trading day
        _prev_map: dict[str, str] = {}
        for i, d in enumerate(_all_dates):
            _prev_map[d] = _all_dates[i - 1] if i > 0 else (
                (pd.Timestamp(d) - pd.tseries.offsets.BDay(1)).strftime("%Y-%m-%d")
            )

        rng = np.random.default_rng(self._seed)
        rows = []
        for td in _predict_dates:
            prev = _prev_map.get(td,
                (pd.Timestamp(td) - pd.tseries.offsets.BDay(1)).strftime("%Y-%m-%d"))
            for ii in range(self._n_inst):
                rows.append({
                    "trade_date": td,
                    "data_date": prev,
                    "instrument": f"000{ii:04d}.SZ",
                    "signal_id": signal_id,
                    "signal_run_id": signal_run_id,
                    "score": float(rng.normal(0, 1)),
                })
        return pd.DataFrame(rows)


class MultiHeadFixtureGenerator:
    """Fixture generator producing rows with multiple signal_ids.

    Simulates a multi-head model (e.g. DNN task-tower) where one
    ``generate()`` call returns a DataFrame containing predictions
    for multiple heads, differentiated by the ``signal_id`` column.
    """

    def __init__(
        self,
        head_signal_ids: tuple[str, ...] = ("head_a", "head_b"),
        n_instruments: int = 50,
        seed: int = 42,
    ) -> None:
        self._head_ids = head_signal_ids
        self._n_inst = n_instruments
        self._seed = seed

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
        import numpy as np
        from datetime import datetime, timedelta

        _all_dates: list[str] = []
        try:
            from qsys.data.calendar import get_trading_calendar
            _all_dates = sorted(get_trading_calendar("2000-01-01", predict_end) or [])
        except Exception:
            pass
        if not _all_dates:
            _bdate_range = pd.bdate_range(start=predict_start, end=predict_end)
            _extended_start = pd.bdate_range(end=predict_start, periods=11, inclusive="left")
            _all_dates = sorted(
                set(d.strftime("%Y-%m-%d") for d in _extended_start)
                | set(d.strftime("%Y-%m-%d") for d in _bdate_range)
            )

        _predict_dates = [d for d in _all_dates if predict_start <= d <= predict_end]
        _prev_map: dict[str, str] = {}
        for i, d in enumerate(_all_dates):
            _prev_map[d] = _all_dates[i - 1] if i > 0 else (
                (pd.Timestamp(d) - pd.tseries.offsets.BDay(1)).strftime("%Y-%m-%d")
            )

        rows = []
        for head_id in self._head_ids:
            h_rng = np.random.default_rng(self._seed + hash(head_id) % 10000)
            for td in _predict_dates:
                prev = _prev_map.get(td,
                    (pd.Timestamp(td) - pd.tseries.offsets.BDay(1)).strftime("%Y-%m-%d"))
                for ii in range(self._n_inst):
                    rows.append({
                        "trade_date": td,
                        "data_date": prev,
                        "instrument": f"000{ii:04d}.SZ",
                        "signal_id": head_id,
                        "signal_run_id": signal_run_id,
                        "score": float(h_rng.normal(0, 1)),
                    })
        return pd.DataFrame(rows)
