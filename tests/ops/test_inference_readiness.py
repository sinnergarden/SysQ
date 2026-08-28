from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from qsys.ops.inference_readiness import check_inference_ready
from qsys.signal.model_blend_inference import InferenceContractError


def _settings() -> dict[str, object]:
    return {
        "bundle_id": "bundle",
        "bundle_hash": "a" * 64,
        "market_close_cutoff": "18:00",
        "universe_snapshot_semantics": "current_constituents_snapshot",
        "feature_snapshot_lag_sessions": 1,
    }


def test_auto_request_uses_the_supplied_run_anchor(tmp_path: Path) -> None:
    anchor = datetime(2026, 8, 28, 1, 0, tzinfo=timezone.utc)
    config = {"inference": {"engine": "pinned_model_blend_v1"}}
    with (
        patch(
            "qsys.ops.inference_readiness.validate_inference_config",
            return_value=_settings(),
        ),
        patch(
            "qsys.ops.inference_readiness.load_open_dates",
            return_value=["2026-08-26", "2026-08-27", "2026-08-28"],
        ),
        patch(
            "qsys.ops.inference_readiness.resolve_inference_dates",
            side_effect=InferenceContractError("stop after date dispatch"),
        ) as resolve,
    ):
        results = check_inference_ready(
            "auto",
            "financial_rc",
            project_root=tmp_path,
            now=anchor,
            strategy_config=config,
        )

    assert results[0] == (
        "signal_date request",
        True,
        "auto (bounded by one run anchor)",
    )
    assert results[-1] == (
        "date contract",
        False,
        "stop after date dispatch",
    )
    assert resolve.call_args.args[0] == "auto"
    assert resolve.call_args.kwargs["now"] is anchor


def test_invalid_signal_date_stops_before_config_load(tmp_path: Path) -> None:
    with patch("qsys.ops.inference_readiness.load_strategy_config") as load:
        results = check_inference_ready(
            "not-a-date",
            "financial_rc",
            project_root=tmp_path,
        )

    assert results[0][1] is False
    load.assert_not_called()
