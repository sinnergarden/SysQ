from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts import data_sync
import scripts.ops.sync_csi800_daily as daily_sync
from qsys.data.source_audit import SourceAuditStore, data_writer_lock
from scripts.ops.sync_csi800_daily import (
    _abort_if_stage_failed,
    _canonical_symbols_with_data_on_date,
    _load_csi1800_research_union,
    _publish_wrapper_terminal_gates,
    _repair_same_date_qlib_gap,
    _write_audit,
)

DUMMY_RECEIPT_SHA = "a" * 64


def test_historical_evidence_uses_manifest_bound_registry_not_membership_union(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "research/universes/csi1800_pit_v2"
    artifact.mkdir(parents=True)
    registry = artifact / "csi1800_pit_union.txt"
    registry.write_text(
        "000001.SZ\t20200101\t20201231\n"
        "000001.SZ\t20210101\t20211231\n"
        "000002.SZ\t20200101\t20211231\n",
        encoding="utf-8",
    )
    (artifact / "manifest.json").write_text(json.dumps({
        "registry_sha256": hashlib.sha256(registry.read_bytes()).hexdigest(),
        "n_registry_instruments": 2,
        "n_unique_instruments": 99,
    }), encoding="utf-8")

    codes, manifest = _load_csi1800_research_union(tmp_path)

    assert codes == ["000001.SZ", "000002.SZ"]
    assert manifest["constituent_count"] == 2
    assert manifest["registry_sha256"] == hashlib.sha256(registry.read_bytes()).hexdigest()


def test_dry_run_does_not_forward_force_fetch_to_canonical_sync_entrypoint():
    with patch.object(
        sys,
        "argv",
        [
            "data_sync.py", "--universe", "csi1800",
            "--target-date", "2026-08-21", "--force-fetch",
        ],
    ), patch("scripts.data_sync.subprocess.run") as run:
        data_sync.main()

    command = run.call_args.args[0]
    assert command[1].endswith("scripts/ops/sync_csi800_daily.py")
    assert command[2:] == [
        "--universe", "csi1800", "--target-date", "2026-08-21"
    ]
    assert run.call_args.kwargs["check"] is True


def test_applied_wrapper_passes_verified_lock_fd_and_one_run_id(tmp_path: Path):
    with patch.object(
        sys,
        "argv",
        [
            "data_sync.py", "--universe", "csi1800",
            "--target-date", "2026-08-21", "--apply", "--force-fetch",
        ],
    ), patch(
        "scripts.data_sync.subprocess.run",
        side_effect=subprocess.CalledProcessError(2, "sync"),
    ) as run, patch(
        "qsys.config.cfg.get_path", return_value=str(tmp_path)
    ), pytest.raises(subprocess.CalledProcessError):
        data_sync.main()

    command = run.call_args.args[0]
    assert command.count("--run-id") == 1
    run_id = command[command.index("--run-id") + 1]
    assert run_id.startswith("data_sync_")
    assert "--wrapper-managed-finalize" in command
    assert command.count("--force-fetch") == 1
    inherited_fd = run.call_args.kwargs["pass_fds"][0]
    assert run.call_args.kwargs["env"]["QSYS_DATA_WRITER_LOCK_FD"] == str(inherited_fd)
    receipts = list((tmp_path / "audit" / "source_runs" / run_id).glob("receipt.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text())["trust_state"] == "untrusted"


def test_applied_config_wrapper_forwards_force_fetch_to_shared_child(tmp_path: Path):
    config = tmp_path / "sync.yaml"
    config.write_text(
        "\n".join(
            [
                "universe: csi800",
                "date_range:",
                "  end_date: '2026-08-21'",
                "execution:",
                "  apply: true",
                "tasks:",
                "  qlib_bin: true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    with patch.object(
        sys,
        "argv",
        ["data_sync.py", "--config", str(config), "--force-fetch"],
    ), patch(
        "scripts.data_sync.subprocess.run",
        side_effect=subprocess.CalledProcessError(2, "sync"),
    ) as run, patch(
        "qsys.config.cfg.get_path", return_value=str(tmp_path)
    ), pytest.raises(subprocess.CalledProcessError):
        data_sync.main()

    command = run.call_args.args[0]
    assert command.count("--force-fetch") == 1
    assert command.count("--run-id") == 1
    assert "--wrapper-managed-finalize" in command
    assert command[command.index("--universe") + 1] == "csi800"


def _write_failed_wrapper_receipt(
    tmp_path: Path,
    *,
    run_id: str,
    universe: str = "csi1800",
    target_date: str = "20260821",
    trust_state: str = "untrusted",
) -> tuple[SourceAuditStore, Path]:
    audit = SourceAuditStore(tmp_path / "audit" / "audit.db")
    audit.append_event(run_id, "run_started", {
        "entrypoint": "scripts/data_sync.py",
        "universe": universe,
        "target_date": target_date,
    })
    root = tmp_path / "audit" / "source_runs"
    if trust_state == "untrusted":
        result = audit.record_crash_receipt(
            run_id=run_id,
            receipt_root=root,
            entrypoint="scripts/data_sync.py",
            error="injected failure",
        )
        return audit, Path(result["receipt_path"])
    return audit, audit.export_receipt(
        run_id, root, trust_state=trust_state, gates={}
    )


@pytest.mark.parametrize(
    "argv,match",
    [
        (
            ["data_sync.py", "--universe", "csi1800", "--target-date", "2026-08-21",
             "--resume-from-run-id", "old", "--force-fetch"],
            "mutually exclusive",
        ),
        (
            ["data_sync.py", "--universe", "csi1800", "--target-date", "2026-08-21",
             "--resume-from-run-id", "old"],
            "apply-only",
        ),
    ],
)
def test_wrapper_resume_is_apply_only_and_conflicts_with_force(argv, match):
    with patch.object(sys, "argv", argv), pytest.raises(SystemExit) as stopped:
        data_sync._main_under_writer_lock(None)
    assert stopped.value.code == 2


def test_wrapper_validates_and_forwards_explicit_resume_before_child(tmp_path: Path):
    old_run = "explicit-failed"
    audit, old_receipt = _write_failed_wrapper_receipt(tmp_path, run_id=old_run)
    old_bytes = old_receipt.read_bytes()
    with patch.object(
        sys,
        "argv",
        [
            "data_sync.py", "--universe", "csi1800", "--target-date", "2026-08-21",
            "--apply", "--resume-from-run-id", old_run,
        ],
    ), patch(
        "scripts.data_sync.subprocess.run",
        side_effect=subprocess.CalledProcessError(2, "sync"),
    ) as run, patch(
        "qsys.config.cfg.get_path", return_value=str(tmp_path)
    ), pytest.raises(subprocess.CalledProcessError):
        data_sync.main()

    command = run.call_args.args[0]
    assert command.count("--resume-from-run-id") == 1
    assert command[command.index("--resume-from-run-id") + 1] == old_run
    assert command.count("--resume-from-receipt-sha256") == 1
    assert command[command.index("--resume-from-receipt-sha256") + 1] == (
        hashlib.sha256(old_receipt.read_bytes()).hexdigest()
    )
    assert command.count("--run-id") == 1
    assert "--wrapper-managed-finalize" in command
    fresh_run = command[command.index("--run-id") + 1]
    events = audit.run_evidence_summary(fresh_run)["events"]
    lineage = [event for event in events if event["event_type"] == "resume_from_run"]
    assert len(lineage) == 1
    assert lineage[0]["payload"]["resume_from_run_id"] == old_run
    assert old_receipt.read_bytes() == old_bytes


def test_applied_config_wrapper_forwards_explicit_resume_to_shared_child(
    tmp_path: Path,
) -> None:
    old_run = "config-explicit-failed"
    _write_failed_wrapper_receipt(
        tmp_path, run_id=old_run, universe="csi800"
    )
    config = tmp_path / "sync-resume.yaml"
    config.write_text(
        "\n".join(
            [
                "universe: csi800",
                "date_range:",
                "  end_date: '2026-08-21'",
                "execution:",
                "  apply: true",
                "tasks:",
                "  qlib_bin: true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    with patch.object(
        sys,
        "argv",
        ["data_sync.py", "--config", str(config), "--resume-from-run-id", old_run],
    ), patch(
        "scripts.data_sync.subprocess.run",
        side_effect=subprocess.CalledProcessError(2, "sync"),
    ) as run, patch(
        "qsys.config.cfg.get_path", return_value=str(tmp_path)
    ), pytest.raises(subprocess.CalledProcessError):
        data_sync.main()

    command = run.call_args.args[0]
    assert command.count("--resume-from-run-id") == 1
    assert command[command.index("--resume-from-run-id") + 1] == old_run
    assert command.count("--resume-from-receipt-sha256") == 1
    assert command.count("--run-id") == 1
    assert "--wrapper-managed-finalize" in command
    assert command[command.index("--universe") + 1] == "csi800"


@pytest.mark.parametrize(
    "source_universe,source_target,trust_state",
    [
        ("csi800", "20260821", "untrusted"),
        ("csi1800", "20260820", "untrusted"),
        ("csi1800", "20260821", "trusted"),
    ],
)
def test_wrapper_rejects_wrong_or_trusted_resume_source_before_child(
    tmp_path: Path, source_universe: str, source_target: str, trust_state: str,
) -> None:
    old_run = f"bad-source-{source_universe}-{source_target}-{trust_state}"
    _write_failed_wrapper_receipt(
        tmp_path,
        run_id=old_run,
        universe=source_universe,
        target_date=source_target,
        trust_state=trust_state,
    )
    with patch.object(
        sys,
        "argv",
        [
            "data_sync.py", "--universe", "csi1800", "--target-date", "20260821",
            "--apply", "--resume-from-run-id", old_run,
        ],
    ), patch(
        "scripts.data_sync.subprocess.run"
    ) as run, patch(
        "qsys.config.cfg.get_path", return_value=str(tmp_path)
    ), pytest.raises(ValueError):
        data_sync.main()
    run.assert_not_called()


def test_inner_wrapper_mode_rejects_non_inherited_lock(tmp_path: Path):
    with data_writer_lock(tmp_path) as direct_lock, patch.object(
        sys,
        "argv",
        ["sync", "--apply", "--wrapper-managed-finalize", "--run-id", "lock-test"],
    ), pytest.raises(SystemExit):
        daily_sync._main_under_writer_lock(direct_lock)


@pytest.mark.parametrize(
    "argv,match",
    [
        (
            ["sync", "--apply", "--resume-from-run-id", "old",
             "--resume-from-receipt-sha256", DUMMY_RECEIPT_SHA],
            "wrapper-managed",
        ),
        (
            ["sync", "--wrapper-managed-finalize", "--resume-from-run-id", "old",
             "--resume-from-receipt-sha256", DUMMY_RECEIPT_SHA],
            "requires --apply",
        ),
        (
            ["sync", "--apply", "--wrapper-managed-finalize", "--resume-from-run-id", "old",
             "--resume-from-receipt-sha256", DUMMY_RECEIPT_SHA, "--force-fetch"],
            "mutually exclusive",
        ),
        (
            ["sync", "--apply", "--wrapper-managed-finalize",
             "--resume-from-run-id", "old"],
            "requires --resume-from-receipt-sha256",
        ),
    ],
)
def test_inner_resume_argument_contract(
    argv: list[str], match: str, capsys: pytest.CaptureFixture[str],
) -> None:
    inherited = SimpleNamespace(inherited=True)
    with patch.object(sys, "argv", argv), pytest.raises(SystemExit):
        daily_sync._main_under_writer_lock(inherited)
    assert match in capsys.readouterr().err


def test_inner_historical_resume_reaches_receipt_validation_before_supplier_work() -> None:
    inherited = SimpleNamespace(inherited=True)
    with patch.object(
        sys,
        "argv",
        [
            "sync", "--apply", "--wrapper-managed-finalize",
            "--resume-from-run-id", "old",
            "--resume-from-receipt-sha256", DUMMY_RECEIPT_SHA,
            "--target-date", "20260821",
            "--repair-start-date", "20260820",
        ],
    ), patch.object(
        daily_sync, "_resolve_target_date", return_value="20260821"
    ), patch.object(
        daily_sync, "QlibAdapter"
    ) as adapter, patch.object(
        daily_sync, "TushareCollector"
    ) as collector, pytest.raises(ValueError, match="receipt missing"):
        daily_sync._main_under_writer_lock(inherited)
    adapter.assert_not_called()
    collector.assert_not_called()


def test_inner_rejects_wrapper_receipt_sha_mismatch_before_qlib_or_supplier_work(
    tmp_path: Path,
) -> None:
    old_run = "sha-mismatch-old"
    current_run = "sha-mismatch-current"
    _write_failed_wrapper_receipt(tmp_path, run_id=old_run)
    inherited = SimpleNamespace(inherited=True)
    with patch.object(
        sys,
        "argv",
        [
            "sync", "--apply", "--wrapper-managed-finalize",
            "--run-id", current_run, "--resume-from-run-id", old_run,
            "--resume-from-receipt-sha256", "0" * 64,
            "--universe", "csi1800", "--target-date", "20260821",
        ],
    ), patch.object(
        daily_sync.cfg, "get_path", return_value=str(tmp_path)
    ), patch.object(
        daily_sync, "_resolve_target_date", return_value="20260821"
    ), patch.object(
        daily_sync, "QlibAdapter"
    ) as adapter, patch.object(
        daily_sync, "TushareCollector"
    ) as collector, pytest.raises(ValueError, match="SHA-256 mismatch"):
        daily_sync._main_under_writer_lock(inherited)
    adapter.assert_not_called()
    collector.assert_not_called()


def test_inner_resume_bypasses_complete_precheck_and_calls_full_universe_collector(
    tmp_path: Path,
) -> None:
    target = "20260821"
    old_run = "precheck-old"
    current_run = "precheck-fresh"
    audit, old_receipt = _write_failed_wrapper_receipt(tmp_path, run_id=old_run)
    audit.append_event(current_run, "run_started", {
        "entrypoint": "scripts/data_sync.py", "universe": "csi1800",
        "target_date": target,
    })
    captured = {}

    class Adapter:
        qlib_dir = tmp_path / "qlib"

        def init_qlib(self):
            pass

        def convert_incremental(self, _since):
            pass

        def _refresh_universe_instruments(self, **_kwargs):
            pass

    class Store:
        pass

    def raw_fetch(_collector, codes, target_dt, **kwargs):
        captured.update({"codes": list(codes), "target": target_dt, **kwargs})
        kwargs["audit_store"].append_event(
            kwargs["run_id"], "canonical_commit", {"status": "success"}
        )
        kwargs["audit_store"].append_event(
            kwargs["run_id"], "source_scope_coverage",
            {"status": "success", "suspension_query_status": "not_required"},
        )
        return {
            "status": "success", "mutations": [],
            "source_scope_coverage": {"status": "success"},
        }

    snapshot = SimpleNamespace(
        instruments=["000001.SZ", "000002.SZ"],
        to_dict=lambda: {"snapshot_semantics": "pit"},
    )
    health = SimpleNamespace(ok=True, blocking_issues=[], warnings=[])
    inherited = SimpleNamespace(inherited=True)
    with patch.object(
        sys,
        "argv",
        [
            "sync", "--apply", "--wrapper-managed-finalize",
            "--run-id", current_run, "--resume-from-run-id", old_run,
            "--resume-from-receipt-sha256",
            hashlib.sha256(old_receipt.read_bytes()).hexdigest(),
            "--universe", "csi1800", "--target-date", target,
        ],
    ), patch.multiple(
        daily_sync,
        QlibAdapter=Adapter,
        TushareCollector=lambda: object(),
        StockDataStore=Store,
        _resolve_target_date=lambda _value: target,
        _check_stock_data_status=lambda *_args: (_ for _ in ()).throw(
            AssertionError("resume must not take canonical precheck noop")
        ),
        _do_raw_fetch=raw_fetch,
        _update_index_daily=lambda *_args: {},
        _refresh_and_verify_changed_symbols=lambda *_args, **_kwargs: {
            "status": "success", "changed_symbols": [], "verified_value_count": 0,
        },
        _repair_same_date_qlib_gap=lambda *_args, **_kwargs: {
            "status": "success", "verified_no_gap": True,
            "canonical_exclusions": [], "canonical_exclusion_count": 0,
        },
        _readiness_check=lambda *_args, **_kwargs: health,
        _previous_open_session=lambda *_args: "20260820",
        _write_audit=lambda *_args, **_kwargs: None,
        _notify_telegram=lambda *_args, **_kwargs: None,
    ), patch.object(
        daily_sync.cfg, "get_path", return_value=str(tmp_path)
    ), patch(
        "qsys.ops.pit_universe_snapshot.resolve_csi1800_pit_snapshot",
        return_value=snapshot,
    ), patch(
        "qsys.ops.pit_universe_snapshot.write_current_qlib_registry",
        return_value={"status": "success"},
    ), patch.object(
        daily_sync.SourceAuditStore,
        "evaluate_field_receipts",
        return_value={"status": "success", "fields": {}},
    ):
        daily_sync._main_under_writer_lock(inherited)

    assert captured["codes"] == ["000001.SZ", "000002.SZ"]
    assert captured["resume_proof"]["resume_from_run_id"] == old_run
    assert captured["scope_key"] == "csi1800"
    assert captured["universe"] == "csi1800"
    events = audit.run_evidence_summary(current_run)["events"]
    assert any(event["event_type"] == "resume_from_run_validated" for event in events)


def test_wrapper_finalizes_only_after_outer_readiness_event(tmp_path: Path):
    audit = SourceAuditStore(tmp_path / "audit" / "audit.db")
    run_id = "wrapper-finalize"
    receipt_root = tmp_path / "audit" / "source_runs"
    gates = {
        "fetch": True, "raw_payloads": True, "canonical_commit": True,
        "qlib_readback": True, "readiness": True, "contiguous_range": True,
    }
    direct_run_id = "direct-inner-run"
    audit.append_event(direct_run_id, "run_started", {"entrypoint": "inner"})
    direct = audit.finalize_run(
        run_id=direct_run_id,
        source="tushare",
        scope_key="csi1800",
        range_start="20260820",
        range_end="20260820",
        fields=["open", "high", "low", "close", "volume", "factor"],
        gates=gates,
        receipt_root=receipt_root,
        trust_state="trusted",
    )
    assert direct["trust_state"] == "trusted"
    audit.append_event(run_id, "run_started", {"entrypoint": "inner"})
    _publish_wrapper_terminal_gates(
        audit_store=audit,
        run_id=run_id,
        payload={
            "mode": "advance", "gates": gates, "prior_trusted": False,
            "source": "tushare", "scope_key": "csi1800",
            "range_start": "20260821", "range_end": "20260821",
            "fields": ["open", "high", "low", "close", "volume", "factor"],
            "previous_open_session": "20260820",
            "allow_initial_history": True,
        },
    )
    assert not (receipt_root / run_id / "receipt.json").exists()

    result = data_sync._finalize_wrapper_evidence(
        audit_store=audit,
        run_id=run_id,
        receipt_root=receipt_root,
        final_readiness_ok=True,
        verified_outer_fields=("ann_date", "holder_num", "hold_ratio"),
    )
    assert result["trust_state"] == "trusted"
    receipt_path = Path(result["receipt_path"])
    receipt = json.loads(receipt_path.read_text())
    assert receipt["audit_journal"][-1]["event_type"] == "outer_readiness"
    outer_receipt_sha256 = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    with sqlite3.connect(tmp_path / "audit" / "audit.db") as connection:
        watermark_lineage = connection.execute(
            """SELECT DISTINCT run_id,terminal_receipt_sha256
               FROM trusted_watermarks
               WHERE source='tushare' AND scope_key='csi1800'"""
        ).fetchall()
        watermark_fields = {
            row[0] for row in connection.execute(
                """SELECT field_name FROM trusted_watermarks
                   WHERE source='tushare' AND scope_key='csi1800'"""
            ).fetchall()
        }
    assert watermark_lineage == [(run_id, outer_receipt_sha256)]
    assert watermark_lineage[0][0] != direct_run_id
    assert {"ann_date", "holder_num", "hold_ratio"}.issubset(watermark_fields)
    assert audit.has_trusted_range(
        source="tushare", scope_key="csi1800",
        range_start="20260821", range_end="20260821",
        fields=["open", "high", "low", "close", "volume", "factor"],
    )


def test_outer_history_mutation_is_not_borrowed_into_target_certificate(tmp_path: Path):
    audit = SourceAuditStore(tmp_path / "audit" / "audit.db")
    audit.append_event("history-run", "run_started", {"entrypoint": "wrapper"})
    with pytest.raises(RuntimeError, match="mutated untrusted core scope"):
        data_sync._block_untrusted_history_mutation(
            audit_store=audit,
            run_id="history-run",
            result={
                "apply": True,
                "canonical_mutated_symbols": ["000002.SZ"],
                "canonical_mutation_range": {
                    "range_start": "2022-08-21", "range_end": "2026-08-21"
                },
                "canonical_mutation_scope_semantics": (
                    "conservative_planned_scope_after_write_started"
                ),
            },
        )
    events = audit.run_evidence_summary("history-run")["events"]
    assert events[-1]["event_type"] == "untrusted_outer_repair_scope"
    assert audit.watermark_snapshot_bytes() == b"[]\n"


def test_qlib_only_history_repair_does_not_block_target_certificate(tmp_path: Path):
    audit = SourceAuditStore(tmp_path / "audit" / "audit.db")
    audit.append_event("qlib-only", "run_started", {"entrypoint": "wrapper"})

    data_sync._validate_universe_history_result(
        audit_store=audit,
        run_id="qlib-only",
        universe="csi1800",
        result={
            "status": "success",
            "apply": True,
            "backfilled_symbols": ["000002.SZ"],
            "canonical_mutated_symbols": [],
            "summary_path": str(tmp_path / "summary.json"),
        },
    )

    assert not any(
        event["event_type"] == "untrusted_outer_repair_scope"
        for event in audit.run_evidence_summary("qlib-only")["events"]
    )


def test_failed_history_after_check_records_scope_before_status_failure(tmp_path: Path):
    audit = SourceAuditStore(tmp_path / "audit" / "audit.db")
    audit.append_event("after-failed", "run_started", {"entrypoint": "wrapper"})
    with pytest.raises(RuntimeError, match="mutated untrusted core scope"):
        data_sync._validate_universe_history_result(
            audit_store=audit,
            run_id="after-failed",
            universe="csi1800",
            result={
                "status": "failed",
                "apply": True,
                "canonical_mutated_symbols": ["000002.SZ"],
                "canonical_mutation_range": {
                    "range_start": "2022-08-21", "range_end": "2026-08-21"
                },
                "canonical_mutation_scope_semantics": (
                    "conservative_planned_scope_after_write_started"
                ),
                "summary_path": str(tmp_path / "summary.json"),
            },
        )
    assert audit.run_evidence_summary("after-failed")["events"][-1][
        "event_type"
    ] == "untrusted_outer_repair_scope"


def test_inner_unexpected_exception_after_run_started_exports_crash_receipt(tmp_path: Path):
    class CrashingAdapter:
        def init_qlib(self):
            raise RuntimeError("unexpected init crash")

    with patch.object(
        sys,
        "argv",
        ["sync", "--apply", "--run-id", "inner-crash", "--target-date", "20260821"],
    ), patch.object(
        daily_sync.cfg, "get_path", return_value=str(tmp_path)
    ), patch.object(
        daily_sync, "_resolve_target_date", return_value="20260821"
    ), patch.object(
        daily_sync, "QlibAdapter", CrashingAdapter
    ), pytest.raises(RuntimeError, match="unexpected init crash"):
        daily_sync.main()

    receipt_path = tmp_path / "audit" / "source_runs" / "inner-crash" / "receipt.json"
    receipt = json.loads(receipt_path.read_text())
    assert receipt["trust_state"] == "untrusted"
    assert receipt["audit_journal"][-1]["event_type"] == "crash"
    audit = SourceAuditStore(tmp_path / "audit" / "audit.db")
    assert audit.watermark_snapshot_bytes() == b"[]\n"


def test_shareholder_history_start_uses_target_and_positive_lookback():
    assert data_sync._shareholder_required_history_start_date(
        "2026-08-21", 1461
    ) == "2022-08-21"
    assert data_sync._shareholder_required_history_start_date(
        "20260821", 1
    ) == "2026-08-20"
    with pytest.raises(ValueError, match="positive"):
        data_sync._shareholder_required_history_start_date("2026-08-21", 0)


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
    with pytest.raises(ValueError, match="invalid run_id"):
        data_sync._data_sync_run_root(data_root, "../escape")


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


def _wrapper_evidence(tmp_path: Path, run_id: str) -> tuple[SourceAuditStore, dict]:
    store = SourceAuditStore(tmp_path / "audit" / "audit.db")
    store.append_event(run_id, "run_started", {"entrypoint": "wrapper"})
    evidence = {
        "store": store,
        "run_id": run_id,
        "universe": "csi1800",
        "target_date": "20260821",
        "receipt_root": tmp_path / "audit" / "source_runs",
    }
    return store, evidence


def test_wrapper_stage_failure_leaves_terminal_receipt_to_outer(tmp_path: Path):
    store, evidence = _wrapper_evidence(tmp_path, "wrapper-stage-fail")
    with pytest.raises(RuntimeError, match="raw_fetch failed"):
        _abort_if_stage_failed(
            {"universe": "csi1800", "target_date": "20260821"},
            stage="raw_fetch",
            summary={"status": "failed", "error": "source timeout"},
            do_apply=True,
            audit_dir=tmp_path / "legacy",
            evidence=evidence,
            outer_owned_terminal=True,
        )
    receipt_root = evidence["receipt_root"]
    assert not list(receipt_root.glob("**/receipt.json"))

    store.record_crash_receipt(
        run_id="wrapper-stage-fail",
        receipt_root=receipt_root,
        entrypoint="scripts/data_sync.py",
        error="child exit 2",
    )
    receipts = list(receipt_root.glob("**/receipt.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text())["trust_state"] == "untrusted"


def test_inherited_inner_exception_does_not_export_crash_receipt(tmp_path: Path):
    store, evidence = _wrapper_evidence(tmp_path, "inherited-inner-fail")

    def fail_after_start(_writer_lock):
        daily_sync._CRASH_EVIDENCE = {
            **evidence,
            "entrypoint": "scripts/ops/sync_csi800_daily.py",
        }
        raise RuntimeError("inner stage failed")

    with data_writer_lock(tmp_path) as outer_lock, patch.dict(
        os.environ,
        {"QSYS_DATA_WRITER_LOCK_FD": str(outer_lock.fileno())},
    ), patch.object(
        daily_sync.cfg, "get_path", return_value=str(tmp_path)
    ), patch.object(
        daily_sync, "_main_under_writer_lock", side_effect=fail_after_start
    ), pytest.raises(RuntimeError, match="inner stage failed"):
        daily_sync.main()

    receipt_root = evidence["receipt_root"]
    assert not list(receipt_root.glob("**/receipt.json"))
    store.record_crash_receipt(
        run_id="inherited-inner-fail",
        receipt_root=receipt_root,
        entrypoint="scripts/data_sync.py",
        error="child exit 2",
    )
    assert len(list(receipt_root.glob("**/receipt.json"))) == 1


def test_wrapper_gate_failure_leaves_terminal_receipt_to_outer(tmp_path: Path):
    store, evidence = _wrapper_evidence(tmp_path, "wrapper-gate-fail")
    gates = {
        "fetch": True,
        "raw_payloads": True,
        "canonical_commit": True,
        "qlib_readback": True,
        "readiness": False,
        "contiguous_range": True,
    }
    with pytest.raises(SystemExit) as stopped:
        _publish_wrapper_terminal_gates(
            audit_store=store,
            run_id="wrapper-gate-fail",
            payload={"mode": "advance", "prior_trusted": False, "gates": gates},
        )
    assert stopped.value.code == 2
    receipt_root = evidence["receipt_root"]
    assert not list(receipt_root.glob("**/receipt.json"))

    store.record_crash_receipt(
        run_id="wrapper-gate-fail",
        receipt_root=receipt_root,
        entrypoint="scripts/data_sync.py",
        error="child exit 2",
    )
    receipts = list(receipt_root.glob("**/receipt.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text())["trust_state"] == "untrusted"


def test_same_date_canonical_gap_is_repaired_and_verified():
    target = "20260821"

    class Store:
        def __init__(self):
            self.frames = {
                "A.SZ": {"trade_date": [target], "close": [10.0]},
                "B.SZ": {"trade_date": [target], "close": [20.0]},
            }

        def load_daily(self, symbol):
            import pandas as pd

            return pd.DataFrame(self.frames.get(symbol, {}))

    class Adapter:
        def __init__(self):
            import pandas as pd

            self.frames = [
                pd.DataFrame(
                    {"$close": [20.0]},
                    index=pd.MultiIndex.from_tuples(
                        [("2026-08-21", "B.SZ")], names=["datetime", "instrument"]
                    ),
                ),
                pd.DataFrame(
                    {"$close": [10.0, 20.0]},
                    index=pd.MultiIndex.from_tuples(
                        [
                            ("2026-08-21", "A.SZ"),
                            ("2026-08-21", "B.SZ"),
                        ],
                        names=["datetime", "instrument"],
                    ),
                ),
            ]
            self.repaired = []
            self.feature_calls = []

        def get_features(self, *args, **kwargs):
            self.feature_calls.append((args, kwargs))
            return self.frames.pop(0)

        def convert_fix_symbols(self, symbols, **kwargs):
            self.repaired.append((symbols, kwargs))
            return {"status": "success"}

    adapter = Adapter()
    summary = _repair_same_date_qlib_gap(
        adapter,
        Store(),
        ["A.SZ", "B.SZ"],
        universe="csi800",
        target_dt=target,
        apply=True,
    )

    assert adapter.feature_calls[0][0][0] == ["A.SZ", "B.SZ"]
    assert adapter.feature_calls[1][0][0] == ["A.SZ", "B.SZ"]
    assert adapter.repaired == [(["A.SZ"], {"refresh_universes": []})]
    assert summary["missing_symbols"] == ["A.SZ"]
    assert summary["residual_symbols"] == []
    assert summary["verified_no_gap"] is True
    assert summary["status"] == "success"


def test_same_date_gap_fails_closed_when_repair_leaves_residual():
    import pandas as pd

    class Store:
        def load_daily(self, symbol):
            return pd.DataFrame({"trade_date": ["20260821"], "close": [1.0]})

    frame = pd.DataFrame(
        {"$close": [1.0]},
        index=pd.MultiIndex.from_tuples(
            [("2026-08-21", "OTHER.SZ")], names=["datetime", "instrument"]
        ),
    )

    class Adapter:
        def __init__(self):
            self.feature_calls = []

        def get_features(self, *_args, **_kwargs):
            self.feature_calls.append((_args, _kwargs))
            return frame

        def convert_fix_symbols(self, symbols, **kwargs):
            assert symbols == ["A.SZ"]
            assert kwargs == {"refresh_universes": []}
            return {"status": "success"}

    adapter = Adapter()
    summary = _repair_same_date_qlib_gap(
        adapter,
        Store(),
        ["A.SZ"],
        universe="csi1800",
        target_dt="20260821",
        apply=True,
    )

    assert summary["status"] == "failed"
    assert summary["verified_no_gap"] is False
    assert summary["residual_symbols"] == ["A.SZ"]


def test_paused_or_suspended_canonical_rows_do_not_trigger_repair():
    import pandas as pd

    target = "20260821"

    class Store:
        def __init__(self):
            self.frames = {
                "PAUSED_EMPTY.SZ": pd.DataFrame(
                    {
                        "trade_date": [target],
                        "open": [float("nan")],
                        "high": [float("nan")],
                        "low": [float("nan")],
                        "close": [float("nan")],
                        "vol": [0.0],
                        "amount": [0.0],
                        "paused": [1],
                    }
                ),
                "PAUSED_CARRY.SZ": pd.DataFrame(
                    {
                        "trade_date": [target],
                        "open": [10.0],
                        "high": [10.0],
                        "low": [10.0],
                        "close": [10.0],
                        "vol": [0.0],
                        "amount": [0.0],
                        "paused": [1],
                    }
                ),
                "SUSPENDED_CARRY.SZ": pd.DataFrame(
                    {
                        "trade_date": [target],
                        "close": [10.0],
                        "is_suspended": ["true"],
                    }
                ),
                "NO_CLOSE.SZ": pd.DataFrame({"trade_date": [target]}),
            }

        def load_daily(self, symbol):
            return self.frames[symbol]

    store = Store()
    symbols = list(store.frames)
    assert _canonical_symbols_with_data_on_date(store, symbols, target) == set()

    class Adapter:
        def get_features(self, requested_symbols, *_args, **_kwargs):
            assert requested_symbols == sorted(symbols)
            return pd.DataFrame({"$close": pd.Series(dtype=float)})

        def convert_fix_symbols(self, *_args, **_kwargs):
            raise AssertionError("suspended rows must not trigger same-date repair")

    summary = _repair_same_date_qlib_gap(
        Adapter(),
        store,
        symbols,
        universe="csi1800",
        target_dt=target,
        apply=True,
    )

    assert summary["status"] == "success"
    assert summary["canonical_symbols_with_data_count"] == 0
    assert summary["canonical_exclusion_count"] == len(symbols)
    assert {item["ts_code"] for item in summary["canonical_exclusions"]} == set(symbols)
    assert summary["missing_symbols"] == []
    assert summary["residual_symbols"] == []
    assert summary["verified_no_gap"] is True


def test_canonical_exclusions_are_audited_with_reasons():
    import pandas as pd

    target = "20260821"

    class Store:
        frames = {
            "PAUSED.SZ": pd.DataFrame(
                {"trade_date": [target], "close": [10.0], "paused": [1]}
            ),
            "SUSPENDED.SZ": pd.DataFrame(
                {"trade_date": [target], "close": [10.0], "is_suspended": ["true"]}
            ),
            "NO_ROW.SZ": pd.DataFrame({"trade_date": ["20260820"], "close": [10.0]}),
            "NO_CLOSE.SZ": pd.DataFrame({"trade_date": [target]}),
            "NULL_CLOSE.SZ": pd.DataFrame({"trade_date": [target], "close": [None]}),
        }

        def load_daily(self, symbol):
            return self.frames[symbol]

    available, exclusions = daily_sync._canonical_symbol_availability_on_date(
        Store(), list(Store.frames), target
    )
    assert available == set()
    assert exclusions == [
        {"ts_code": "NO_CLOSE.SZ", "reasons": ["missing_close_column"]},
        {"ts_code": "NO_ROW.SZ", "reasons": ["missing_target_row"]},
        {"ts_code": "NULL_CLOSE.SZ", "reasons": ["null_close"]},
        {"ts_code": "PAUSED.SZ", "reasons": ["paused"]},
        {"ts_code": "SUSPENDED.SZ", "reasons": ["is_suspended"]},
    ]


def test_main_ignores_stale_qlib_watermark_unless_repair_is_explicit(tmp_path):
    target = "20260821"
    collectors = []
    adapters = []

    class Collector:
        def __init__(self):
            self.daily_calls = []
            self.history_calls = []

        def update_daily(self, *args, **kwargs):
            self.daily_calls.append((args, kwargs))

        def update_universe_history(self, **kwargs):
            self.history_calls.append(kwargs)

    class Adapter:
        def __init__(self):
            self.qlib_dir = tmp_path / "qlib"
            self.incremental_calls = []
            self.fix_calls = []

        def init_qlib(self):
            pass

        def convert_incremental(self, since):
            self.incremental_calls.append(since)

        def convert_fix(self, since):
            self.fix_calls.append(since)

        def _refresh_universe_instruments(self, **_kwargs):
            pass

    class Store:
        pass

    def collector_factory():
        value = Collector()
        collectors.append(value)
        return value

    def adapter_factory():
        value = Adapter()
        adapters.append(value)
        return value

    snapshot = SimpleNamespace(
        instruments=["000001.SZ", "000002.SZ"],
        to_dict=lambda: {"snapshot_semantics": "pit"},
    )
    health = SimpleNamespace(ok=True, blocking_issues=[], warnings=[])
    reports = []
    common = {
        "QlibAdapter": adapter_factory,
        "TushareCollector": collector_factory,
        "StockDataStore": Store,
        "_resolve_target_date": lambda _value: target,
        "_check_stock_data_status": lambda *_args: {
            "have": [], "missing": ["000001.SZ", "000002.SZ"],
            "total": 2, "already_up_to_date": 0, "need_fetch": 2,
        },
        "_update_index_daily": lambda *_args: {},
        "_repair_same_date_qlib_gap": lambda *_args, **_kwargs: {
            "status": "success", "verified_no_gap": True,
            "canonical_exclusions": [], "canonical_exclusion_count": 0,
        },
        "_readiness_check": lambda *_args, **_kwargs: health,
        "_write_audit": lambda _audit_dir, report: reports.append(report),
        "_notify_telegram": lambda *_args, **_kwargs: None,
    }
    pit_path = "qsys.ops.pit_universe_snapshot.resolve_csi1800_pit_snapshot"
    registry_path = "qsys.ops.pit_universe_snapshot.write_current_qlib_registry"
    with patch.multiple(daily_sync, **common), patch.object(
        daily_sync.cfg, "get_path", return_value=str(tmp_path)
    ), patch.object(
        daily_sync,
        "_load_csi1800_research_union",
        return_value=(list(snapshot.instruments), {"snapshot_semantics": "pit_union"}),
    ), patch(pit_path, return_value=snapshot), patch(registry_path, return_value={"status": "success"}), patch.object(
        daily_sync.SourceAuditStore,
        "evaluate_field_receipts",
        return_value={"status": "success", "fields": {}},
    ), patch.object(
        daily_sync.SourceAuditStore,
        "evaluate_history_field_receipts",
        return_value={"status": "success", "fields": {}},
    ), patch.object(
        daily_sync.SourceAuditStore,
        "finalize_run",
        return_value={"status": "trusted", "trust_state": "trusted", "watermark_advanced": True},
    ):
        with patch.object(sys, "argv", ["sync", "--apply", "--universe", "csi1800", "--target-date", target]):
            daily_sync.main()
        with patch.object(
            sys,
            "argv",
            ["sync", "--apply", "--universe", "csi1800", "--target-date", target,
             "--repair-start-date", "20260820"],
        ):
            daily_sync.main()

    assert collectors[0].daily_calls
    assert collectors[0].daily_calls[0][0] == (target,)
    assert collectors[0].history_calls == []
    assert adapters[0].incremental_calls == ["2026-08-21"]
    assert reports[0]["sync_window"] == {
        "mode": "daily_single_day", "start_date": target, "target_date": target
    }
    assert collectors[1].daily_calls == []
    assert collectors[1].history_calls[0]["start_date"] == "20260820"
    assert collectors[1].history_calls[0]["batch_size"] == 50
    assert adapters[1].incremental_calls == ["2026-08-20"]
    assert reports[1]["sync_window"] == {
        "mode": "explicit_historical_repair",
        "start_date": "20260820",
        "target_date": target,
    }
