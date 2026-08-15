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
    # A-share calendars average fewer than five sessions per seven calendar
    # days once public holidays are included.  Use a conservative factor so
    # long maturity embargoes do not silently shorten the requested OOS span.
    estimated = dt - timedelta(days=int(n_days * 1.6) + buffer + 5)
    return estimated.strftime("%Y-%m-%d")


def build_rolling_windows(
    start_date: str,
    end_date: str,
    *,
    train_window_days: int = 252,
    predict_window_days: int | None = None,
    step_days: int = 5,
    label_maturity_lag_trading_days: int = 0,
) -> list[RollingWindow]:
    """Build rolling windows.

    Each window uses the same number of predict days as the step
    (predict = step), so every trading day gets exactly one prediction
    from exactly one model version — no overlap.

    When *label_maturity_lag_trading_days* > 0, the effective train end
    is pushed back by that many trading days so that every sample's label
    (e.g. 180d forward return) is fully realised *before* the predict
    window begins.  If the effective train end falls before train_start,
    the window is skipped with a warning.
    """
    from qsys.data.calendar import get_trading_calendar

    if predict_window_days is not None and predict_window_days != step_days:
        raise ValueError(
            "predict_window_days is a deprecated compatibility alias and "
            "must equal step_days to preserve gap-free, non-overlapping windows"
        )

    # The calendar prefix must cover both the training window and the label
    # maturity embargo.  Backdating only ``train_window_days`` silently skips
    # the first months of long-horizon studies (notably 180d), even when the
    # underlying dataset contains sufficient history.
    history_days = train_window_days + label_maturity_lag_trading_days
    _extended_start = _calendar_backdate(start_date, history_days)
    full_cal = get_trading_calendar(_extended_start, end_date)
    if not full_cal:
        raise ValueError(f"No trading dates in [{_extended_start}, {end_date}]")

    pred_cal = [d for d in full_cal if start_date <= d <= end_date]
    if not pred_cal:
        raise ValueError(f"No trading dates in [{start_date}, {end_date}]")

    windows: list[RollingWindow] = []

    for offset in range(0, len(pred_cal), step_days):
        # Keep the terminal partial window so a study ends on its declared
        # end_date instead of silently truncating to the last full step.
        pred_end_offset = min(offset + step_days - 1, len(pred_cal) - 1)

        predict_start = pred_cal[offset]
        predict_end = pred_cal[pred_end_offset]

        try:
            predict_idx = full_cal.index(predict_start)
        except ValueError:
            continue

        # ── Apply label maturity delay ──────────────────────────────
        if label_maturity_lag_trading_days > 0:
            # effective last training date = (predict_start - 1) - label_maturity_lag
            raw_end_idx = predict_idx - 1
            train_end_idx = raw_end_idx - label_maturity_lag_trading_days
        else:
            train_end_idx = predict_idx - 1

        train_start_idx = train_end_idx - train_window_days + 1

        if train_start_idx < 0:
            continue

        train_start = full_cal[train_start_idx]
        train_end = full_cal[train_end_idx] if train_end_idx >= 0 else full_cal[0]

        # Sanity: effective train window must contain at least some trading days
        if train_end_idx - train_start_idx + 1 < 20:
            continue

        windows.append(RollingWindow(
            window_id=f"w{offset:04d}",
            train_start=train_start,
            train_end=train_end,
            predict_start=predict_start,
            predict_end=predict_end,
        ))

    return windows
