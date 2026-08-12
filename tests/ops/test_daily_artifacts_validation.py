from __future__ import annotations

import json
from pathlib import Path

import pytest

from qsys.ops.daily_artifacts import (
    validate_daily_stage_manifest,
    write_daily_manifest,
)


def _write(run_root: Path, *, strategy_id: str = "alpha_v1") -> None:
    write_daily_manifest(
        run_root,
        trade_date="2026-08-11",
        stage="preopen",
        strategy_id=strategy_id,
        account_id="shadow_alpha_v1",
        stage_status={"preopen": "completed"},
    )


def test_valid_manifest_proves_stage_completion(tmp_path: Path) -> None:
    _write(tmp_path)
    payload = validate_daily_stage_manifest(
        tmp_path,
        trade_date="2026-08-11",
        strategy_id="alpha_v1",
        stage="preopen",
    )
    assert payload["stage_status"]["preopen"] == "completed"


def test_missing_manifest_is_fatal(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="without daily manifest"):
        validate_daily_stage_manifest(
            tmp_path,
            trade_date="2026-08-11",
            strategy_id="alpha_v1",
            stage="preopen",
        )


def test_stale_or_cross_strategy_manifest_is_fatal(tmp_path: Path) -> None:
    _write(tmp_path, strategy_id="alpha_v2")
    with pytest.raises(RuntimeError, match="identity mismatch"):
        validate_daily_stage_manifest(
            tmp_path,
            trade_date="2026-08-11",
            strategy_id="alpha_v1",
            stage="preopen",
        )


def test_manifest_without_completed_status_is_fatal(tmp_path: Path) -> None:
    _write(tmp_path)
    path = tmp_path / "daily_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["stage_status"]["preopen"] = "started"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="does not prove preopen completion"):
        validate_daily_stage_manifest(
            tmp_path,
            trade_date="2026-08-11",
            strategy_id="alpha_v1",
            stage="preopen",
        )
