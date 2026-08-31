from __future__ import annotations

import json
import hashlib
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
import yaml

import scripts.data_sync as data_sync

from qsys.data._merge_helpers import FINANCIAL_AVAILABILITY_CONTRACT
from qsys.data.income_sidecar import (
    INCOME_SOURCE_MODE_AUDITED,
    INCOME_SOURCE_MODE_LEGACY,
    IncomeSidecarError,
    materialize_audited_income_sidecar,
    normalize_income_feature_source,
    validate_income_sidecar_identity,
)
from qsys.data.source_audit import (
    REQUIRED_TERMINAL_GATES,
    SourceAuditStore,
    checkpoint_requested_scope,
    normalized_response_metadata,
    stable_scope_hash,
)


RUN_ID = "income-sidecar-source"
SCOPE_KEY = "csi1800"
START = "20180313"
CUTOFF = "20260821"
FIELDS = (
    "ts_code", "ann_date", "f_ann_date", "end_date", "report_type",
    "comp_type", "end_type", "update_flag", "n_income", "revenue", "oper_cost",
)
TRUSTED_FIELDS = ("ann_date", "end_date", "report_type", "n_income", "revenue", "oper_cost")


def _row(**overrides) -> dict:
    row = {
        "ts_code": "000001.SZ",
        "ann_date": "20260820",
        "f_ann_date": "20260820",
        "end_date": "20260630",
        "report_type": "1",
        "comp_type": "1",
        "end_type": "2",
        "update_flag": "0",
        "n_income": 2.0,
        "revenue": 10.0,
        "oper_cost": 6.0,
    }
    row.update(overrides)
    return row


def _scope(symbol: str, *, variant: str | None = FINANCIAL_AVAILABILITY_CONTRACT) -> dict:
    return checkpoint_requested_scope(
        {
            "date_start": START,
            "date_end": CUTOFF,
            "availability_cutoff": CUTOFF,
            "query_axis": "announcement_date_query_axis",
            "symbol_count": 1,
            "symbols": [symbol],
            "symbols_sha256": stable_scope_hash([symbol]),
        },
        source="tushare",
        endpoint="income",
        contract_version="1",
        scope_key=SCOPE_KEY,
        universe=SCOPE_KEY,
        request_variant=variant,
        request_sha256="a" * 64,
    )


def _trusted_terminal(
    tmp_path: Path,
    *,
    run_id: str = RUN_ID,
    first_frame: pd.DataFrame | None = None,
    variant: str | None = FINANCIAL_AVAILABILITY_CONTRACT,
    linked_fields: tuple[str, ...] = FIELDS,
    history_checkpoint: dict | None = None,
) -> tuple[SourceAuditStore, Path]:
    data_root = tmp_path / "data"
    audit = SourceAuditStore(data_root / "audit" / "audit.db")
    audit.append_event(run_id, "run_started", {
        "entrypoint": "scripts/data_sync.py",
        "universe": SCOPE_KEY,
        "target_date": CUTOFF,
        "range_start": START,
    })
    frames = {
        "000001.SZ": first_frame if first_frame is not None else pd.DataFrame([
            _row(end_date="20260331", end_type="1"),
            _row(
                ann_date="20260820", f_ann_date="20260822",
                end_date="20260630", end_type="2",
            ),
            _row(
                ann_date="20251231", f_ann_date="20251231",
                end_date="20250930", end_type="3", update_flag="1",
            ),
        ]),
        "000002.SZ": pd.DataFrame(columns=FIELDS),
    }
    for symbol, frame in frames.items():
        metadata = normalized_response_metadata(frame)
        status = "empty" if frame.empty else "success"
        receipt_id = audit.record_fetch(
            run_id=run_id,
            source="tushare",
            endpoint="income",
            contract_version="1",
            status=status,
            requested_scope=_scope(symbol, variant=variant),
            returned_rows=len(frame),
            attempt_count=1,
            payload_frame=frame if status == "success" else None,
            **metadata,
        )
        audit.record_field_receipt_links(
            run_id=run_id,
            receipt_id=receipt_id,
            dataset="income_sidecar",
            fields=linked_fields,
        )
    if history_checkpoint is not None:
        audit.append_event(run_id, "history_scope_completed", {
            **history_checkpoint,
            "receipt_ids": audit.fetch_receipt_ids(run_id),
        })
    result = audit.finalize_run(
        run_id=run_id,
        source="tushare",
        scope_key=SCOPE_KEY,
        range_start=START,
        range_end=CUTOFF,
        fields=TRUSTED_FIELDS,
        gates={name: True for name in REQUIRED_TERMINAL_GATES},
        receipt_root=data_root / "audit" / "source_runs",
        allow_initial_history=True,
    )
    assert result["status"] == "trusted"
    return audit, Path(result["receipt_path"])


