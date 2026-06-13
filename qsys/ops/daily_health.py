"""Daily ops health checks — data freshness, feature readiness, model selection.

Extracted from scripts/ops/run_shadow_daily.py to provide reusable health-check
functions for the DailyRunner pipeline (UC-8/UC-9 entrypoints).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from qsys.config import cfg
from qsys.data.adapter import QlibAdapter
from qsys.data.health import inspect_qlib_data_health
from qsys.ops.instrument_coverage import (
    DEFAULT_MIN_ACTIVE_INSTRUMENTS,
    summarize_universe_registry,
)
from qsys.ops.model_registry import (
    latest_shadow_model_is_usable,
    read_latest_shadow_model,
)
from qsys.ops.state import load_json

_DEFAULT_MAINLINE = "feature_173"


def load_latest_presync(base_dir: Path) -> dict[str, Any]:
    """Load the latest presync run metadata, if available."""
    latest = load_json(base_dir / "runs" / "latest_shadow_presync.json")
    if not latest:
        return {}
    summary_path = latest.get("presync_summary_path")
    if summary_path:
        summary_payload = load_json(Path(str(summary_path)))
        if summary_payload:
            latest["summary"] = summary_payload
            latest.setdefault("ready_for_daily_shadow",
                              summary_payload.get("ready_for_daily_shadow"))
            latest.setdefault("overall_status",
                              summary_payload.get("overall_status"))
    return latest


def build_latest_presync_payload(
    latest_presync: dict[str, Any],
) -> dict[str, Any] | None:
    if not latest_presync:
        return None
    summary = latest_presync.get("summary") or {}
    return {
        "run_id": latest_presync.get("run_id") or summary.get("run_id"),
        "overall_status": latest_presync.get("overall_status")
                         or summary.get("overall_status"),
        "ready_for_daily_shadow": bool(
            latest_presync.get("ready_for_daily_shadow")
            or summary.get("ready_for_daily_shadow")),
    }


def build_data_freshness_status(
    *,
    trade_date: str,
    universe: str = "csi300",
    mainline_object_name: str = _DEFAULT_MAINLINE,
    latest_presync: dict[str, Any] | None = None,
    require_presync_ready: bool = False,
) -> dict[str, Any]:
    """Check data freshness for daily ops.

    Returns a status dict with keys: trade_date, status (success|failed),
    health_report, blocking_issues, etc.
    """
    data_root = cfg.get_path("root")
    qlib_dir = cfg.get_path("qlib_bin")
    lpp = build_latest_presync_payload(latest_presync or {})

    min_active = DEFAULT_MIN_ACTIVE_INSTRUMENTS

    if require_presync_ready:
        if lpp is None:
            return _failed_data_status(
                trade_date, universe, mainline_object_name,
                data_root, qlib_dir, min_active,
                ["presync_required_but_missing"], latest_presync=lpp,
            )
        if not bool(lpp.get("ready_for_daily_shadow")):
            return _failed_data_status(
                trade_date, universe, mainline_object_name,
                data_root, qlib_dir, min_active,
                ["presync_required_but_not_ready"], latest_presync=lpp,
            )

    from qsys.research.mainline import resolve_mainline_feature_config
    feature_fields = resolve_mainline_feature_config(mainline_object_name) or ["$close"]
    adapter = QlibAdapter()
    adapter.init_qlib()
    last_qlib_date = adapter.get_last_qlib_date()
    report = inspect_qlib_data_health(trade_date, feature_fields, universe=universe)
    instrument_summary = summarize_universe_registry(
        adapter, universe=universe, trade_date=trade_date,
    )

    blocking_issues = list(report.to_dict().get("blocking_issues", []))
    if instrument_summary.active_on_trade_date < min_active:
        blocking_issues.append(
            f"instrument_coverage_mismatch: active="
            f"{instrument_summary.active_on_trade_date} < min={min_active}"
        )

    return {
        "trade_date": trade_date,
        "status": "success" if not blocking_issues else "failed",
        "mode": "freshness_check_only",
        "lightweight_check_only": True,
        "universe": universe,
        "mainline_object_name": mainline_object_name,
        "data_root": str(data_root),
        "qlib_dir": str(qlib_dir),
        "data_root_exists": bool(data_root.exists()),
        "qlib_dir_exists": bool(qlib_dir.exists()),
        "last_qlib_date": (
            last_qlib_date.strftime("%Y-%m-%d")
            if last_qlib_date is not None else None
        ),
        "health_report": {**report.to_dict(), "blocking_issues": blocking_issues},
        "active_instruments": instrument_summary.active_on_trade_date,
        "min_active_instruments": min_active,
        "instrument_coverage_status": instrument_summary.coverage_status,
        "instrument_coverage": instrument_summary.to_dict(),
        "latest_presync": lpp,
        "require_presync_ready": require_presync_ready,
        "error": None if not blocking_issues else "; ".join(blocking_issues),
    }


def build_feature_readiness_status(
    *,
    trade_date: str,
    universe: str = "csi300",
    mainline_object_name: str = _DEFAULT_MAINLINE,
) -> dict[str, Any]:
    """Check feature readiness for daily ops.

    Returns status dict with keys: trade_date, status, degradation_level, etc.
    """
    from qsys.research.mainline import (
        MAINLINE_OBJECTS,
        resolve_mainline_feature_config,
    )
    from qsys.research.readiness import (
        build_feature_coverage,
        build_model_input_frame,
        build_readiness_summary,
    )

    feature_config = resolve_mainline_feature_config(mainline_object_name)
    if not feature_config:
        return {
            "trade_date": trade_date,
            "status": "failed",
            "mode": "readiness_check_only",
            "lightweight_check_only": True,
            "mainline_object_name": mainline_object_name,
            "field_count": 0,
            "usable_field_count": 0,
            "degradation_level": "blocked",
            "notes": [f"No feature config found for {mainline_object_name}"],
            "error": f"No feature config found for {mainline_object_name}",
        }

    adapter = QlibAdapter()
    adapter.init_qlib()
    frame = adapter.get_features(
        universe, feature_config,
        start_time=trade_date, end_time=trade_date,
    )
    model_path = (
        cfg.get_path("root") / "models"
        / MAINLINE_OBJECTS[mainline_object_name].model_name
    )
    model_input_frame = build_model_input_frame(
        feature_frame=frame,
        model_path=model_path if model_path.exists() else None,
    )
    coverage = build_feature_coverage(
        spec=MAINLINE_OBJECTS[mainline_object_name],
        frame=frame,
        model_input_frame=model_input_frame,
    )
    summary = build_readiness_summary(
        spec=MAINLINE_OBJECTS[mainline_object_name],
        coverage=coverage,
    )
    return {
        "trade_date": trade_date,
        "status": "success",
        "mode": "readiness_check_only",
        "lightweight_check_only": True,
        "mainline_object_name": mainline_object_name,
        **summary,
        "error": None,
    }


def select_latest_shadow_model(
    base_dir: Path,
) -> tuple[dict[str, Any] | None, str | None]:
    """Select the latest usable shadow model.

    Returns (payload, None) on success, (None, error_message) on failure.
    """
    payload = read_latest_shadow_model(base_dir)
    if not latest_shadow_model_is_usable(base_dir, payload):
        return None, "no usable latest model"
    return payload, None


def resolve_feature_stage_status(payload: dict[str, Any]) -> str:
    """Map feature readiness payload to stage status."""
    degradation_level = str(payload.get("degradation_level", "") or "")
    status_value = str(payload.get("status", "failed") or "failed")
    if degradation_level in {"blocked", "extended_blocked"}:
        return "failed"
    if degradation_level == "extended_warn":
        return "success"
    return "success" if status_value == "success" else "failed"


# ── Internal helpers ─────────────────────────────────────────────────


def _failed_data_status(
    trade_date: str,
    universe: str,
    mainline_object_name: str,
    data_root: Path,
    qlib_dir: Path,
    min_active: int,
    blocking_issues: list[str],
    *,
    latest_presync: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "trade_date": trade_date,
        "status": "failed",
        "mode": "freshness_check_only",
        "lightweight_check_only": True,
        "universe": universe,
        "mainline_object_name": mainline_object_name,
        "data_root": str(data_root),
        "qlib_dir": str(qlib_dir),
        "data_root_exists": bool(data_root.exists()),
        "qlib_dir_exists": bool(qlib_dir.exists()),
        "last_qlib_date": None,
        "health_report": {"blocking_issues": blocking_issues},
        "active_instruments": 0,
        "min_active_instruments": min_active,
        "instrument_coverage_status": "unknown",
        "instrument_coverage": {},
        "latest_presync": latest_presync,
        "require_presync_ready": True,
        "error": "; ".join(blocking_issues),
    }
