from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import data_sync
from scripts.ops.sync_csi800_daily import _abort_if_stage_failed, _write_audit


def test_data_sync_routes_csi1800_to_canonical_sync_entrypoint():
    with patch.object(
        sys,
        "argv",
        ["data_sync.py", "--universe", "csi1800", "--target-date", "2026-08-21"],
    ), patch("scripts.data_sync.subprocess.run") as run:
        data_sync.main()

    command = run.call_args.args[0]
    assert command[1].endswith("scripts/ops/sync_csi800_daily.py")
    assert command[2:] == [
        "--universe", "csi1800", "--target-date", "2026-08-21"
    ]
    assert run.call_args.kwargs["check"] is True


def test_csi1800_audit_uses_distinct_target_date_path(tmp_path: Path):
    path = _write_audit(
        tmp_path,
        {"universe": "csi1800", "target_date": "20260821"},
    )

    assert path == tmp_path / "sync_csi1800_20260821.json"


def test_sync_target_rejects_fallback_date():
    with pytest.raises(RuntimeError, match="exact synced csi1800"):
        data_sync._require_exact_sync_target(
            {
                "status": "fallback_to_latest_available",
                "resolved_trade_date": "2026-08-20",
            },
            requested_target="2026-08-21",
            universe="csi1800",
        )


def test_repair_audits_follow_external_data_root(tmp_path: Path):
    runtime = tmp_path / "runtime"
    data_root = tmp_path / "production" / "data"

    result = data_sync._data_sync_run_root(data_root, "20260823_210000")

    assert result == data_root / "audit" / "data_sync" / "20260823_210000"
    assert runtime not in result.parents


def test_failed_raw_stage_is_audited_and_blocks(tmp_path: Path):
    report = {"universe": "csi1800", "target_date": "20260821"}
    with pytest.raises(RuntimeError, match="raw_fetch failed"):
        _abort_if_stage_failed(
            report,
            stage="raw_fetch",
            summary={"status": "failed", "error": "source timeout"},
            do_apply=True,
            audit_dir=tmp_path,
        )

    assert report["overall_status"] == "failed"
    assert report["failure_stage"] == "raw_fetch"
    assert (tmp_path / "sync_csi1800_20260821.json").is_file()


def test_failed_qlib_stage_blocks_even_without_apply(tmp_path: Path):
    with pytest.raises(RuntimeError, match="qlib_convert failed"):
        _abort_if_stage_failed(
            {"universe": "csi1800", "target_date": "20260821"},
            stage="qlib_convert",
            summary={"status": "failed", "error": "dump failed"},
            do_apply=False,
            audit_dir=tmp_path,
        )
    assert not list(tmp_path.iterdir())


def test_failed_registry_refresh_is_audited_and_blocks(tmp_path: Path):
    report = {"universe": "csi1800", "target_date": "20260821"}
    with pytest.raises(RuntimeError, match="refresh_instruments failed"):
        _abort_if_stage_failed(
            report,
            stage="refresh_instruments",
            summary={"status": "failed", "error": "registry write failed"},
            do_apply=True,
            audit_dir=tmp_path,
        )

    audit = tmp_path / "sync_csi1800_20260821.json"
    assert audit.is_file()
    assert report["failure_stage"] == "refresh_instruments"
