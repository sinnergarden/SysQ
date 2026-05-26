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

from pathlib import Path
from typing import Any

import pandas as pd


class BaseStrategyAdapter:
    """Base class with default implementations for common adapter methods.

    Strategies should only override a method when they need different semantics
    (e.g. custom data availability constraints).

    Subclasses must set ``_project_root`` and ``_predictions_dir`` (or define
    them as properties) before calling any of the shared utility methods.
    """

    def __init__(self) -> None:
        self._stock_names: dict[str, str] = {}
        self._stock_names_loaded = False

    # ── Data-date resolution ───────────────────────────────────────────

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

    # ── Stock-name lookup ──────────────────────────────────────────────

    def _load_stock_names(self) -> None:
        path = self._project_root / "data" / "stock_names.csv"
        if path.exists():
            df = pd.read_csv(path)
            for _, row in df.iterrows():
                self._stock_names[str(row["ts_code"])] = str(row["name"])
        self._stock_names_loaded = True

    def get_stock_name(self, ts_code: str) -> str:
        """Return human-readable name for a stock code.

        Falls back to *ts_code* itself when the name is unknown or the
        names file is absent.
        """
        if not self._stock_names_loaded:
            self._load_stock_names()
        return self._stock_names.get(ts_code, ts_code)

    # ── Predict + Plan utilities ──────────────────────────────────────

    def print_predictions_summary(self, predictions: Any) -> None:
        """Print top-5 predictions to console."""
        top = predictions.sort_values("score", ascending=False).head(5)
        for i, (_, row) in enumerate(top.iterrows(), 1):
            print(f"    #{i} {row['instrument']}  score={row['score']:.4f}")

    def load_plan_instruments(self, plan_dir: Any) -> list[str]:
        """Return instrument codes from ``order_intents.csv`` in *plan_dir*."""
        intents_path = Path(plan_dir) / "order_intents.csv"
        if not intents_path.exists():
            return []
        try:
            df = pd.read_csv(intents_path)
            return sorted(set(df["instrument"].astype(str)))
        except Exception:
            return []

    def save_predictions(self, predictions: Any, run_root: Any, trade_date: str) -> None:
        """Save predictions to the strategy's shared predictions directory.

        Uses ``self._predictions_dir`` (subclass must provide this as a
        property or attribute).
        """
        shared_dir = self._predictions_dir  # type: ignore[attr-defined]
        shared_dir.mkdir(parents=True, exist_ok=True)
        path = shared_dir / f"predictions_{trade_date}.csv"
        predictions.to_csv(path, index=False)
        print(f"  → {len(predictions)} predictions saved: {path}")

    def fetch_open_prices(self, trade_date: str, instruments: list[str]) -> dict[str, float]:
        """Fetch open prices via qlib for the given instruments."""
        from qsys.data.adapter import QlibAdapter

        adapter = QlibAdapter()
        adapter.init_qlib()
        market = adapter.get_features(
            instruments, ["$open"],
            start_time=trade_date, end_time=trade_date,
        )
        if market is None or market.empty:
            return {}
        if isinstance(market.index, pd.MultiIndex):
            market = market.swaplevel().sort_index()
        frame = market.reset_index()
        frame = frame[frame["datetime"].astype(str).str.startswith(trade_date)]
        if frame.empty:
            return {}
        frame = frame.sort_values(["instrument", "datetime"]).drop_duplicates(
            subset=["instrument"], keep="last"
        )
        return frame.set_index("instrument")["$open"].astype(float).to_dict()

    # ── Notification utilities ────────────────────────────────────────

    def send_notification(self, text: str) -> None:
        """Send *text* via Telegram using the shared notifier."""
        from qsys.ops.telegram import send_telegram_message

        print(f"\n{'─' * 50}")
        print("📱 Telegram 通知:")
        print(text)
        print(f"{'─' * 50}\n")
        result = send_telegram_message(text)
        status = result.get("status", "unknown")
        if status == "skipped":
            print(f"  ⚠ Telegram 未配置: {result.get('message', '')}")
        elif status == "failed":
            print(f"  ❌ Telegram 发送失败: {result.get('error', '')}")
        else:
            print(f"  ✅ Telegram 已发送")
