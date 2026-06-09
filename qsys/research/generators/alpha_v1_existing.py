"""Alpha V1 Existing — thin adapter over saved alpha_v1 prediction hooks.

This generator reuses ``AlphaV1StrategyAdapter.generate_predictions_for_date``
for per-date prediction.  In CI/mock environments, the adapter's data path can
be monkeypatched.

The generator wraps the adapter so that the rolling runner's per-window
lifecycle produces one SignalStore-compatible DataFrame per window,
preserving preopen semantics (data_date <= previous_trading_day(trade_date)).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from qsys.research.generators.utils import build_prev_trading_date_lookup


def _resolve_adapter(project_root: Path | None = None) -> Any:
    """Create and return an AlphaV1StrategyAdapter instance."""
    from qsys.strategy.alpha_v1.adapter import AlphaV1StrategyAdapter
    return AlphaV1StrategyAdapter(project_root=project_root)


@dataclass
class AlphaV1ExistingGenerator:
    """Generator wrapper around existing alpha_v1 prediction path.

    For each predict window, calls the adapter for each trade_date
    and assembles rolling predictions.

    Parameters
    ----------
    adapter_factory:
        Callable that returns an adapter with a
        ``generate_predictions_for_date(trade_date, data_date=None)``
        method.  Defaults to ``AlphaV1StrategyAdapter``.
    project_root:
        Project root for path resolution (passed to adapter).
    """

    adapter_factory: Callable[..., Any] | None = None
    project_root: Path | None = None

    _adapter: Any = field(default=None, repr=False)

    def _get_adapter(self) -> Any:
        if self._adapter is None:
            factory = self.adapter_factory or _resolve_adapter
            self._adapter = factory(project_root=self.project_root)
        return self._adapter

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
        """Generate alpha_v1 predictions for rolling window.

        Uses the existing adapter's ``generate_predictions_for_date``
        per trade_date and assembles into a SignalStore-compatible frame.
        """
        try:
            from qsys.data.calendar import get_trading_calendar
            cal = get_trading_calendar(predict_start, predict_end) or []
        except Exception:
            cal = []

        if not cal:
            dt = datetime.strptime(predict_start, "%Y-%m-%d")
            end_dt = datetime.strptime(predict_end, "%Y-%m-%d")
            cal = []
            while dt <= end_dt:
                if dt.weekday() < 5:
                    cal.append(dt.strftime("%Y-%m-%d"))
                dt += timedelta(days=1)

        # Build data_date lookup using full trading calendar
        prev_td_lookup = build_prev_trading_date_lookup(predict_start, predict_end)

        adapter = self._get_adapter()
        all_rows: list[dict[str, Any]] = []

        for td in sorted(cal):
            try:
                pred_df = adapter.generate_predictions_for_date(td)
            except Exception as exc:
                raise RuntimeError(
                    f"AlphaV1Existing: prediction failed for {td}: {exc}"
                ) from exc

            if pred_df is None or pred_df.empty:
                continue

            dd = prev_td_lookup.get(td)
            if dd is None:
                # Absolute fallback
                _dt = datetime.strptime(td, "%Y-%m-%d")
                _prev = _dt - timedelta(days=1)
                while _prev.weekday() >= 5:
                    _prev -= timedelta(days=1)
                dd = _prev.strftime("%Y-%m-%d")

            for _, row in pred_df.iterrows():
                all_rows.append({
                    "trade_date": td,
                    "data_date": dd,
                    "instrument": str(row.get("instrument", "")),
                    "signal_id": signal_id,
                    "signal_run_id": signal_run_id,
                    "score": float(row.get("score", 0.0)),
                })

        if not all_rows:
            raise RuntimeError(
                f"AlphaV1Existing: no predictions for "
                f"[{predict_start}, {predict_end}]"
            )

        return pd.DataFrame(all_rows)
