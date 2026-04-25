from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.data.adapter import QlibAdapter

DEFAULT_UNIVERSE = "csi300"
DEFAULT_PROBE_FIELDS = ["$close"]


def _normalize_date(value: str | None) -> str:
    if value:
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    return datetime.now().strftime("%Y-%m-%d")


def _build_payload(
    *,
    requested_date: str,
    resolved_trade_date: str | None,
    last_qlib_date: str | None,
    status: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "requested_date": requested_date,
        "resolved_trade_date": resolved_trade_date,
        "last_qlib_date": last_qlib_date,
        "status": status,
        "reason": reason,
        "is_exact_match": bool(resolved_trade_date and resolved_trade_date == requested_date and status == "success"),
    }


def _probe_has_rows(adapter: QlibAdapter, *, universe: str, probe_date: str) -> bool:
    frame = adapter.get_features(
        universe,
        DEFAULT_PROBE_FIELDS,
        start_time=probe_date,
        end_time=probe_date,
    )
    return frame is not None and not frame.empty


def _read_calendar_dates(adapter: QlibAdapter) -> list[str]:
    cal_path = Path(adapter.qlib_dir) / "calendars" / "day.txt"
    if not cal_path.exists():
        return []
    try:
        df = pd.read_csv(cal_path, header=None)
    except Exception:
        return []
    if df.empty:
        return []
    return [pd.Timestamp(value).strftime("%Y-%m-%d") for value in df.iloc[:, 0].tolist()]


def _get_latest_available_date_on_or_before(adapter: QlibAdapter, requested_date: str, universe: str) -> str | None:
    requested_ts = pd.Timestamp(requested_date)
    candidates = [date for date in _read_calendar_dates(adapter) if pd.Timestamp(date) <= requested_ts]
    for candidate in reversed(candidates):
        if _probe_has_rows(adapter, universe=universe, probe_date=candidate):
            return candidate
    return None


def resolve_daily_trade_date(
    requested_date: str | None,
    *,
    universe: str = DEFAULT_UNIVERSE,
    allow_fallback_to_latest: bool = True,
) -> dict[str, Any]:
    requested = _normalize_date(requested_date)
    adapter = QlibAdapter()
    adapter.init_qlib()

    last_qlib_ts = adapter.get_last_qlib_date()
    last_qlib_date = last_qlib_ts.strftime("%Y-%m-%d") if last_qlib_ts is not None else None
    if last_qlib_date is None:
        return _build_payload(
            requested_date=requested,
            resolved_trade_date=None,
            last_qlib_date=None,
            status="failed",
            reason="no available qlib trading date",
        )

    if _probe_has_rows(adapter, universe=universe, probe_date=requested):
        return _build_payload(
            requested_date=requested,
            resolved_trade_date=requested,
            last_qlib_date=last_qlib_date,
            status="success",
            reason="requested_date is available in qlib",
        )

    if not allow_fallback_to_latest:
        return _build_payload(
            requested_date=requested,
            resolved_trade_date=None,
            last_qlib_date=last_qlib_date,
            status="failed",
            reason="requested_date has no qlib feature rows and fallback is disabled",
        )

    fallback_date = _get_latest_available_date_on_or_before(adapter, requested, universe)
    if fallback_date is None:
        return _build_payload(
            requested_date=requested,
            resolved_trade_date=None,
            last_qlib_date=last_qlib_date,
            status="failed",
            reason="no available qlib trading date on or before requested_date",
        )

    return _build_payload(
        requested_date=requested,
        resolved_trade_date=fallback_date,
        last_qlib_date=last_qlib_date,
        status="fallback_to_latest_available",
        reason="requested_date has no qlib feature rows; using latest available trading date on or before requested_date",
    )


def resolve_training_end_date(
    requested_date: str | None,
    *,
    universe: str = DEFAULT_UNIVERSE,
    allow_fallback_to_latest: bool = True,
) -> dict[str, Any]:
    payload = resolve_daily_trade_date(
        requested_date,
        universe=universe,
        allow_fallback_to_latest=allow_fallback_to_latest,
    )
    if payload["status"] == "success":
        payload["reason"] = "requested train_end is available in qlib"
    elif payload["status"] == "fallback_to_latest_available":
        payload["reason"] = "requested train_end has no qlib feature rows; using latest available trading date"
    return payload