def _build(tmp_path: Path, receipt: Path, *, source_run_id: str = RUN_ID) -> dict:
    return materialize_audited_income_sidecar(
        terminal_receipt_path=receipt,
        source_run_id=source_run_id,
        scope_key=SCOPE_KEY,
        range_start=START,
        range_end=CUTOFF,
        availability_cutoff=CUTOFF,
        required_history_start=START,
        output_root=tmp_path / "artifacts",
    )


def test_builder_projects_trusted_raw_and_publishes_immutable_identity(
    tmp_path: Path,
) -> None:
    _, receipt = _trusted_terminal(tmp_path)

    first = _build(tmp_path, receipt)
    second = _build(tmp_path, receipt)

    assert first["status"] == "published"
    assert second["status"] == "reused"
    assert first["required_history_start"] == START
    assert first["artifact_id"] == second["artifact_id"]
    sidecar = pd.read_parquet(first["artifact_path"])
    assert sidecar["end_date"].tolist() == ["20260331"]
    assert sidecar["availability_date"].tolist() == ["20260820"]
    assert sidecar["source_run_id"].unique().tolist() == [RUN_ID]
    manifest = json.loads(Path(first["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["scope"]["symbols"] == ["000001.SZ", "000002.SZ"]
    assert manifest["projection"]["excluded_future_rows"] == 1
    assert manifest["projection"]["right_censored_keys"] == 1
    assert manifest["source_evidence"]["terminal_receipt_sha256"]

    identity = validate_income_sidecar_identity(
        artifact_path=first["artifact_path"],
        artifact_sha256=first["artifact_sha256"],
        manifest_path=first["manifest_path"],
        manifest_sha256=first["manifest_sha256"],
        required_start=START,
        required_end=CUTOFF,
        required_history_start=START,
        required_symbols={"000001.SZ", "000002.SZ"},
    )
    assert identity["manifest"]["artifact_id"] == first["artifact_id"]


def test_builder_preserves_orderable_income_revision_events(tmp_path: Path) -> None:
    _, receipt = _trusted_terminal(
        tmp_path,
        first_frame=pd.DataFrame([
            _row(
                ann_date="20260818", f_ann_date="20260818",
                update_flag="0", revenue=10.0,
            ),
            _row(
                ann_date="20260820", f_ann_date="20260820",
                update_flag="1", revenue=11.0,
            ),
        ]),
    )

    result = _build(tmp_path, receipt)
    sidecar = pd.read_parquet(result["artifact_path"])
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))

    assert sidecar["availability_date"].tolist() == ["20260818", "20260820"]
    assert sidecar["revenue"].tolist() == [10.0, 11.0]
    assert sidecar["event_kind"].tolist() == [
        "INITIAL_PUBLICATION", "REVISION_PUBLICATION",
    ]
    assert manifest["projection"]["proven_revision_events"] == 1


def test_builder_projects_income_from_explicit_inherited_history_scope(
    tmp_path: Path,
) -> None:
    checkpoint = {
        "scope_id": "scope-1",
        "canonical_scope_sha256": "b" * 64,
        "receipt_ids": [],
    }
    audit, source_receipt = _trusted_terminal(
        tmp_path,
        run_id="income-history-source",
        history_checkpoint=checkpoint,
    )
    source = json.loads(source_receipt.read_text(encoding="utf-8"))
    checkpoint = next(
        event["payload"] for event in source["audit_journal"]
        if event["event_type"] == "history_scope_completed"
    )
    source_sha = hashlib.sha256(source_receipt.read_bytes()).hexdigest()

    certifying_run = "income-history-certifier"
    audit.append_event(certifying_run, "run_started", {
        "entrypoint": "scripts/data_sync.py",
        "universe": SCOPE_KEY,
        "target_date": CUTOFF,
        "range_start": START,
    })
    audit.record_history_scope_inherited(
        run_id=certifying_run,
        checkpoint={
            **checkpoint,
            "source_run_id": "income-history-source",
            "source_receipt_sha256": source_sha,
        },
    )
    certified = audit.finalize_run(
        run_id=certifying_run,
        source="tushare",
        scope_key=SCOPE_KEY,
        range_start=START,
        range_end=CUTOFF,
        fields=TRUSTED_FIELDS,
        gates={name: True for name in REQUIRED_TERMINAL_GATES},
        receipt_root=tmp_path / "data" / "audit" / "source_runs",
        allow_initial_history=True,
    )

    result = _build(
        tmp_path, Path(certified["receipt_path"]), source_run_id=certifying_run,
    )
    sidecar = pd.read_parquet(result["artifact_path"])
    assert sidecar["source_run_id"].unique().tolist() == ["income-history-source"]
    assert sidecar["certifying_run_id"].unique().tolist() == [certifying_run]
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["source_evidence"]["run_id"] == certifying_run
    assert {
        row["evidence_run_id"] for row in manifest["source_evidence"]["receipts"]
    } == {"income-history-source"}


def test_builder_uses_scope_bound_symbol_without_ts_code_field_link(
    tmp_path: Path,
) -> None:
    _, receipt = _trusted_terminal(
        tmp_path,
        linked_fields=tuple(field for field in FIELDS if field != "ts_code"),
    )

    result = _build(tmp_path, receipt)

    assert result["status"] == "published"


def test_builder_rejects_untrusted_terminal(tmp_path: Path) -> None:
    _, receipt = _trusted_terminal(tmp_path)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["trust_state"] = "untrusted"
    receipt.unlink()
    receipt.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(IncomeSidecarError, match="not trusted"):
        _build(tmp_path, receipt)


def test_builder_rejects_terminal_without_watermark_backlink(tmp_path: Path) -> None:
    _, receipt = _trusted_terminal(tmp_path)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["exported_at"] = "2099-01-01T00:00:00+00:00"
    receipt.unlink()
    receipt.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(IncomeSidecarError, match="watermark backlink"):
        _build(tmp_path, receipt)


def test_builder_rejects_incomplete_income_field_links(tmp_path: Path) -> None:
    _, receipt = _trusted_terminal(
        tmp_path, linked_fields=tuple(field for field in FIELDS if field != "f_ann_date")
    )

    with pytest.raises(IncomeSidecarError, match="field links are incomplete"):
        _build(tmp_path, receipt)


def test_builder_rejects_tampered_payload(tmp_path: Path) -> None:
    _, receipt = _trusted_terminal(tmp_path)
    terminal = json.loads(receipt.read_text(encoding="utf-8"))
    success = next(
        row for row in terminal["fetch_receipts"]
        if row["endpoint"] == "income" and row["status"] == "success"
    )
    data_root = receipt.parents[3]
    payload = data_root / success["payload_path"]
    payload.write_bytes(payload.read_bytes() + b"tampered")

    with pytest.raises(IncomeSidecarError, match="sha256 mismatch"):
        _build(tmp_path, receipt)


def test_builder_rejects_legacy_contract_receipt(tmp_path: Path) -> None:
    _, receipt = _trusted_terminal(tmp_path, variant=None)

    with pytest.raises(IncomeSidecarError, match="current-contract"):
        _build(tmp_path, receipt)


def test_identity_rejects_hash_tamper_and_scope_mismatch(tmp_path: Path) -> None:
    _, receipt = _trusted_terminal(tmp_path)
    result = _build(tmp_path, receipt)

    with pytest.raises(IncomeSidecarError, match="artifact sha256 mismatch"):
        validate_income_sidecar_identity(
            artifact_path=result["artifact_path"],
            artifact_sha256="0" * 64,
            manifest_path=result["manifest_path"],
            manifest_sha256=result["manifest_sha256"],
        )
    with pytest.raises(IncomeSidecarError, match="required end"):
        validate_income_sidecar_identity(
            artifact_path=result["artifact_path"],
            artifact_sha256=result["artifact_sha256"],
            manifest_path=result["manifest_path"],
            manifest_sha256=result["manifest_sha256"],
            required_end="20260822",
        )
    with pytest.raises(IncomeSidecarError, match="required symbols"):
        validate_income_sidecar_identity(
            artifact_path=result["artifact_path"],
            artifact_sha256=result["artifact_sha256"],
            manifest_path=result["manifest_path"],
            manifest_sha256=result["manifest_sha256"],
            required_symbols={"999999.SZ"},
        )
    with pytest.raises(IncomeSidecarError, match="required history scope"):
        validate_income_sidecar_identity(
            artifact_path=result["artifact_path"],
            artifact_sha256=result["artifact_sha256"],
            manifest_path=result["manifest_path"],
            manifest_sha256=result["manifest_sha256"],
            required_history_start="20170313",
        )

    forged_manifest = json.loads(
        Path(result["manifest_path"]).read_text(encoding="utf-8")
    )
    forged_manifest.pop("identity")
    forged_path = tmp_path / "forged-manifest.json"
    forged_path.write_text(json.dumps(forged_manifest), encoding="utf-8")
    with pytest.raises(IncomeSidecarError, match="contract/identity mismatch"):
        validate_income_sidecar_identity(
            artifact_path=result["artifact_path"],
            artifact_sha256=result["artifact_sha256"],
            manifest_path=forged_path,
            manifest_sha256=hashlib.sha256(forged_path.read_bytes()).hexdigest(),
        )


def test_same_identity_existing_bytes_must_match(tmp_path: Path) -> None:
    _, receipt = _trusted_terminal(tmp_path)
    result = _build(tmp_path, receipt)
    manifest_path = Path(result["manifest_path"])
    manifest_path.write_bytes(manifest_path.read_bytes() + b" ")

    with pytest.raises(IncomeSidecarError, match="byte-identical"):
        _build(tmp_path, receipt)


def test_data_sync_explicit_bootstrap_mode_calls_real_offline_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _, receipt = _trusted_terminal(tmp_path)
    data_root = receipt.parents[3]
    from qsys.config import cfg

    original_get_path = cfg.get_path
    monkeypatch.setattr(
        cfg,
        "get_path",
        lambda name: str(data_root) if name == "root" else original_get_path(name),
    )
    monkeypatch.setattr(
        data_sync.sys,
        "argv",
        [
            "scripts/data_sync.py",
            "--apply",
            "--build-income-sidecar-from-run-id", RUN_ID,
            "--income-sidecar-output-root", "research/source_snapshots/income",
            "--income-sidecar-scope-key", SCOPE_KEY,
            "--income-sidecar-range-start", START,
            "--income-sidecar-cutoff", CUTOFF,
            "--income-sidecar-required-history-start", START,
        ],
    )

    data_sync._main_under_writer_lock(object())

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "published"
    assert Path(result["artifact_path"]).is_file()
    assert data_root in Path(result["artifact_path"]).parents


def test_normal_daily_never_invokes_income_sidecar_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        data_sync.sys,
        "argv",
        [
            "scripts/data_sync.py",
            "--universe", "csi1800",
            "--target-date", "2026-08-21",
        ],
    )
    with patch(
        "qsys.data.income_sidecar.materialize_audited_income_sidecar"
    ) as materialize, patch.object(data_sync.subprocess, "run") as child:
        data_sync.main()

    materialize.assert_not_called()
    child.assert_called_once()


