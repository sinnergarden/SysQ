"""PredictionsCacheProxy — wraps a StrategyCandidate adapter, caching predictions.

Usage
-----
    adapter = AlphaV1ResearchAdapter.from_config(config)
    cached = PredictionsCacheProxy(adapter, cache_dir="/tmp/pred_cache")

    # First call: generates + caches predictions
    preds = cached.generate_predictions_for_date("2024-01-03")

    # Second call: loads from cache (fast)
    preds = cached.generate_predictions_for_date("2024-01-03")

    # Works transparently with BacktestRunner:
    runner.run_range(cached, spec, start_date, end_date)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


class PredictionsCacheProxy:
    """Proxy that caches ``generate_predictions_for_date`` results.

    All other attributes and methods delegate transparently to the inner
    adapter via ``__getattr__``, making this a drop-in wrapper for any
    ``StrategyCandidate``.
    """

    def __init__(self, inner: Any, cache_dir: str | Path) -> None:
        self._inner = inner
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._mem_cache: dict[str, pd.DataFrame] = {}

    # ── Public API ─────────────────────────────────────────────────────

    def generate_predictions_for_date(
        self, trade_date: str, *, data_date: str | None = None,
    ) -> pd.DataFrame:
        """Return cached predictions for *trade_date*, computing if absent.

        Delegates to the inner adapter on cache miss, then persists to CSV.
        Subsequent calls (even from different proxy instances sharing the
        same directory) load from disk and populate the in-memory cache.
        """
        # 1. In-memory cache (fastest)
        if trade_date in self._mem_cache:
            return self._mem_cache[trade_date]

        # 2. Disk cache
        cache_path = self._cache_dir / f"{trade_date}.csv"
        if cache_path.exists():
            df = pd.read_csv(cache_path)
            self._mem_cache[trade_date] = df
            return df

        # 3. Cache miss — compute
        df = self._inner.generate_predictions_for_date(
            trade_date, data_date=data_date,
        )
        if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
            df.to_csv(cache_path, index=False)
        else:
            # Store sentinel for empty predictions (don't cache empty)
            self._mem_cache[trade_date] = df if df is not None else pd.DataFrame()
            return self._mem_cache[trade_date]

        self._mem_cache[trade_date] = df
        return df

    def warm_cache(self, trading_dates: list[str]) -> None:
        """Pre-load all available cached predictions into memory."""
        for d in trading_dates:
            cache_path = self._cache_dir / f"{d}.csv"
            if cache_path.exists():
                df = pd.read_csv(cache_path)
                self._mem_cache[d] = df

    # ── Transparent delegation ─────────────────────────────────────────

    def __getattr__(self, name: str) -> Any:
        """Delegate all other attribute access to the inner adapter."""
        return getattr(self._inner, name)
