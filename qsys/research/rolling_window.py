"""Rolling window builder — window data structure and construction.

Extracted from ``rolling_runner.py``.  See that module for
``RollingResearchRunner`` and the research pipeline orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RollingWindow:
    window_id: str
    train_start: str
    train_end: str
    predict_start: str
    predict_end: str


def _calendar_backdate(start_date: str, n_days: int, buffer: int = 10) -> str:
    from datetime import datetime, timedelta
    dt = datetime.strptime(start_date, "%Y-%m-%d")
    estimated = dt - timedelta(days=int(n_days * 1.4) + buffer + 5)
    return estimated.strftime("%Y-%m-%d")


def build_rolling_windows(
    start_date: str,
    end_date: str,
    *,
    train_window_days: int = 252,
    step_days: int = 5,
) -> list[RollingWindow]:
    """Build rolling windows.

    Each window uses the same number of predict days as the step
    (predict = step), so every trading day gets exactly one prediction
    from exactly one model version — no overlap.
    """
    from qsys.data.calendar import get_trading_calendar

    _extended_start = _calendar_backdate(start_date, train_window_days)
    full_cal = get_trading_calendar(_extended_start, end_date)
    if not full_cal:
        raise ValueError(f"No trading dates in [{_extended_start}, {end_date}]")

    pred_cal = [d for d in full_cal if start_date <= d <= end_date]
    if not pred_cal:
        raise ValueError(f"No trading dates in [{start_date}, {end_date}]")

    windows: list[RollingWindow] = []

    for offset in range(0, len(pred_cal), step_days):
        pred_end_offset = offset + step_days - 1
        if pred_end_offset >= len(pred_cal):
            break

        predict_start = pred_cal[offset]
        predict_end = pred_cal[pred_end_offset]

        try:
            predict_idx = full_cal.index(predict_start)
        except ValueError:
            continue

        train_end_idx = predict_idx - 1
        train_start_idx = predict_idx - train_window_days

        if train_start_idx < 0:
            continue

        train_start = full_cal[train_start_idx]
        train_end = full_cal[train_end_idx] if train_end_idx >= 0 else full_cal[0]

        windows.append(RollingWindow(
            window_id=f"w{offset:04d}",
            train_start=train_start,
            train_end=train_end,
            predict_start=predict_start,
            predict_end=predict_end,
        ))

    return windows