@pytest.mark.parametrize(
    "missing",
    ["artifact_path", "artifact_sha256", "manifest_path", "manifest_sha256"],
)
def test_audited_source_contract_requires_complete_identity(missing: str) -> None:
    value = {
        "mode": INCOME_SOURCE_MODE_AUDITED,
        "artifact_path": "income.parquet",
        "artifact_sha256": "a" * 64,
        "manifest_path": "manifest.json",
        "manifest_sha256": "b" * 64,
        "required_history_start": START,
    }
    value.pop(missing)

    with pytest.raises(ValueError, match="missing"):
        normalize_income_feature_source(value)


def test_income_source_modes_keep_history_scope_explicit() -> None:
    with pytest.raises(ValueError, match="required_history_start"):
        normalize_income_feature_source({
            "mode": INCOME_SOURCE_MODE_AUDITED,
            "artifact_path": "income.parquet",
            "artifact_sha256": "a" * 64,
            "manifest_path": "manifest.json",
            "manifest_sha256": "b" * 64,
        })
    assert normalize_income_feature_source(None)["mode"] == INCOME_SOURCE_MODE_LEGACY
    with pytest.raises(ValueError, match="cannot carry audited identity"):
        normalize_income_feature_source({
            "mode": INCOME_SOURCE_MODE_LEGACY,
            "artifact_path": "income.parquet",
        })


def test_audited_source_accepts_portable_content_identity() -> None:
    portable = normalize_income_feature_source({
        "mode": INCOME_SOURCE_MODE_AUDITED,
        "artifact_id": "c" * 64,
        "artifact_sha256": "a" * 64,
        "manifest_sha256": "b" * 64,
        "required_history_start": START,
    })

    assert portable["artifact_id"] == "c" * 64
    assert portable["artifact_path"] == ""
    assert portable["manifest_path"] == ""
    with pytest.raises(ValueError, match="cannot mix"):
        normalize_income_feature_source({
            **portable,
            "artifact_path": "income.parquet",
        })


def test_formal_strategy_configs_declare_unverified_compatibility_mode() -> None:
    project_root = Path(__file__).resolve().parents[1]
    for name in ("financial_rc.yaml", "s180_top10.yaml"):
        config = yaml.safe_load(
            (project_root / "configs" / "strategies" / name).read_text(
                encoding="utf-8"
            )
        )
        source = normalize_income_feature_source(config["income_feature_source"])
        assert source["mode"] == INCOME_SOURCE_MODE_LEGACY
