from __future__ import annotations

from datetime import datetime
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

    requested_frame = adapter.get_features(
        universe,
        DEFAULT_PROBE_FIELDS,
        start_time=requested,
        end_time=requested,
    )
    if requested_frame is not None and not requested_frame.empty:
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

    latest_frame = adapter.get_features(
        universe,
        DEFAULT_PROBE_FIELDS,
        start_time=last_qlib_date,
        end_time=last_qlib_date,
    )
    if latest_frame is None or latest_frame.empty:
        return _build_payload(
            requested_date=requested,
            resolved_trade_date=None,
            last_qlib_date=last_qlib_date,
            status="failed",
            reason="no available qlib trading date",
        )

    return _build_payload(
        requested_date=requested,
        resolved_trade_date=last_qlib_date,
        last_qlib_date=last_qlib_date,
        status="fallback_to_latest_available",
        reason="requested_date has no qlib feature rows; using latest available trading date",
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
