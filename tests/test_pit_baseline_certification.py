from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pandas as pd
import pytest
import yaml

import qsys.pit_certification as pit_certification

from qsys.pit_certification import (
    CertificationError,
    _canonical_bytes,
    _canonical_materialization_identity,
    _checkpoint_set_payload,
    _proofs_cover_scope,
    _scope_rows,
    _sha256_bytes,
    certify_pit_baseline,
    classify_mutation_intersection,
    iter_canonical_mutations,
    load_checkpoint_scope,
    load_universe_spans,
    sha256_file,
    stable_scope_hash,
    validate_feature_dependencies,
)
from qsys.pit_datapack import (
    _corporate_action_files,
    export_certified_datapack,
    verify_datapack,
)


ROOT = Path(__file__).resolve().parents[1]
REAL_DEPENDENCIES = ROOT / "configs/audit/feature_dependencies/v3a_plus_liquidity_financial_rc_v1.yaml"
REAL_SOURCE_CONTRACT_PATH = ROOT / "docs/requirements/contracts/tushare_daily.yaml"
REAL_R2_RESEARCH_CONFIG = (
    ROOT
    / "configs/research/60d/financial_rc_180d_rolling_5y_to_202607_v3_pit_csi1800_terminal_r2.yaml"
)
REAL_R3_RESEARCH_CONFIG = (
    ROOT
    / "configs/research/60d/financial_rc_180d_rolling_5y_to_202607_v3_pit_csi1800_terminal_r3.yaml"
)
REAL_REQUEST = yaml.safe_load(
    (ROOT / "configs/audit/csi1800_s180_baseline_v1_r1.yaml").read_text(encoding="utf-8")
)


def test_current_tushare_source_contract_digest_is_frozen() -> None:
    assert sha256_file(REAL_SOURCE_CONTRACT_PATH) == (
        "afea082e35568323907f0f161b6c90ea9ab5636c2bbb6366e0cad54db27b1c7e"
    )


def test_r3_research_semantics_differ_only_by_data_and_run_identity() -> None:
    left = yaml.safe_load(REAL_R2_RESEARCH_CONFIG.read_text(encoding="utf-8"))
    right = yaml.safe_load(REAL_R3_RESEARCH_CONFIG.read_text(encoding="utf-8"))
    differences: set[str] = set()

    def compare(first, second, path: str = "") -> None:
        if isinstance(first, dict) and isinstance(second, dict):
            assert first.keys() == second.keys()
            for key in first:
                compare(first[key], second[key], f"{path}/{key}")
            return
        if isinstance(first, list) and isinstance(second, list):
            assert len(first) == len(second)
            for index, (first_item, second_item) in enumerate(zip(first, second)):
                compare(first_item, second_item, f"{path}/{index}")
            return
        if first != second:
            differences.add(path)

    compare(left, right)
    assert differences == {
        "/experiment_id",
        "/source_manifest_hash",
        "/signal/signal_id",
        "/generators/0/generator_id",
        "/generators/0/params/shareholder_holder_path",
        "/generators/0/params/shareholder_holder_sha256",
        "/generators/0/params/shareholder_top10_path",
        "/generators/0/params/shareholder_top10_sha256",
        "/generators/0/params/shareholder_manifest_path",
        "/generators/0/params/shareholder_manifest_sha256",
    }


def test_receipt_shards_must_cover_the_instrument_interval_without_gap() -> None:
    def proof(left: str, right: str, symbols: list[str]) -> dict:
        return {"receipt": {
            "receipt_id": f"{left}:{right}:{','.join(symbols)}",
            "requested_scope": {
                "date_start": left, "date_end": right, "symbols": symbols,
            },
        }}

    scope = {
        "instrument": "000001.SZ", "date_start": "20200101", "date_end": "20201231",
    }
    proofs = [
        proof("20200101", "20200630", ["000001.SZ"]),
        proof("20200630", "20201231", ["000001.SZ"]),
        proof("20200101", "20201231", ["000002.SZ"]),
    ]
    covered = _proofs_cover_scope(proofs, scope)
    assert len(covered) == 2
    cache = {
        item["receipt"]["receipt_id"]: frozenset(
            item["receipt"]["requested_scope"]["symbols"]
        )
        for item in proofs
    }
    assert _proofs_cover_scope(
        proofs, scope, requested_symbols_cache=cache,
    ) == covered
    assert _proofs_cover_scope([
        proof("20200101", "20200629", ["000001.SZ"]),
        proof("20200701", "20201231", ["000001.SZ"]),
    ], scope) == []


def test_inherited_raw_evidence_requires_exact_terminal_event_and_qlib_readback(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    audit_root = data_root / "audit"
    database = _write(audit_root / "audit.db", b"sqlite-placeholder")
    raw = _write(data_root / "raw/source.parquet", b"raw")
    requested_scope = {
        "date_start": "20200101", "date_end": "20200131",
        "symbol_count": 1, "symbols": ["000001.SZ"],
        "symbols_sha256": stable_scope_hash(["000001.SZ"]),
    }
    raw_receipt = {
        "receipt_id": "raw-1", "run_id": "source-run", "source": "tushare",
        "endpoint": "daily", "status": "success", "requested_scope": requested_scope,
        "returned_rows": 1, "response_hash": "a" * 64,
        "response_columns": ["ts_code", "trade_date", "close"],
        "response_date_min": "20200101", "response_date_max": "20200131",
        "payload_kind": "raw_supplier", "payload_path": "raw/source.parquet",
        "payload_sha256": sha256_file(raw),
    }
    source_terminal = {
        "schema_version": 1, "run_id": "source-run", "trust_state": "untrusted",
        "terminal_gates": {name: False for name in pit_certification.REQUIRED_TERMINAL_GATES},
        "fetch_receipts": [raw_receipt],
        "field_receipt_links": [{
            "run_id": "source-run", "dataset": "canonical_daily",
            "field_name": "close", "receipt_id": "raw-1",
        }],
        "audit_journal": [],
    }
    source_path = _write(
        audit_root / "source_runs/source-run/receipt.json",
        json.dumps(source_terminal, sort_keys=True) + "\n",
    )
    readback = _write(
        audit_root / "source_runs/trusted-run/qlib_readback.json", b"verified",
    )
    terminal = {
        "schema_version": 1, "run_id": "trusted-run", "trust_state": "trusted",
        "terminal_gates": {name: True for name in pit_certification.REQUIRED_TERMINAL_GATES},
        "fetch_receipts": [], "field_receipt_links": [],
        "audit_journal": [
            {
                "event_type": "history_scope_inherited",
                "payload": {
                    "source": "tushare", "scope_key": "tiny",
                    "source_run_id": "source-run",
                    "source_receipt_sha256": sha256_file(source_path),
                    "receipt_ids": ["raw-1"],
                    "range_start": "20200101", "range_end": "20200131",
                    "symbol_count": 1,
                    "symbols_sha256": stable_scope_hash(["000001.SZ"]),
                    "canonical_semantic_contract": "bounded_canonical_values_v1",
                    "canonical_semantic_fields": ["close"],
                },
            },
            {
                "event_type": "qlib_readback",
                "payload": {
                    "status": "success", "mismatch_count": 0,
                    "verified_fields": ["$close"],
                    "artifact_path": "audit/source_runs/trusted-run/qlib_readback.json",
                    "artifact_sha256": sha256_file(readback),
                },
            },
        ],
    }
    terminal_path = _write(
        audit_root / "source_runs/trusted-run/receipt.json",
        json.dumps(terminal, sort_keys=True) + "\n",
    )
    watermark = {
        "run_id": "trusted-run", "source": "tushare", "field_name": "close",
        "scope_key": "tiny", "range_start": "20200101", "trusted_through": "20200131",
        "terminal_receipt_sha256": sha256_file(terminal_path),
    }
    link = source_terminal["field_receipt_links"][0]
    receipt = {**raw_receipt, "payload_verified": True}
    scope = {
        "source": "tushare", "dataset": "canonical_daily", "endpoint": "daily",
        "field": "close", "instrument": "000001.SZ",
        "date_start": "20200101", "date_end": "20200131",
    }

    assert pit_certification._inherited_terminal_proof_valid(
        audit_db=database, watermark=watermark, receipt=receipt, link=link,
        scope=scope, consumed_instruments=frozenset({"000001.SZ"}),
        terminal_cache={}, inherited_cache={}, required_dataset="canonical_daily",
    )
    readback.write_bytes(b"tampered")
    assert not pit_certification._inherited_terminal_proof_valid(
        audit_db=database, watermark=watermark, receipt=receipt, link=link,
        scope=scope, consumed_instruments=frozenset({"000001.SZ"}),
        terminal_cache={}, inherited_cache={}, required_dataset="canonical_daily",
    )


def test_terminal_proof_accepts_hashed_empty_supplier_response(tmp_path: Path) -> None:
    audit_db = _write(tmp_path / "data/audit/audit.db", b"sqlite-placeholder")
    symbols = ["000001.SZ"]
    requested_scope = {
        "date_start": "20200115", "date_end": "20200115",
        "symbol_count": 1, "symbols": symbols,
        "symbols_sha256": stable_scope_hash(symbols),
    }
    receipt = {
        "receipt_id": "empty-1", "run_id": "trusted-run", "source": "tushare",
        "endpoint": "top10_holders", "status": "empty",
        "requested_scope": requested_scope, "returned_rows": 0,
        "response_hash": "a" * 64, "response_columns": ["ann_date", "hold_ratio"],
        "response_date_min": None, "response_date_max": None,
        "payload_kind": "raw_supplier", "payload_path": None, "payload_sha256": None,
    }
    link = {
        "run_id": "trusted-run", "dataset": "shareholder_top10",
        "field_name": "hold_ratio", "receipt_id": "empty-1",
    }
    terminal = {
        "schema_version": 1, "run_id": "trusted-run", "trust_state": "trusted",
        "terminal_gates": {name: True for name in pit_certification.REQUIRED_TERMINAL_GATES},
        "fetch_receipts": [receipt], "field_receipt_links": [link],
    }
    terminal_path = _write(
        audit_db.parent / "source_runs/trusted-run/receipt.json",
        json.dumps(terminal, sort_keys=True) + "\n",
    )
    watermark = {
        "run_id": "trusted-run", "terminal_receipt_sha256": sha256_file(terminal_path),
    }

    assert pit_certification._terminal_proof_valid(
        audit_db=audit_db, watermark=watermark, receipt=receipt, link=link,
        scope_start="20200115", scope_end="20200115", consumed_instruments=symbols,
    )
    assert not pit_certification._terminal_proof_valid(
        audit_db=audit_db, watermark=watermark,
        receipt={**receipt, "returned_rows": 1}, link=link,
        scope_start="20200115", scope_end="20200115", consumed_instruments=symbols,
    )


def test_projected_raw_receipts_use_their_declared_query_axis() -> None:
    receipt = {
        "endpoint": "daily", "status": "success", "returned_rows": 2,
        "response_hash": "a" * 64, "response_columns": ["date", "value"],
        "response_date_min": "20191231", "response_date_max": "20200201",
        "payload_kind": "raw_supplier", "payload_verified": True,
        "requested_scope": {"date_start": "20200101", "date_end": "20200131"},
    }
    assert not pit_certification._raw_supplier_receipt_valid(receipt)

    industry = copy.deepcopy(receipt)
    industry["endpoint"] = "bak_basic"
    industry["requested_scope"].update({
        "query_axis": "all_history", "availability_cutoff": "20200131",
        "request_variant": "history_bak_basic_industry_v1",
    })
    assert pit_certification._raw_supplier_receipt_valid(industry)
    industry["requested_scope"]["query_axis"] = "trade_date_market_snapshot"
    assert not pit_certification._raw_supplier_receipt_valid(industry)

    financial = copy.deepcopy(receipt)
    financial["endpoint"] = "income"
    financial["requested_scope"].update({
        "query_axis": "announcement_date_query_axis",
        "availability_cutoff": "20200131",
        "request_variant": "financial_first_available_v1",
    })
    assert pit_certification._raw_supplier_receipt_valid(financial)
    financial["endpoint"] = "fina_indicator"
    assert not pit_certification._raw_supplier_receipt_valid(financial)
    financial["requested_scope"]["query_axis"] = "report_period_query_axis"
    assert pit_certification._raw_supplier_receipt_valid(financial)


def _write(path: Path, value: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(value, encoding="utf-8")
    return path


def _identity(project: Path, relative: str) -> dict[str, str]:
    path = project / relative
    return {"path": relative, "sha256": sha256_file(path)}


def _refresh_request_identities(project: Path, request_path: Path, *names: str) -> None:
    request = yaml.safe_load(request_path.read_text(encoding="utf-8"))
    for name in names:
        relative = request["identities"][name]["path"]
        request["identities"][name]["sha256"] = sha256_file(project / relative)
    request_path.write_text(yaml.safe_dump(request, sort_keys=False), encoding="utf-8")


def _make_db(path: Path, *, evidence: bool = True, mutation: dict | None = None) -> None:
    payload = _write(path.parent / "raw.parquet", b"supplier response")
    requested_scope = {
        "date_start": "20200101", "date_end": "20200131", "symbol_count": 1,
        "symbols_sha256": stable_scope_hash(["000001.SZ"]),
    }
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            PRAGMA user_version=1;
            CREATE TABLE fetch_receipts(
              receipt_id TEXT PRIMARY KEY,run_id TEXT,source TEXT,endpoint TEXT,status TEXT,
              requested_scope_json TEXT,returned_rows INTEGER,response_hash TEXT,
              response_columns_json TEXT,response_date_min TEXT,response_date_max TEXT,
              attempt_count INTEGER,payload_kind TEXT,payload_path TEXT,payload_sha256 TEXT,
              published_at TEXT,observed_at TEXT,error_json TEXT);
            CREATE TABLE field_receipt_links(run_id TEXT,dataset TEXT,field_name TEXT,receipt_id TEXT);
            CREATE TABLE canonical_mutations(
              mutation_id TEXT PRIMARY KEY,run_id TEXT,dataset TEXT,source TEXT,endpoint TEXT,
              fetch_receipt_id TEXT,symbol TEXT,date_start TEXT,date_end TEXT,fields_json TEXT,
              mutation_type TEXT,before_hash TEXT,after_hash TEXT,ingested_at TEXT);
            CREATE TABLE trusted_watermarks(
              source TEXT,field_name TEXT,scope_key TEXT,range_start TEXT,trusted_through TEXT,
              run_id TEXT,terminal_receipt_sha256 TEXT,updated_at TEXT);
            CREATE TABLE audit_journal(run_id TEXT,seq INTEGER,event_type TEXT,payload_json TEXT,created_at TEXT);
            """
        )
        if evidence:
            conn.execute(
                "INSERT INTO fetch_receipts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("receipt-1", "evidence-1", "tushare", "daily", "success",
                 json.dumps(requested_scope, sort_keys=True), 1,
                 "a" * 64, '["ts_code","trade_date","close"]', "20200101", "20200131",
                 1, "raw_supplier", "raw.parquet", sha256_file(payload), None,
                 "2020-02-01T00:00:00Z", None),
            )
            conn.execute(
                "INSERT INTO fetch_receipts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("derived-1", "evidence-1", "local", "daily_bundle", "success",
                 json.dumps(requested_scope, sort_keys=True), 1,
                 "b" * 64, '["ts_code","trade_date","close"]', "20200101", "20200131",
                 1, "derived", None, None, None, "2020-02-01T00:00:00Z", None),
            )
            conn.execute(
                "INSERT INTO field_receipt_links VALUES(?,?,?,?)",
                ("evidence-1", "canonical_daily", "close", "receipt-1"),
            )
            conn.execute(
                "INSERT INTO trusted_watermarks VALUES(?,?,?,?,?,?,?,?)",
                ("tushare", "close", "tiny", "20200101", "20200131", "evidence-1", "pending",
                 "2020-02-01T00:00:00Z"),
            )
            conn.execute("INSERT INTO audit_journal VALUES(?,?,?,?,?)", ("evidence-1", 1, "terminal", "{}", "x"))
        if mutation:
            conn.execute(
                "INSERT INTO canonical_mutations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (mutation["mutation_id"], mutation["run_id"], mutation.get("dataset"),
                 mutation.get("source"), mutation.get("endpoint"), None, mutation.get("symbol"),
                 mutation.get("date_start"), mutation.get("date_end"),
                 json.dumps(mutation.get("fields")), mutation.get("mutation_type", "update"),
                 "d" * 64, "e" * 64,
                 mutation.get("ingested_at", "2020-02-01T00:00:00Z")),
            )
    if evidence:
        terminal = {
            "schema_version": 1, "run_id": "evidence-1", "trust_state": "trusted",
            "terminal_gates": {
                "fetch": True, "raw_payloads": True, "canonical_commit": True,
                "qlib_readback": True, "readiness": True, "contiguous_range": True,
            },
            "fetch_receipts": [{
                "receipt_id": "receipt-1", "run_id": "evidence-1", "source": "tushare",
                "endpoint": "daily", "status": "success", "requested_scope": requested_scope,
                "returned_rows": 1, "response_hash": "a" * 64,
                "response_columns": ["ts_code", "trade_date", "close"],
                "response_date_min": "20200101", "response_date_max": "20200131",
                "payload_kind": "raw_supplier", "payload_path": "raw.parquet",
                "payload_sha256": sha256_file(payload),
            }],
            "field_receipt_links": [{
                "run_id": "evidence-1", "dataset": "canonical_daily",
                "field_name": "$close", "receipt_id": "receipt-1",
            }],
        }
        terminal_path = _write(
            path.parent / "source_runs/evidence-1/receipt.json",
            json.dumps(terminal, sort_keys=True) + "\n",
        )
        with sqlite3.connect(path) as conn:
            conn.execute(
                "UPDATE trusted_watermarks SET terminal_receipt_sha256=? WHERE run_id='evidence-1'",
                (sha256_file(terminal_path),),
            )


@pytest.fixture
def tiny_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    research = {"name": "tiny"}
    _write(project / "research.yaml", yaml.safe_dump(research))
    _write(project / "signal.parquet", b"signal predictions")
    _write(project / "registry.txt", b"000001.SZ\n")
    membership = project / "membership.parquet"
    pd.DataFrame([{
        "instrument": "000001.SZ", "effective_from": "20200101", "effective_to": "20200131",
    }]).to_parquet(membership, index=False)
    _write(project / "universe.json", json.dumps({
        "universe_id": "tiny-universe",
        "membership_sha256": sha256_file(membership),
        "registry_sha256": sha256_file(project / "registry.txt"),
    }))
    predictions = _write(project / "checkpoints/w1.parquet", b"prediction")
    manifest = {
        "predictions_file": predictions.name,
        "predictions_sha256": sha256_file(predictions),
        "row_count": 1,
        "identity": {
            "base_identity_sha256": "f" * 64,
            "research_config": research,
            "window": {
                "window_id": "w1", "train_start": "2020-01-01", "train_end": "2020-01-10",
                "predict_start": "2020-01-11", "predict_end": "2020-01-31",
            },
        },
    }
    manifest_path = _write(project / "checkpoints/w1.manifest.json", json.dumps(manifest))
    set_hash = _sha256_bytes(_canonical_bytes({
        "checkpoints": _checkpoint_set_payload([(manifest_path, manifest)])
    }))
    _write(project / "signal.json", json.dumps({
        "signal_id": "tiny-signal", "signal_run_id": "tiny-signal-run",
        "predictions_file": "signal.parquet", "source_manifest_hash": None,
        "predictions_sha256": sha256_file(project / "signal.parquet"),
        "window_checkpoint_set_sha256": set_hash,
        "feature_list_contract": {
            "feature_list_id": "tiny", "feature_count": 1,
            "features_sha256": "tiny-features", "feature_list_config_sha256": "tiny-config",
        },
    }))
    _write(project / "data/canonical/daily/000001.SZ.feather", b"canonical rows")
    events = _write(
        project / "data/research/corporate_actions/tiny-ca/events.parquet",
        b"corporate actions",
    )
    source_bundle = _write(
        project / "data/research/corporate_actions/tiny-ca/source/source_bundle.zip",
        b"raw corporate action evidence",
    )
    corporate_action_manifest = {
        "schema_version": "corporate_actions_v1", "artifact_name": "tiny-ca",
        "events_sha256": sha256_file(events),
        "source_raw_path": "source/source_bundle.zip",
        "source_raw_artifact_sha256": sha256_file(source_bundle),
    }
    _write(
        project / "data/research/corporate_actions/tiny-ca/manifest.json",
        json.dumps(corporate_action_manifest),
    )
    _write(project / "backtest.json", json.dumps({
        "artifacts": {}, "signal_id": "tiny-signal", "signal_run_id": "tiny-signal-run",
        "accounting": {
            "canonical_data_root": "data/canonical/daily",
            "corporate_action_artifact": "tiny-ca",
            "corporate_action_manifest": corporate_action_manifest,
        },
        "signal_sources": [{
            "manifest_sha256": sha256_file(project / "signal.json"),
            "predictions_sha256": sha256_file(project / "signal.parquet"),
            "signal_id": "tiny-signal", "signal_run_id": "tiny-signal-run",
            "source_manifest_hash": None,
        }],
        "pit_execution_universe": {
            "manifest_sha256": sha256_file(project / "universe.json"),
            "membership_sha256": sha256_file(project / "membership.parquet"),
            "universe_id": "tiny-universe", "artifact": "tiny-universe",
        },
    }))
    dependencies = {
        "schema_version": "feature_dependency_assertion_v1", "dependency_version": "tiny_v1",
        "feature_list_id": "tiny", "feature_count": 1, "features_sha256": "tiny-features",
        "lookback_contract": {
            "runtime_max_lookback_trading_sessions": 1, "input_warmup_calendar_days": 0,
        },
        "features": [{
            "feature": "ret_1d", "required": True,
            "dependencies": [{
                "source": "tushare", "dataset": "canonical_daily", "endpoint": "daily",
                "leaf_fields": ["close"], "visibility": "completed_session",
                "pit_status": "evidence_required",
            }],
        }],
    }
    _write(project / "dependencies.yaml", yaml.safe_dump(dependencies, sort_keys=False))
    monkeypatch.setattr(
        "qsys.pit_certification.FeatureListRegistry.contract",
        lambda feature_list_id: {
            "feature_list_id": "tiny", "features": ["ret_1d"], "feature_count": 1,
            "features_sha256": "tiny-features", "feature_list_config_sha256": "tiny-config",
        },
    )
    request = {
        "schema_version": "pit_baseline_request_v1", "baseline_id": "tiny-baseline",
        "scope_key": "tiny", "feature_list_id": "tiny",
        "feature_dependencies_path": "dependencies.yaml",
        "feature_input_scope": {"date_start": "2020-01-01", "date_end": "2020-01-31"},
        "identities": {
            "research_config": _identity(project, "research.yaml"),
            "signal_manifest": _identity(project, "signal.json"),
            "signal_predictions": _identity(project, "signal.parquet"),
            "universe_manifest": _identity(project, "universe.json"),
            "universe_membership": _identity(project, "membership.parquet"),
            "universe_registry": _identity(project, "registry.txt"),
            "backtest_manifest": _identity(project, "backtest.json"),
        },
        "checkpoints": {
            "path": "checkpoints", "checkpoint_count": 1,
            "checkpoint_set_sha256": set_hash, "base_identity_sha256": "f" * 64,
            "model": {"mode": "ephemeral_per_window", "path": None},
        },
    }
    request_path = _write(project / "request.yaml", yaml.safe_dump(request, sort_keys=False))
    database = project / "audit.db"
    _make_db(database)
    return project, request_path, database


def _request_with_frozen_evidence(
    project: Path, request_path: Path, source_result: dict,
) -> Path:
    receipt_path = Path(source_result["receipt_path"])
    snapshot_path = receipt_path.parent / "evidence_snapshot.json"
    request = yaml.safe_load(request_path.read_text(encoding="utf-8"))
    request["baseline_id"] = "tiny-frozen-replay"
    request["frozen_evidence"] = {
        "audit_receipt": _identity(
            project, receipt_path.relative_to(project).as_posix(),
        ),
        "evidence_snapshot": _identity(
            project, snapshot_path.relative_to(project).as_posix(),
        ),
    }
    return _write(
        project / "frozen-request.yaml",
        yaml.safe_dump(request, sort_keys=False),
    )


def _synthetic_consumed_sidecars(
    project: Path, *, terminal_sha256: str,
) -> tuple[dict, dict, dict]:
    from qsys.data._merge_helpers import (
        FINANCIAL_AVAILABILITY_CONTRACT,
        FINANCIAL_AVAILABILITY_RULE,
    )
    from qsys.data.income_sidecar import (
        INCOME_SIDECAR_SCHEMA,
        INCOME_SIDECAR_TRANSFORM,
    )
    from qsys.ops.shareholder_sync import (
        AUDITED_SNAPSHOT_CONTRACT,
        AUDITED_SNAPSHOT_SCHEMA,
    )

    symbols = ["000001.SZ"]
    income_root = project / "data/research/source_snapshots/income/test"
    income = _write(income_root / "income.parquet", b"income")
    income_identity = {
        "schema": INCOME_SIDECAR_SCHEMA,
        "transform_contract": INCOME_SIDECAR_TRANSFORM,
        "financial_availability_contract": FINANCIAL_AVAILABILITY_CONTRACT,
        "financial_availability_rule": FINANCIAL_AVAILABILITY_RULE,
        "source": "tushare", "endpoint": "income",
        "source_run_id": "evidence-1",
        "terminal_receipt_sha256": terminal_sha256,
        "scope_key": "tiny", "range_start": "20200101", "range_end": "20200131",
        "availability_cutoff": "20200131", "required_history_start": "20200101",
        "symbol_count": 1, "symbols_sha256": stable_scope_hash(symbols),
        "source_receipts": [],
    }
    income_identity_bytes = (
        json.dumps(income_identity, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode()
    income_manifest_payload = {
        "schema_version": 1, "artifact_type": INCOME_SIDECAR_SCHEMA,
        "artifact_id": hashlib.sha256(income_identity_bytes).hexdigest(),
        "identity": income_identity,
        "artifact": {"path": "income.parquet", "sha256": sha256_file(income)},
        "scope": {
            "scope_key": "tiny", "range_start": "20200101", "range_end": "20200131",
            "availability_cutoff": "20200131", "required_history_start": "20200101",
            "symbol_count": 1, "symbols_sha256": stable_scope_hash(symbols),
            "symbols": symbols,
        },
        "contracts": {
            "transform": INCOME_SIDECAR_TRANSFORM,
            "financial_availability": FINANCIAL_AVAILABILITY_CONTRACT,
            "availability_rule": FINANCIAL_AVAILABILITY_RULE,
        },
        "source_evidence": {
            "run_id": "evidence-1", "terminal_receipt_sha256": terminal_sha256,
        },
    }
    income_manifest = _write(
        income_root / "manifest.json", json.dumps(income_manifest_payload),
    )
    income_semantics = {
        "artifact_id": income_manifest_payload["artifact_id"],
        "source_run_id": "evidence-1", "terminal_receipt_sha256": terminal_sha256,
        "scope_key": "tiny", "range_start": "20200101", "range_end": "20200131",
        "availability_cutoff": "20200131", "required_history_start": "20200101",
        "transform_contract": INCOME_SIDECAR_TRANSFORM,
        "financial_availability_contract": FINANCIAL_AVAILABILITY_CONTRACT,
        "availability_rule": FINANCIAL_AVAILABILITY_RULE,
        "symbol_count": 1, "symbols_sha256": stable_scope_hash(symbols),
    }

    # The immutable snapshot may cover the historical union, which can be
    # broader than the instruments consumed by this baseline request.
    shareholder_symbols = ["000001.SZ", "000002.SZ"]
    shareholder_root = project / "data/research/source_snapshots/shareholder/test"
    holder = _write(shareholder_root / "holder_num.parquet", b"holder")
    top10 = _write(shareholder_root / "top10_holder_ratio.parquet", b"top10")
    shareholder_identity = {
        "schema": AUDITED_SNAPSHOT_SCHEMA, "contract": AUDITED_SNAPSHOT_CONTRACT,
        "source": "tushare", "source_run_id": "evidence-1",
        "terminal_receipt_sha256": terminal_sha256,
        "scope_key": "tiny", "range_start": "20200101", "range_end": "20200131",
        "symbol_count": len(shareholder_symbols),
        "symbols_sha256": stable_scope_hash(shareholder_symbols),
        "receipt_count": 2, "receipts_sha256": "c" * 64,
    }
    shareholder_identity_bytes = (
        json.dumps(shareholder_identity, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode()
    shareholder_manifest_payload = {
        "schema_version": 2, "artifact_type": AUDITED_SNAPSHOT_SCHEMA,
        "artifact_id": hashlib.sha256(shareholder_identity_bytes).hexdigest(),
        "identity": shareholder_identity,
        "artifacts": {
            "holder_num": {"path": holder.name, "sha256": sha256_file(holder)},
            "top10_holder_ratio": {"path": top10.name, "sha256": sha256_file(top10)},
        },
        "scope": {
            "scope_key": "tiny", "range_start": "20200101", "range_end": "20200131",
            "symbol_count": len(shareholder_symbols),
            "symbols_sha256": stable_scope_hash(shareholder_symbols),
            "symbols": shareholder_symbols,
        },
        "contracts": {"transform": AUDITED_SNAPSHOT_CONTRACT},
        "source_evidence": {
            "run_id": "evidence-1", "terminal_receipt_sha256": terminal_sha256,
        },
    }
    shareholder_manifest = _write(
        shareholder_root / "manifest.json", json.dumps(shareholder_manifest_payload),
    )
    shareholder_semantics = {
        "artifact_id": shareholder_manifest_payload["artifact_id"],
        "source_run_id": "evidence-1", "terminal_receipt_sha256": terminal_sha256,
        "scope_key": "tiny", "range_start": "20200101", "range_end": "20200131",
        "symbol_count": len(shareholder_symbols),
        "symbols_sha256": stable_scope_hash(shareholder_symbols),
        "transform_contract": AUDITED_SNAPSHOT_CONTRACT,
    }

    consumed = {
        "income": {
            "artifact": _identity(project, income.relative_to(project).as_posix()),
            "manifest": _identity(project, income_manifest.relative_to(project).as_posix()),
            **income_semantics,
        },
        "shareholder": {
            "holder_num": _identity(project, holder.relative_to(project).as_posix()),
            "top10_holder_ratio": _identity(project, top10.relative_to(project).as_posix()),
            "manifest": _identity(
                project, shareholder_manifest.relative_to(project).as_posix(),
            ),
            **shareholder_semantics,
        },
    }
    generator_params = {
        "income_source_mode": "audited_sidecar_v1",
        "income_sidecar_path": consumed["income"]["artifact"]["path"],
        "income_sidecar_sha256": consumed["income"]["artifact"]["sha256"],
        "income_sidecar_manifest_path": consumed["income"]["manifest"]["path"],
        "income_sidecar_manifest_sha256": consumed["income"]["manifest"]["sha256"],
        "income_sidecar_required_history_start": "20200101",
        "shareholder_holder_path": consumed["shareholder"]["holder_num"]["path"],
        "shareholder_holder_sha256": consumed["shareholder"]["holder_num"]["sha256"],
        "shareholder_top10_path": consumed["shareholder"]["top10_holder_ratio"]["path"],
        "shareholder_top10_sha256": consumed["shareholder"]["top10_holder_ratio"]["sha256"],
        "shareholder_manifest_path": consumed["shareholder"]["manifest"]["path"],
        "shareholder_manifest_sha256": consumed["shareholder"]["manifest"]["sha256"],
    }
    lineage = {
        "income_sidecar": {
            "path": str(income.resolve()), "sha256": sha256_file(income),
            "manifest_path": str(income_manifest.resolve()),
            "manifest_sha256": sha256_file(income_manifest), **income_semantics,
        },
        "holder_num": {"path": str(holder.resolve()), "sha256": sha256_file(holder)},
        "top10_holder_ratio": {"path": str(top10.resolve()), "sha256": sha256_file(top10)},
        "shareholder_sidecar": {
            "path": str(shareholder_manifest.resolve()),
            "sha256": sha256_file(shareholder_manifest), **shareholder_semantics,
        },
    }
    return consumed, generator_params, lineage


def test_consumed_sidecars_require_exact_request_config_signal_and_terminal_backlink(
    tiny_project: tuple[Path, Path, Path],
) -> None:
    project, _request_path, database = tiny_project
    terminal_sha = sha256_file(project / "source_runs/evidence-1/receipt.json")
    consumed, generator_params, lineage = _synthetic_consumed_sidecars(
        project, terminal_sha256=terminal_sha,
    )
    dependencies = {
        "features": [{"dependencies": [
            {"dataset": "income_sidecar"},
            {"dataset": "shareholder_holdernumber"},
            {"dataset": "shareholder_top10"},
        ]}],
    }
    evidence = pit_certification.read_selected_evidence(database, ["evidence-1"], [])
    context = {
        "dependency_datasets": [
            "income_sidecar", "shareholder_holdernumber", "shareholder_top10",
        ],
        "generator_params": generator_params,
        "feature_source_lineage": lineage,
    }
    request = {"scope_key": "tiny", "consumed_sidecars": consumed}
    spans = [{
        "instrument": "000001.SZ", "date_start": "20200101", "date_end": "20200131",
    }]

    validated = pit_certification._validate_consumed_sidecars(
        project=project, request=request, dependencies=dependencies,
        lineage_context=context, spans=spans, evidence=evidence,
    )
    assert set(validated) == {"income", "shareholder"}
    assert validated["income"]["required_history_start"] == "20200101"
    assert validated["shareholder"]["terminal_receipt_sha256"] == terminal_sha
    assert validated["shareholder"]["symbol_count"] == 2

    floored_dependencies = copy.deepcopy(dependencies)
    for item in floored_dependencies["features"]:
        for dependency in item["dependencies"]:
            dependency["evidence_date_floor"] = "20200101"
    floored = pit_certification._validate_consumed_sidecars(
        project=project, request=request, dependencies=floored_dependencies,
        lineage_context=context,
        spans=[{
            "instrument": "000001.SZ",
            "date_start": "20190101",
            "date_end": "20200131",
        }],
        evidence=evidence,
    )
    assert floored["income"]["range_start"] == "20200101"
    assert floored["shareholder"]["range_start"] == "20200101"

    old_request = {"scope_key": "tiny"}
    with pytest.raises(CertificationError, match="must exactly match"):
        pit_certification._validate_consumed_sidecars(
            project=project, request=old_request, dependencies=dependencies,
            lineage_context=context, spans=spans, evidence=evidence,
        )
    old_lineage = dict(lineage)
    old_lineage.pop("income_sidecar")
    with pytest.raises(CertificationError, match="income signal"):
        pit_certification._validate_consumed_sidecars(
            project=project, request=request, dependencies=dependencies,
            lineage_context={**context, "feature_source_lineage": old_lineage},
            spans=spans, evidence=evidence,
        )


def test_consumed_sidecar_tamper_and_manifest_mismatch_fail_closed(
    tiny_project: tuple[Path, Path, Path],
) -> None:
    project, _request_path, database = tiny_project
    terminal_sha = sha256_file(project / "source_runs/evidence-1/receipt.json")
    consumed, generator_params, lineage = _synthetic_consumed_sidecars(
        project, terminal_sha256=terminal_sha,
    )
    context = {
        "dependency_datasets": ["income_sidecar"],
        "generator_params": generator_params,
        "feature_source_lineage": lineage,
    }
    request = {"scope_key": "tiny", "consumed_sidecars": {"income": consumed["income"]}}
    evidence = pit_certification.read_selected_evidence(database, ["evidence-1"], [])
    spans = [{
        "instrument": "000001.SZ", "date_start": "20200101", "date_end": "20200131",
    }]
    Path(project / consumed["income"]["artifact"]["path"]).write_bytes(b"tampered")
    with pytest.raises(CertificationError, match="sha256 mismatch"):
        pit_certification._validate_consumed_sidecars(
            project=project, request=request,
            dependencies={"features": [{"dependencies": [{"dataset": "income_sidecar"}]}]},
            lineage_context=context, spans=spans, evidence=evidence,
        )


def test_sidecar_mutation_accounting_requires_manifest_source_identity() -> None:
    mutation = {
        "date_start": "20200101", "date_end": "20200102",
        "ingested_at": "2020-02-01T00:00:00Z",
    }
    proof = {"watermark": {
        "range_start": "20200101", "trusted_through": "20200131",
        "updated_at": "2020-02-02T00:00:00Z", "run_id": "newer-run",
        "terminal_receipt_sha256": "b" * 64,
    }}
    sidecar = {"source_run_id": "sidecar-run", "terminal_receipt_sha256": "a" * 64}
    assert not pit_certification._proof_accounts_mutation(
        proof, mutation, required_sidecar_identity=sidecar,
    )
    proof["watermark"].update({
        "run_id": "sidecar-run", "terminal_receipt_sha256": "a" * 64,
    })
    assert pit_certification._proof_accounts_mutation(
        proof, mutation, required_sidecar_identity=sidecar,
    )


def test_noncanonical_sidecar_alias_is_unknown_but_scope_out_mutation_is_disjoint() -> None:
    scope = {
        "source": "tushare", "dataset": "shareholder_holdernumber",
        "endpoint": "stk_holdernumber", "field": "holder_num",
        "instrument": "000001.SZ", "date_start": "20200101", "date_end": "20200131",
    }
    index = pit_certification._build_mutation_scope_index([scope])
    alias = {
        "source": "tushare", "dataset": "shareholder", "endpoint": "stk_holdernumber",
        "fields": ["$holder_num"], "symbol": "000001.SZ",
        "date_start": "20200101", "date_end": "20200131",
    }
    assert pit_certification._mutation_candidate_indices(alias, index) == ([], True)
    noncanonical_instrument = {**alias, "dataset": "shareholder_holdernumber"}
    noncanonical_instrument["symbol"] = "000001.sz"
    assert pit_certification._mutation_candidate_indices(
        noncanonical_instrument, index,
    ) == ([], True)
    outside = {**alias, "dataset": "unrelated_canonical_dataset", "fields": ["close"]}
    assert pit_certification._mutation_candidate_indices(outside, index) == ([], False)
    assert classify_mutation_intersection(outside, scope) == "DISJOINT"
    extra_snapshot_symbol = {
        **alias,
        "dataset": "shareholder_holdernumber",
        "fields": ["holder_num"],
        "symbol": "000002.SZ",
    }
    assert pit_certification._mutation_candidate_indices(
        extra_snapshot_symbol, index,
    ) == ([], False)
    assert classify_mutation_intersection(extra_snapshot_symbol, scope) == "DISJOINT"


def test_real_feature_contract_binds_exact_96_and_multisource_scopes() -> None:
    registry, dependencies = validate_feature_dependencies(
        "v3a_plus_liquidity_financial_rc", REAL_DEPENDENCIES
    )
    assert registry["feature_count"] == 96
    assert registry["features_sha256"] == "707364b53ec71d68503ca67702999a5c375af166a3ba390912a0a53b9ef0d25e"
    operating = next(row for row in dependencies["features"] if row["feature"] == "operating_cf_to_profit")
    assert {(row["endpoint"], tuple(row["leaf_fields"])) for row in operating["dependencies"]} == {
        ("cashflow", ("n_cashflow_act",)), ("income", ("n_income",)),
    }
    scopes, keys = _scope_rows(
        dependencies, [{"instrument": "000001.SZ", "date_start": "20200101", "date_end": "20200102"}]
    )
    assert len(scopes) == len(keys)
    assert any(row["endpoint"] == "daily_basic" and row["field"] == "total_mv" for row in scopes)
    income = next(row for row in dependencies["features"] if row["feature"] == "ttm_revenue_yoy")
    assert income["dependencies"][0]["dataset"] == "income_sidecar"
    assert income["dependencies"][0]["pit_status"] == "evidence_required"
    assert "blocker_codes" not in income["dependencies"][0]
    industry = next(row for row in dependencies["features"] if row["feature"] == "rps_industry_60d")
    classification = next(row for row in industry["dependencies"] if row["endpoint"] == "bak_basic")
    assert classification["source"] == "tushare"
    assert classification["dataset"] == "canonical_daily"
    assert classification["evidence_date_floor"] == "20180313"
    floor_scopes, _ = _scope_rows(
        dependencies,
        [{"instrument": "000001.SZ", "date_start": "20140101", "date_end": "20200102"}],
    )
    industry_scope = next(
        row for row in floor_scopes
        if row["endpoint"] == "bak_basic" and row["field"] == "industry"
    )
    assert industry_scope["date_start"] == "20180313"
    pre_floor_scopes, _ = _scope_rows(
        dependencies,
        [{"instrument": "DELISTED.SZ", "date_start": "20140101", "date_end": "20171231"}],
    )
    assert not any(row["endpoint"] == "bak_basic" for row in pre_floor_scopes)
    assert all(row["date_start"] <= row["date_end"] for row in pre_floor_scopes)
    bad = yaml.safe_load(REAL_DEPENDENCIES.read_text(encoding="utf-8"))
    bad["features"][0], bad["features"][1] = bad["features"][1], bad["features"][0]
    path = ROOT / ".pytest-pit-dependencies-bad.yaml"
    try:
        path.write_text(yaml.safe_dump(bad), encoding="utf-8")
        with pytest.raises(CertificationError, match="order/content"):
            validate_feature_dependencies("v3a_plus_liquidity_financial_rc", path)
    finally:
        path.unlink(missing_ok=True)


def test_real_checkpoint_set_and_ephemeral_model_identity() -> None:
    research_path = ROOT / REAL_REQUEST["identities"]["research_config"]["path"]
    checkpoint_root = ROOT / REAL_REQUEST["checkpoints"]["path"]
    expected_count = int(REAL_REQUEST["checkpoints"]["checkpoint_count"])
    if len(list(checkpoint_root.glob("**/*.manifest.json"))) != expected_count:
        pytest.skip("real 68-checkpoint fixture is not available in this checkout")
    scope = load_checkpoint_scope(
        checkpoint_root=checkpoint_root,
        request=REAL_REQUEST["checkpoints"], research_config=yaml.safe_load(research_path.read_text()),
    )
    assert scope["checkpoint_count"] == 68
    assert scope["checkpoint_set_sha256"] == REAL_REQUEST["checkpoints"]["checkpoint_set_sha256"]
    assert scope["model"] == {"mode": "ephemeral_per_window", "path": None}


def test_universe_warmup_expands_each_membership_span(tmp_path: Path) -> None:
    path = tmp_path / "membership.parquet"
    pd.DataFrame([
        {"instrument": "A", "effective_from": "20200101", "effective_to": "20201231"},
        {"instrument": "B", "effective_from": "20200601", "effective_to": "20201231"},
    ]).to_parquet(path, index=False)
    spans, boundary = load_universe_spans(
        path, feature_start="20200615", feature_end="20200630", max_lookback_days=30
    )
    by_instrument = {row["instrument"]: row for row in spans}
    assert by_instrument["A"]["date_start"] == "20200516"
    assert by_instrument["B"]["date_start"] == "20200516"
    assert boundary["feature_input_start"] == "20200615"
    long_path = tmp_path / "long-membership.parquet"
    pd.DataFrame([{
        "instrument": "A", "effective_from": "20180313", "effective_to": "20260731",
    }]).to_parquet(long_path, index=False)
    long_spans, _ = load_universe_spans(
        long_path, feature_start="20180313", feature_end="20260731", max_lookback_days=1461,
    )
    assert long_spans[0]["date_start"] == "20140313"


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ({"source": "tushare", "dataset": "canonical_daily", "endpoint": "daily", "fields": ["$close", "vol"], "symbol": "A", "date_start": "20200131", "date_end": "20200131", "mutation_type": "update"}, "INTERSECTS"),
        ({"source": "tushare", "dataset": "canonical_daily", "endpoint": "daily_bundle", "fields": ["close"], "symbol": "A", "date_start": "20200101", "date_end": "20200131", "mutation_type": "update"}, "INTERSECTS"),
        ({"source": "other", "dataset": None, "endpoint": None, "fields": None, "symbol": None, "mutation_type": "update"}, "DISJOINT"),
        ({"source": None, "dataset": "canonical_daily", "endpoint": "daily", "fields": ["close"], "symbol": "A", "date_start": "20200101", "date_end": "20200131", "mutation_type": "update"}, "UNKNOWN"),
        ({"mutation_type": "noop"}, "DISJOINT"),
    ],
)
def test_four_dimensional_mutation_intersection(mutation: dict, expected: str) -> None:
    scope = {"source": "tushare", "dataset": "canonical_daily", "endpoint": "daily", "field": "close", "instrument": "A", "date_start": "20200101", "date_end": "20200131"}
    assert classify_mutation_intersection(mutation, scope) == expected


@pytest.mark.parametrize(
    ("supplier_field", "canonical_field"),
    [
        ("n_cashflow_act", "op_cashflow"), ("n_income", "net_income"),
        ("rzye", "margin_balance"), ("rzmre", "margin_buy_amount"),
        ("rzche", "margin_repay_amount"),
    ],
)
def test_supplier_fields_intersect_canonical_daily_bundle_aliases(
    supplier_field: str, canonical_field: str,
) -> None:
    scope = {
        "source": "tushare", "dataset": "canonical_daily", "endpoint": "income",
        "field": supplier_field, "instrument": "A", "date_start": "20200101",
        "date_end": "20200131",
    }
    mutation = {
        "source": "tushare", "dataset": "canonical_daily", "endpoint": "daily_bundle",
        "fields": [canonical_field], "symbol": "A", "date_start": "20200101",
        "date_end": "20200131", "mutation_type": "update",
    }
    assert classify_mutation_intersection(mutation, scope) == "INTERSECTS"


def test_read_only_certification_deterministic_exclusive_and_hash_linked(
    tiny_project: tuple[Path, Path, Path], tmp_path: Path,
) -> None:
    project, request, database = tiny_project
    before = database.read_bytes()
    first = certify_pit_baseline(
        request_path=request, audit_db=database, output_root=tmp_path / "out-a",
        evidence_run_ids=["evidence-1"], project_root=project,
    )
    second = certify_pit_baseline(
        request_path=request, audit_db=database, output_root=tmp_path / "out-b",
        evidence_run_ids=["evidence-1"], project_root=project,
    )
    assert first["status"] == "CERTIFIED"
    assert first["audit_id"] == second["audit_id"]
    assert database.read_bytes() == before
    receipt = json.loads(Path(first["receipt_path"]).read_text())
    assert receipt["baseline_status"] == "CERTIFIED"
    for name, digest in receipt["artifacts"].items():
        assert sha256_file(Path(first["output_dir"]) / name) == digest
    with pytest.raises(FileExistsError):
        certify_pit_baseline(
            request_path=request, audit_db=database, output_root=tmp_path / "out-a",
            evidence_run_ids=["evidence-1"], project_root=project,
        )


def test_certified_datapack_exports_exact_inputs_without_qlib(
    tiny_project: tuple[Path, Path, Path],
) -> None:
    project, request, database = tiny_project
    certified = certify_pit_baseline(
        request_path=request, audit_db=database, output_root=project / "certifications",
        evidence_run_ids=["evidence-1"], project_root=project,
    )
    pack = project / "exports/tiny-pack"
    exported = export_certified_datapack(
        certification_dir=certified["output_dir"], output_dir=pack,
        project_root=project,
    )
    assert exported["status"] == "VERIFIED"
    assert exported["baseline_id"] == "tiny-baseline"
    assert (pack / "data/canonical/daily/000001.SZ.feather").is_file()
    assert (pack / "data/raw.parquet").is_file()
    assert (pack / "data/corporate_actions/tiny-ca/events.parquet").is_file()
    assert not (pack / "data/qlib_bin").exists()
    assert verify_datapack(pack)["pack_id"] == exported["pack_id"]
    cli = subprocess.run(
        [
            sys.executable, str(ROOT / "scripts/research/certify_pit_baseline.py"),
            "--verify-datapack", str(pack),
        ],
        cwd=ROOT, check=False, capture_output=True, text=True,
    )
    assert cli.returncode == 0, cli.stderr
    assert json.loads(cli.stdout)["status"] == "VERIFIED"

    (pack / "data/canonical/daily/000001.SZ.feather").write_bytes(b"tampered")
    with pytest.raises(CertificationError, match="checksum mismatch"):
        verify_datapack(pack)


def test_datapack_roundtrip_packages_only_certified_consumed_sidecars(
    tiny_project: tuple[Path, Path, Path],
) -> None:
    project, request, database = tiny_project
    terminal_sha = sha256_file(project / "source_runs/evidence-1/receipt.json")
    consumed, _params, _lineage = _synthetic_consumed_sidecars(
        project, terminal_sha256=terminal_sha,
    )
    certified = certify_pit_baseline(
        request_path=request, audit_db=database, output_root=project / "certifications",
        evidence_run_ids=["evidence-1"], project_root=project,
    )
    certification_dir = Path(certified["output_dir"])
    receipt_path = certification_dir / "audit_receipt.json"
    scope_path = certification_dir / "audit_scope.json"
    receipt = json.loads(receipt_path.read_text())
    scope = json.loads(scope_path.read_text())
    receipt["input_identities"]["consumed_sidecars"] = consumed
    audit_id = _sha256_bytes(_canonical_bytes(receipt["input_identities"]))
    receipt["audit_id"] = audit_id
    scope.update(receipt["input_identities"])
    scope["audit_id"] = audit_id
    scope_path.write_text(json.dumps(scope, indent=2, sort_keys=True) + "\n")
    receipt["artifacts"]["audit_scope.json"] = sha256_file(scope_path)
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")

    pack = project / "exports/sidecar-pack"
    exported = export_certified_datapack(
        certification_dir=certification_dir, output_dir=pack, project_root=project,
    )
    assert exported["status"] == "VERIFIED"
    assert (pack / "data/sidecars/income/income.parquet").is_file()
    assert (pack / "data/sidecars/income/manifest.json").is_file()
    assert (pack / "data/sidecars/shareholder/holder_num.parquet").is_file()
    assert (pack / "data/sidecars/shareholder/top10_holder_ratio.parquet").is_file()
    assert (pack / "data/sidecars/shareholder/manifest.json").is_file()

    shareholder_manifest = pack / "data/sidecars/shareholder/manifest.json"
    payload = json.loads(shareholder_manifest.read_text())
    payload["artifacts"]["holder_num"]["sha256"] = "0" * 64
    shareholder_manifest.write_text(json.dumps(payload))
    manifest_path = pack / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for row in manifest["files"]:
        if row["path"] == "data/sidecars/shareholder/manifest.json":
            row["sha256"] = sha256_file(shareholder_manifest)
            row["size"] = shareholder_manifest.stat().st_size
    manifest["pack_id"] = _sha256_bytes(_canonical_bytes(manifest["files"]))
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    members = sorted(
        path for path in pack.rglob("*")
        if path.is_file() and path.name != "checksums.sha256"
    )
    (pack / "checksums.sha256").write_text(
        "".join(
            f"{sha256_file(path)}  {path.relative_to(pack).as_posix()}\n"
            for path in members
        )
    )
    with pytest.raises(CertificationError, match="shareholder sidecar manifest mismatch"):
        verify_datapack(pack)


def test_datapack_rejects_non_certified_receipt(
    tiny_project: tuple[Path, Path, Path],
) -> None:
    project, request, database = tiny_project
    blocked = certify_pit_baseline(
        request_path=request, audit_db=database, output_root=project / "blocked",
        project_root=project,
    )
    with pytest.raises(CertificationError, match="only a CERTIFIED"):
        export_certified_datapack(
            certification_dir=blocked["output_dir"], output_dir=project / "should-not-exist",
            project_root=project,
        )


def test_datapack_rejects_canonical_change_after_certification(
    tiny_project: tuple[Path, Path, Path],
) -> None:
    project, request, database = tiny_project
    certified = certify_pit_baseline(
        request_path=request, audit_db=database, output_root=project / "certifications",
        evidence_run_ids=["evidence-1"], project_root=project,
    )
    (project / "data/canonical/daily/000001.SZ.feather").write_bytes(b"changed")
    with pytest.raises(CertificationError, match="changed after certification"):
        export_certified_datapack(
            certification_dir=certified["output_dir"], output_dir=project / "exports/changed",
            project_root=project,
        )


def test_datapack_verify_rejects_symlink_root(
    tiny_project: tuple[Path, Path, Path],
) -> None:
    project, request, database = tiny_project
    certified = certify_pit_baseline(
        request_path=request, audit_db=database, output_root=project / "certifications",
        evidence_run_ids=["evidence-1"], project_root=project,
    )
    pack = project / "exports/pack"
    export_certified_datapack(
        certification_dir=certified["output_dir"], output_dir=pack, project_root=project,
    )
    alias = project / "exports/pack-alias"
    alias.symlink_to(pack, target_is_directory=True)
    with pytest.raises(CertificationError, match="symlink"):
        verify_datapack(alias)


def test_datapack_rejects_canonical_instrument_and_ca_path_traversal(
    tiny_project: tuple[Path, Path, Path],
) -> None:
    project, _request, _database = tiny_project
    with pytest.raises(CertificationError, match="unsafe consumed canonical instrument"):
        _canonical_materialization_identity(project, project / "backtest.json", ["../secret"])

    backtest = json.loads((project / "backtest.json").read_text(encoding="utf-8"))
    ca_manifest_path = project / "data/research/corporate_actions/tiny-ca/manifest.json"
    ca_manifest = json.loads(ca_manifest_path.read_text(encoding="utf-8"))
    ca_manifest["source_raw_path"] = "../../../../signal.parquet"
    ca_manifest["source_raw_artifact_sha256"] = sha256_file(project / "signal.parquet")
    ca_manifest_path.write_text(json.dumps(ca_manifest), encoding="utf-8")
    backtest["accounting"]["corporate_action_manifest"] = ca_manifest
    with pytest.raises(CertificationError, match="escapes"):
        _corporate_action_files(project=project, backtest_manifest=backtest, files={})


def test_datapack_verifier_rejects_qlib_member(
    tiny_project: tuple[Path, Path, Path],
) -> None:
    project, request, database = tiny_project
    certified = certify_pit_baseline(
        request_path=request, audit_db=database, output_root=project / "certifications",
        evidence_run_ids=["evidence-1"], project_root=project,
    )
    pack = project / "exports/pack"
    export_certified_datapack(
        certification_dir=certified["output_dir"], output_dir=pack, project_root=project,
    )
    qlib = _write(pack / "data/qlib_bin/features/a.bin", b"cache")
    manifest_path = pack / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].append({
        "path": qlib.relative_to(pack).as_posix(),
        "sha256": sha256_file(qlib), "size": qlib.stat().st_size,
    })
    manifest["files"].sort(key=lambda row: row["path"])
    manifest["pack_id"] = _sha256_bytes(_canonical_bytes(manifest["files"]))
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    members = sorted(
        path for path in pack.rglob("*")
        if path.is_file() and path.name != "checksums.sha256"
    )
    (pack / "checksums.sha256").write_text(
        "".join(f"{sha256_file(path)}  {path.relative_to(pack).as_posix()}\n" for path in members),
        encoding="utf-8",
    )
    with pytest.raises(CertificationError, match="Qlib cache is forbidden"):
        verify_datapack(pack)


def test_datapack_verifier_rejects_self_consistent_audit_only_pack(
    tiny_project: tuple[Path, Path, Path],
) -> None:
    project, request, database = tiny_project
    certified = certify_pit_baseline(
        request_path=request, audit_db=database, output_root=project / "certifications",
        evidence_run_ids=["evidence-1"], project_root=project,
    )
    pack = project / "exports/pack"
    export_certified_datapack(
        certification_dir=certified["output_dir"], output_dir=pack, project_root=project,
    )
    for name in ("contracts", "lineage", "data"):
        shutil.rmtree(pack / name)
    manifest_path = pack / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"] = [
        row for row in manifest["files"] if row["path"].startswith("audit/")
    ]
    manifest["pack_id"] = _sha256_bytes(_canonical_bytes(manifest["files"]))
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    members = sorted(
        path for path in pack.rglob("*")
        if path.is_file() and path.name != "checksums.sha256"
    )
    (pack / "checksums.sha256").write_text(
        "".join(f"{sha256_file(path)}  {path.relative_to(pack).as_posix()}\n" for path in members),
        encoding="utf-8",
    )
    with pytest.raises(CertificationError):
        verify_datapack(pack)


def test_zero_or_missing_evidence_produces_complete_blocked_report(
    tiny_project: tuple[Path, Path, Path], tmp_path: Path,
) -> None:
    project, request, database = tiny_project
    zero = certify_pit_baseline(
        request_path=request, audit_db=database, output_root=tmp_path / "zero", project_root=project,
    )
    missing = certify_pit_baseline(
        request_path=request, audit_db=database, output_root=tmp_path / "missing",
        evidence_run_ids=["does-not-exist"], project_root=project,
    )
    assert zero["status"] == missing["status"] == "BLOCKED"
    reasons = set(pd.read_parquet(Path(zero["output_dir"]) / "exceptions.parquet")["reason_code"])
    assert {"NO_EVIDENCE_RUNS_SELECTED", "FEATURE_DEPENDENCY_EVIDENCE_GAP"}.issubset(reasons)
    reasons = set(pd.read_parquet(Path(missing["output_dir"]) / "exceptions.parquet")["reason_code"])
    assert "EVIDENCE_RUN_MISSING" in reasons
    mutation_missing = certify_pit_baseline(
        request_path=request, audit_db=database, output_root=tmp_path / "mutation-missing",
        evidence_run_ids=["evidence-1"], mutation_run_ids=["evidence-1"], project_root=project,
    )
    reasons = set(pd.read_parquet(
        Path(mutation_missing["output_dir"]) / "exceptions.parquet"
    )["reason_code"])
    assert "MUTATION_RUN_MISSING" in reasons


def test_semantic_requirement_emits_blocking_certification_exception(
    tiny_project: tuple[Path, Path, Path], tmp_path: Path,
) -> None:
    project, request, database = tiny_project
    dependencies_path = project / "dependencies.yaml"
    dependencies = yaml.safe_load(dependencies_path.read_text(encoding="utf-8"))
    dependencies["semantic_blockers"] = [{
        "code": "TEST_SEMANTIC_CAPABILITY_UNVERIFIED",
        "required_contract": "test_latest_known_v1",
        "endpoints": ["daily"],
        "reason": "fixture intentionally lacks the required semantic capability",
    }]
    dependencies_path.write_text(
        yaml.safe_dump(dependencies, sort_keys=False), encoding="utf-8"
    )

    result = certify_pit_baseline(
        request_path=request, audit_db=database, output_root=tmp_path / "semantic",
        evidence_run_ids=["evidence-1"], project_root=project,
    )

    assert result["status"] == "BLOCKED"
    exceptions = pd.read_parquet(Path(result["output_dir"]) / "exceptions.parquet")
    blocker = exceptions.loc[
        exceptions["reason_code"].eq("TEST_SEMANTIC_CAPABILITY_UNVERIFIED")
    ].iloc[0]
    assert blocker["severity"] == "BLOCKING"
    assert json.loads(blocker["affected_features_json"]) == ["ret_1d"]


def test_tampered_supplier_payload_blocks_coverage(
    tiny_project: tuple[Path, Path, Path], tmp_path: Path,
) -> None:
    project, request, database = tiny_project
    (project / "raw.parquet").write_bytes(b"tampered")
    result = certify_pit_baseline(
        request_path=request, audit_db=database, output_root=tmp_path / "tampered",
        evidence_run_ids=["evidence-1"], project_root=project,
    )
    assert result["status"] == "BLOCKED"
    coverage = pd.read_parquet(Path(result["output_dir"]) / "coverage.parquet")
    assert set(coverage["status"]) == {"MISSING"}


def test_mutation_newer_than_selected_evidence_requires_reaudit(
    tiny_project: tuple[Path, Path, Path], tmp_path: Path,
) -> None:
    project, request, _database = tiny_project
    database = project / "mutation.db"
    _make_db(database, mutation={
        "mutation_id": "mutation-1", "run_id": "mutation-run", "source": "tushare",
        "dataset": "canonical_daily", "endpoint": "daily_bundle", "fields": ["close"],
        "symbol": "000001.SZ", "date_start": "20200131", "date_end": "20200131",
        "ingested_at": "2020-02-02T00:00:00Z",
    })
    result = certify_pit_baseline(
        request_path=request, audit_db=database, output_root=tmp_path / "reaudit",
        evidence_run_ids=["evidence-1"], project_root=project,
    )
    assert result["status"] == "REAUDIT_REQUIRED"
    exceptions = pd.read_parquet(Path(result["output_dir"]) / "exceptions.parquet")
    row = exceptions.loc[exceptions["reason_code"] == "CANONICAL_MUTATION_INTERSECTS"].iloc[0]
    assert row["mutation_run_id"] == "mutation-run"
    assert row["mutation_id"] == "mutation-1"
    receipt = json.loads(Path(result["receipt_path"]).read_text())
    assert receipt["input_identities"]["full_mutation_ledger_sha256"]


def test_frozen_evidence_replays_selected_rows_but_rescans_current_mutations(
    tiny_project: tuple[Path, Path, Path], tmp_path: Path,
) -> None:
    project, request, database = tiny_project
    source = certify_pit_baseline(
        request_path=request, audit_db=database,
        output_root=project / "certifications",
        evidence_run_ids=["evidence-1"], project_root=project,
    )
    source_receipt = json.loads(Path(source["receipt_path"]).read_text())
    source_snapshot = json.loads(
        (Path(source["output_dir"]) / "evidence_snapshot.json").read_text()
    )
    frozen_request = _request_with_frozen_evidence(project, request, source)
    with sqlite3.connect(database) as connection:
        for table in (
            "field_receipt_links", "trusted_watermarks", "audit_journal",
            "fetch_receipts",
        ):
            connection.execute(f"DELETE FROM {table}")
        connection.execute(
            "INSERT INTO canonical_mutations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "mutation-1", "mutation-run", "canonical_daily", "tushare",
                "daily_bundle", None, "000001.SZ", "20200131", "20200131",
                json.dumps(["close"]), "update", "d" * 64, "e" * 64,
                "2020-02-02T00:00:00Z",
            ),
        )

    result = certify_pit_baseline(
        request_path=frozen_request, audit_db=database,
        output_root=tmp_path / "replay", evidence_run_ids=["evidence-1"],
        project_root=project,
    )

    assert result["status"] == "REAUDIT_REQUIRED"
    receipt = json.loads(Path(result["receipt_path"]).read_text())
    frozen = receipt["input_identities"]["frozen_evidence"]
    assert frozen["source_audit_id"] == source["audit_id"]
    assert receipt["input_identities"]["full_mutation_ledger_sha256"] != (
        source_receipt["input_identities"]["full_mutation_ledger_sha256"]
    )
    replay_snapshot = json.loads(
        (Path(result["output_dir"]) / "evidence_snapshot.json").read_text()
    )
    assert replay_snapshot["audit_db_sha256_at_query"] == (
        source_snapshot["audit_db_sha256_at_query"]
    )
    assert replay_snapshot["certification_audit_db_sha256"] == sha256_file(database)
    reasons = set(pd.read_parquet(
        Path(result["output_dir"]) / "exceptions.parquet"
    )["reason_code"])
    assert "EVIDENCE_RUN_MISSING" not in reasons
    assert "CANONICAL_MUTATION_INTERSECTS" in reasons


def test_frozen_evidence_requires_receipt_backlink_and_exact_selectors(
    tiny_project: tuple[Path, Path, Path], tmp_path: Path,
) -> None:
    project, request, database = tiny_project
    source = certify_pit_baseline(
        request_path=request, audit_db=database,
        output_root=project / "certifications",
        evidence_run_ids=["evidence-1"], project_root=project,
    )
    frozen_request = _request_with_frozen_evidence(project, request, source)
    with pytest.raises(CertificationError, match="selectors do not match"):
        certify_pit_baseline(
            request_path=frozen_request, audit_db=database,
            output_root=tmp_path / "selector-mismatch",
            evidence_run_ids=["different-run"], project_root=project,
        )

    receipt_path = Path(source["receipt_path"])
    receipt = json.loads(receipt_path.read_text())
    receipt["artifacts"]["evidence_snapshot.json"] = "0" * 64
    receipt_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    request_payload = yaml.safe_load(frozen_request.read_text(encoding="utf-8"))
    request_payload["frozen_evidence"]["audit_receipt"]["sha256"] = sha256_file(
        receipt_path
    )
    frozen_request.write_text(
        yaml.safe_dump(request_payload, sort_keys=False), encoding="utf-8",
    )
    with pytest.raises(CertificationError, match="does not bind snapshot"):
        certify_pit_baseline(
            request_path=frozen_request, audit_db=database,
            output_root=tmp_path / "backlink-mismatch",
            evidence_run_ids=["evidence-1"], project_root=project,
        )


def test_validated_evidence_at_mutation_time_accounts_and_certifies(
    tiny_project: tuple[Path, Path, Path], tmp_path: Path,
) -> None:
    project, request, _database = tiny_project
    database = project / "accounted.db"
    _make_db(database, mutation={
        "mutation_id": "mutation-1", "run_id": "mutation-run", "source": "tushare",
        "dataset": "canonical_daily", "endpoint": "daily_bundle", "fields": ["close"],
        "symbol": "000001.SZ", "date_start": "20200131", "date_end": "20200131",
        "ingested_at": "2020-02-01T00:00:00+00:00",
    })
    result = certify_pit_baseline(
        request_path=request, audit_db=database, output_root=tmp_path / "accounted",
        evidence_run_ids=["evidence-1"], project_root=project,
    )
    assert result["status"] == "CERTIFIED"
    coverage = pd.read_parquet(Path(result["output_dir"]) / "coverage.parquet")
    mutation_row = coverage.loc[coverage["scope_kind"] == "canonical_mutation"].iloc[0]
    assert mutation_row["status"] == "ACCOUNTED"
    exceptions = pd.read_parquet(Path(result["output_dir"]) / "exceptions.parquet")
    assert not exceptions["reason_code"].str.startswith("CANONICAL_MUTATION_").any()


def test_large_mutation_ledger_is_streamed_and_detail_is_bounded(
    tiny_project: tuple[Path, Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, request, _database = tiny_project
    database = project / "bounded-mutations.db"
    _make_db(database, mutation={
        "mutation_id": "mutation-1", "run_id": "mutation-run", "source": "tushare",
        "dataset": "canonical_daily", "endpoint": "daily_bundle", "fields": ["close"],
        "symbol": "000001.SZ", "date_start": "20200131", "date_end": "20200131",
        "ingested_at": "2020-02-01T00:00:00+00:00",
    })
    with sqlite3.connect(database) as connection:
        for number in (2, 3):
            connection.execute(
                "INSERT INTO canonical_mutations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"mutation-{number}", "mutation-run", "canonical_daily",
                    "tushare", "daily_bundle", None, "000001.SZ", "20200131",
                    "20200131", json.dumps(["close"]), "update", "d" * 64,
                    "e" * 64, "2020-02-01T00:00:00+00:00",
                ),
            )
    monkeypatch.setattr(pit_certification, "MAX_MUTATION_DETAIL_ROWS", 1)

    result = certify_pit_baseline(
        request_path=request,
        audit_db=database,
        output_root=tmp_path / "bounded",
        evidence_run_ids=["evidence-1"],
        project_root=project,
    )

    scope = json.loads((Path(result["output_dir"]) / "audit_scope.json").read_text())
    summary = scope["canonical_mutation_summary"]
    assert summary["count"] == 3
    assert summary["detail_count"] == 1
    assert summary["detail_omitted_count"] == 2
    coverage = pd.read_parquet(Path(result["output_dir"]) / "coverage.parquet")
    assert len(coverage.loc[coverage["scope_kind"] == "canonical_mutation"]) == 1
    snapshot = json.loads(
        (Path(result["output_dir"]) / "evidence_snapshot.json").read_text()
    )
    assert snapshot["tables"]["canonical_mutations"] == []
    assert snapshot["canonical_mutation_summary"]["count"] == 3
    assert snapshot["full_mutation_ledger_sha256"] == hashlib.sha256(
        _canonical_bytes(list(iter_canonical_mutations(database)))
    ).hexdigest()


def test_multifield_mutation_requires_newer_proof_for_every_consumed_field(
    tiny_project: tuple[Path, Path, Path], tmp_path: Path,
) -> None:
    project, request, _database = tiny_project
    dependencies_path = project / "dependencies.yaml"
    dependencies = yaml.safe_load(dependencies_path.read_text(encoding="utf-8"))
    dependencies["features"][0]["dependencies"][0]["leaf_fields"] = ["close", "volume"]
    dependencies_path.write_text(yaml.safe_dump(dependencies, sort_keys=False), encoding="utf-8")
    database = project / "multifield.db"
    _make_db(database, mutation={
        "mutation_id": "mutation-1", "run_id": "mutation-run", "source": "tushare",
        "dataset": "canonical_daily", "endpoint": "daily_bundle",
        "fields": ["close", "volume"], "symbol": "000001.SZ",
        "date_start": "20200131", "date_end": "20200131",
        "ingested_at": "2020-02-02T00:00:00Z",
    })
    with sqlite3.connect(database) as conn:
        conn.execute(
            "UPDATE trusted_watermarks SET updated_at='2020-02-03T00:00:00Z' "
            "WHERE field_name='close'"
        )
        conn.execute(
            "INSERT INTO field_receipt_links VALUES(?,?,?,?)",
            ("evidence-1", "canonical_daily", "volume", "receipt-1"),
        )
        conn.execute(
            "INSERT INTO trusted_watermarks VALUES(?,?,?,?,?,?,?,?)",
            ("tushare", "volume", "tiny", "20200101", "20200131", "evidence-1",
             "pending", "2020-02-01T00:00:00Z"),
        )
    terminal_path = project / "source_runs/evidence-1/receipt.json"
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    terminal["field_receipt_links"].append({
        "run_id": "evidence-1", "dataset": "canonical_daily",
        "field_name": "$volume", "receipt_id": "receipt-1",
    })
    terminal_path.write_text(json.dumps(terminal, sort_keys=True) + "\n", encoding="utf-8")
    with sqlite3.connect(database) as conn:
        conn.execute(
            "UPDATE trusted_watermarks SET terminal_receipt_sha256=? WHERE run_id='evidence-1'",
            (sha256_file(terminal_path),),
        )
    result = certify_pit_baseline(
        request_path=request, audit_db=database, output_root=tmp_path / "multifield",
        evidence_run_ids=["evidence-1"], project_root=project,
    )
    assert result["status"] == "REAUDIT_REQUIRED"
    coverage = pd.read_parquet(Path(result["output_dir"]) / "coverage.parquet")
    mutation_row = coverage.loc[coverage["scope_kind"] == "canonical_mutation"].iloc[0]
    assert mutation_row["status"] == "INTERSECTS"


def test_unknown_mutation_scope_is_never_accounted(
    tiny_project: tuple[Path, Path, Path], tmp_path: Path,
) -> None:
    project, request, _database = tiny_project
    database = project / "unknown.db"
    _make_db(database, mutation={
        "mutation_id": "mutation-1", "run_id": "mutation-run", "source": "tushare",
        "dataset": "canonical_daily", "endpoint": "daily_bundle", "fields": None,
        "symbol": "000001.SZ", "date_start": "20200131", "date_end": "20200131",
        "ingested_at": "2020-02-01T00:00:00Z",
    })
    result = certify_pit_baseline(
        request_path=request, audit_db=database, output_root=tmp_path / "unknown",
        evidence_run_ids=["evidence-1"], project_root=project,
    )
    assert result["status"] == "REAUDIT_REQUIRED"
    coverage = pd.read_parquet(Path(result["output_dir"]) / "coverage.parquet")
    mutation_row = coverage.loc[coverage["scope_kind"] == "canonical_mutation"].iloc[0]
    assert mutation_row["status"] == "UNKNOWN"
    exceptions = pd.read_parquet(Path(result["output_dir"]) / "exceptions.parquet")
    assert "CANONICAL_MUTATION_SCOPE_UNKNOWN" in set(exceptions["reason_code"])


def test_mutation_candidate_index_avoids_full_scope_scan(
    tiny_project: tuple[Path, Path, Path], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, request_path, _database = tiny_project
    instruments = [f"S{index:05d}" for index in range(2500)]
    target = instruments[1234]
    membership_path = project / "membership.parquet"
    pd.DataFrame({
        "instrument": instruments,
        "effective_from": ["20200101"] * len(instruments),
        "effective_to": ["20200131"] * len(instruments),
    }).to_parquet(membership_path, index=False)
    universe_path = project / "universe.json"
    universe = json.loads(universe_path.read_text(encoding="utf-8"))
    universe["membership_sha256"] = sha256_file(membership_path)
    universe_path.write_text(json.dumps(universe), encoding="utf-8")
    backtest_path = project / "backtest.json"
    backtest = json.loads(backtest_path.read_text(encoding="utf-8"))
    backtest["pit_execution_universe"]["manifest_sha256"] = sha256_file(universe_path)
    backtest["pit_execution_universe"]["membership_sha256"] = sha256_file(membership_path)
    backtest_path.write_text(json.dumps(backtest), encoding="utf-8")
    _refresh_request_identities(
        project, request_path, "universe_manifest", "universe_membership", "backtest_manifest",
    )
    database = project / "indexed.db"
    _make_db(database, mutation={
        "mutation_id": "mutation-1", "run_id": "mutation-run", "source": "tushare",
        "dataset": "canonical_daily", "endpoint": "daily_bundle", "fields": ["close"],
        "symbol": target, "date_start": "20200131", "date_end": "20200131",
    })
    calls = 0

    def counted(mutation: dict, scope: dict) -> str:
        nonlocal calls
        calls += 1
        return classify_mutation_intersection(mutation, scope)

    monkeypatch.setattr("qsys.pit_certification.classify_mutation_intersection", counted)
    result = certify_pit_baseline(
        request_path=request_path, audit_db=database, output_root=tmp_path / "indexed",
        evidence_run_ids=["evidence-1"], project_root=project,
    )
    coverage = pd.read_parquet(Path(result["output_dir"]) / "coverage.parquet")
    assert len(coverage.loc[coverage["scope_kind"] == "feature_dependency"]) == 2500
    assert calls == 1


@pytest.mark.parametrize(
    ("timestamp_owner", "timestamp"),
    [
        ("mutation", None), ("mutation", "not-a-time"),
        ("mutation", "2020-02-01T00:00:00"),
        ("watermark", None), ("watermark", "not-a-time"),
        ("watermark", "2020-02-01T00:00:00"),
    ],
)
def test_missing_invalid_or_naive_accounting_timestamps_fail_closed(
    tiny_project: tuple[Path, Path, Path], tmp_path: Path,
    timestamp_owner: str, timestamp: str | None,
) -> None:
    project, request, _database = tiny_project
    database = project / f"timestamp-{timestamp_owner}.db"
    mutation = {
        "mutation_id": "mutation-1", "run_id": "mutation-run", "source": "tushare",
        "dataset": "canonical_daily", "endpoint": "daily_bundle", "fields": ["close"],
        "symbol": "000001.SZ", "date_start": "20200131", "date_end": "20200131",
        "ingested_at": "2020-02-01T00:00:00Z",
    }
    if timestamp_owner == "mutation":
        mutation["ingested_at"] = timestamp
    _make_db(database, mutation=mutation)
    if timestamp_owner == "watermark":
        with sqlite3.connect(database) as conn:
            conn.execute(
                "UPDATE trusted_watermarks SET updated_at=? WHERE field_name='close'",
                (timestamp,),
            )
    result = certify_pit_baseline(
        request_path=request, audit_db=database,
        output_root=tmp_path / f"timestamp-{timestamp_owner}-{timestamp}",
        evidence_run_ids=["evidence-1"], project_root=project,
    )
    assert result["status"] == "REAUDIT_REQUIRED"
    coverage = pd.read_parquet(Path(result["output_dir"]) / "coverage.parquet")
    mutation_row = coverage.loc[coverage["scope_kind"] == "canonical_mutation"].iloc[0]
    assert mutation_row["status"] == "INTERSECTS"


@pytest.mark.parametrize(
    "case",
    [
        "gate", "trust", "terminal_hash", "narrow_scope", "symbol_hash", "response",
        "status", "payload_kind", "field_link_missing", "field_link_mismatch",
    ],
)
def test_terminal_proof_failures_block_coverage(
    tiny_project: tuple[Path, Path, Path], tmp_path: Path, case: str,
) -> None:
    project, request, database = tiny_project
    terminal_path = project / "source_runs/evidence-1/receipt.json"
    terminal = json.loads(terminal_path.read_text())
    with sqlite3.connect(database) as conn:
        if case == "gate":
            terminal["terminal_gates"]["readiness"] = False
        elif case == "trust":
            terminal["trust_state"] = "untrusted"
        elif case == "terminal_hash":
            terminal["tampered"] = True
        elif case == "narrow_scope":
            scope = dict(terminal["fetch_receipts"][0]["requested_scope"])
            scope["date_start"] = scope["date_end"] = "20200131"
            terminal["fetch_receipts"][0]["requested_scope"] = scope
            conn.execute(
                "UPDATE fetch_receipts SET requested_scope_json=? WHERE receipt_id='receipt-1'",
                (json.dumps(scope, sort_keys=True),),
            )
        elif case == "symbol_hash":
            scope = dict(terminal["fetch_receipts"][0]["requested_scope"])
            scope["symbols_sha256"] = "0" * 64
            terminal["fetch_receipts"][0]["requested_scope"] = scope
            conn.execute(
                "UPDATE fetch_receipts SET requested_scope_json=? WHERE receipt_id='receipt-1'",
                (json.dumps(scope, sort_keys=True),),
            )
        elif case == "response":
            terminal["fetch_receipts"][0]["response_date_min"] = "20200201"
            conn.execute(
                "UPDATE fetch_receipts SET response_date_min='20200201' WHERE receipt_id='receipt-1'"
            )
        elif case == "status":
            terminal["fetch_receipts"][0]["status"] = "partial"
            conn.execute("UPDATE fetch_receipts SET status='partial' WHERE receipt_id='receipt-1'")
        elif case == "payload_kind":
            terminal["fetch_receipts"][0]["payload_kind"] = "derived"
            conn.execute("UPDATE fetch_receipts SET payload_kind='derived' WHERE receipt_id='receipt-1'")
        elif case == "field_link_missing":
            terminal["field_receipt_links"] = []
        elif case == "field_link_mismatch":
            terminal["field_receipt_links"][0]["dataset"] = "wrong_dataset"
    terminal_path.write_text(json.dumps(terminal, sort_keys=True) + "\n", encoding="utf-8")
    if case != "terminal_hash":
        with sqlite3.connect(database) as conn:
            conn.execute(
                "UPDATE trusted_watermarks SET terminal_receipt_sha256=? WHERE run_id='evidence-1'",
                (sha256_file(terminal_path),),
            )
    result = certify_pit_baseline(
        request_path=request, audit_db=database, output_root=tmp_path / case,
        evidence_run_ids=["evidence-1"], project_root=project,
    )
    assert result["status"] == "BLOCKED"
    coverage = pd.read_parquet(Path(result["output_dir"]) / "coverage.parquet")
    assert set(coverage.loc[coverage["scope_kind"] == "feature_dependency", "status"]) == {"MISSING"}


def test_shareholder_terminal_proof_requires_announcement_aligned_request_scope(
    tmp_path: Path,
) -> None:
    audit_db = _write(tmp_path / "audit" / "audit.db", b"")
    instruments = ["000001.SZ"]

    def proof_valid(
        run_id: str,
        *,
        endpoint: str,
        dataset: str,
        field_name: str,
        request_start: str,
        request_end: str,
    ) -> bool:
        requested_scope = {
            "date_start": request_start,
            "date_end": request_end,
            "symbol_count": 1,
            "symbols": instruments,
            "symbols_sha256": stable_scope_hash(instruments),
        }
        receipt = {
            "receipt_id": f"{run_id}-receipt",
            "run_id": run_id,
            "source": "tushare",
            "endpoint": endpoint,
                "status": "success",
                "requested_scope": requested_scope,
                "returned_rows": 1,
                "response_hash": "a" * 64,
                "response_columns": ["ts_code", "ann_date", field_name],
                "response_date_min": "20200430",
            "response_date_max": "20200430",
            "payload_kind": "raw_supplier",
            "payload_path": f"raw/{run_id}.parquet",
            "payload_sha256": "a" * 64,
            "payload_verified": True,
        }
        link = {
            "run_id": run_id,
            "dataset": dataset,
            "field_name": field_name,
            "receipt_id": receipt["receipt_id"],
        }
        terminal = {
            "schema_version": 1,
            "run_id": run_id,
            "trust_state": "trusted",
            "terminal_gates": {
                "fetch": True,
                "raw_payloads": True,
                "canonical_commit": True,
                "qlib_readback": True,
                "readiness": True,
                "contiguous_range": True,
            },
            "fetch_receipts": [receipt],
            "field_receipt_links": [link],
        }
        terminal_path = _write(
            audit_db.parent / "source_runs" / run_id / "receipt.json",
            json.dumps(terminal, sort_keys=True) + "\n",
        )
        watermark = {
            "run_id": run_id,
            "terminal_receipt_sha256": sha256_file(terminal_path),
        }
        return pit_certification._terminal_proof_valid(
            audit_db=audit_db,
            watermark=watermark,
            receipt=receipt,
            link=link,
            scope_start="20200430",
            scope_end="20200430",
            consumed_instruments=instruments,
        )

    assert proof_valid(
        "holdernumber-announcement-aligned",
        endpoint="stk_holdernumber",
        dataset="shareholder_holdernumber",
        field_name="holder_num",
        request_start="20200430",
        request_end="20200430",
    )
    assert proof_valid(
        "shareholder-announcement-aligned",
        endpoint="top10_holders",
        dataset="shareholder_top10",
        field_name="hold_ratio",
        request_start="20200430",
        request_end="20200430",
    )
    assert not proof_valid(
        "shareholder-report-period-scope",
        endpoint="top10_holders",
        dataset="shareholder_top10",
        field_name="hold_ratio",
        request_start="20200101",
        request_end="20200331",
    )


def test_each_field_watermark_must_match_terminal_hash_even_when_run_is_cached(
    tiny_project: tuple[Path, Path, Path], tmp_path: Path,
) -> None:
    project, request, database = tiny_project
    dependencies_path = project / "dependencies.yaml"
    dependencies = yaml.safe_load(dependencies_path.read_text(encoding="utf-8"))
    dependencies["features"][0]["dependencies"][0]["leaf_fields"] = ["close", "open"]
    dependencies_path.write_text(yaml.safe_dump(dependencies, sort_keys=False), encoding="utf-8")

    terminal_path = project / "source_runs/evidence-1/receipt.json"
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    second = dict(terminal["fetch_receipts"][0])
    second["receipt_id"] = "receipt-2"
    terminal["fetch_receipts"].append(second)
    terminal["field_receipt_links"].append({
        "run_id": "evidence-1", "dataset": "canonical_daily",
        "field_name": "$open", "receipt_id": "receipt-2",
    })
    terminal_path.write_text(json.dumps(terminal, sort_keys=True) + "\n", encoding="utf-8")
    good_hash = sha256_file(terminal_path)
    with sqlite3.connect(database) as conn:
        first = conn.execute(
            "SELECT * FROM fetch_receipts WHERE receipt_id='receipt-1'"
        ).fetchone()
        conn.execute(
            "INSERT INTO fetch_receipts SELECT ?,run_id,source,endpoint,status,"
            "requested_scope_json,returned_rows,response_hash,response_columns_json,"
            "response_date_min,response_date_max,attempt_count,payload_kind,payload_path,"
            "payload_sha256,published_at,observed_at,error_json FROM fetch_receipts "
            "WHERE receipt_id='receipt-1'",
            ("receipt-2",),
        )
        assert first is not None
        conn.execute(
            "INSERT INTO field_receipt_links VALUES(?,?,?,?)",
            ("evidence-1", "canonical_daily", "open", "receipt-2"),
        )
        conn.execute(
            "UPDATE trusted_watermarks SET terminal_receipt_sha256=? WHERE field_name='close'",
            (good_hash,),
        )
        conn.execute(
            "INSERT INTO trusted_watermarks VALUES(?,?,?,?,?,?,?,?)",
            ("tushare", "open", "tiny", "20200101", "20200131", "evidence-1",
             "0" * 64, "2020-02-01T00:00:00Z"),
        )

    result = certify_pit_baseline(
        request_path=request, audit_db=database, output_root=tmp_path / "bad_second_hash",
        evidence_run_ids=["evidence-1"], project_root=project,
    )

    assert result["status"] == "BLOCKED"
    coverage = pd.read_parquet(Path(result["output_dir"]) / "coverage.parquet")
    open_rows = coverage.loc[coverage["field"] == "open"]
    assert set(open_rows["status"]) == {"MISSING"}


def test_mutation_role_receipt_cannot_cover_evidence_scope(
    tiny_project: tuple[Path, Path, Path], tmp_path: Path,
) -> None:
    project, request, _ = tiny_project
    database = project / "role.db"
    _make_db(database, mutation={
        "mutation_id": "mutation-1", "run_id": "mutation-run", "source": "tushare",
        "dataset": "canonical_daily", "endpoint": "daily_bundle", "fields": ["close"],
        "symbol": "OTHER", "date_start": "20200131", "date_end": "20200131",
    })
    terminal_path = project / "source_runs/evidence-1/receipt.json"
    terminal = json.loads(terminal_path.read_text())
    terminal["run_id"] = "mutation-run"
    terminal["fetch_receipts"][0]["run_id"] = "mutation-run"
    mutation_terminal = project / "source_runs/mutation-run/receipt.json"
    _write(mutation_terminal, json.dumps(terminal, sort_keys=True) + "\n")
    with sqlite3.connect(database) as conn:
        for table in ("fetch_receipts", "field_receipt_links", "trusted_watermarks", "audit_journal"):
            conn.execute(f"UPDATE {table} SET run_id='mutation-run' WHERE run_id='evidence-1'")
        conn.execute(
            "UPDATE trusted_watermarks SET terminal_receipt_sha256=? WHERE run_id='mutation-run'",
            (sha256_file(mutation_terminal),),
        )
    result = certify_pit_baseline(
        request_path=request, audit_db=database, output_root=tmp_path / "role",
        mutation_run_ids=["mutation-run"], project_root=project,
    )
    coverage = pd.read_parquet(Path(result["output_dir"]) / "coverage.parquet")
    assert set(coverage.loc[coverage["scope_kind"] == "feature_dependency", "status"]) == {"MISSING"}


def test_terminal_receipt_symlink_cannot_cover_scope(
    tiny_project: tuple[Path, Path, Path], tmp_path: Path,
) -> None:
    project, request, database = tiny_project
    terminal = project / "source_runs/evidence-1/receipt.json"
    outside = _write(project / "terminal-copy.json", terminal.read_bytes())
    terminal.unlink()
    terminal.symlink_to(outside)
    result = certify_pit_baseline(
        request_path=request, audit_db=database, output_root=tmp_path / "terminal-symlink",
        evidence_run_ids=["evidence-1"], project_root=project,
    )
    coverage = pd.read_parquet(Path(result["output_dir"]) / "coverage.parquet")
    assert set(coverage.loc[coverage["scope_kind"] == "feature_dependency", "status"]) == {"MISSING"}


def test_artifact_publish_failure_leaves_no_partial_final(
    tiny_project: tuple[Path, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, request, database = tiny_project
    output = tmp_path / "atomic"
    monkeypatch.setattr(pd.DataFrame, "to_parquet", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("injected")))
    with pytest.raises(RuntimeError, match="injected"):
        certify_pit_baseline(
            request_path=request, audit_db=database, output_root=output,
            evidence_run_ids=["evidence-1"], project_root=project,
        )
    baseline_root = output / "tiny-baseline"
    assert baseline_root.is_dir()
    assert list(baseline_root.iterdir()) == []


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("signal_source", "backtest signal source lineage mismatch"),
        ("universe", "backtest PIT universe lineage mismatch"),
        ("checkpoint", "signal manifest checkpoint set identity mismatch"),
    ],
)
def test_cross_artifact_lineage_mismatch_is_input_error(
    tiny_project: tuple[Path, Path, Path], tmp_path: Path, case: str, message: str,
) -> None:
    project, request_path, database = tiny_project
    backtest_path = project / "backtest.json"
    backtest = json.loads(backtest_path.read_text())
    if case == "signal_source":
        backtest["signal_sources"][0]["predictions_sha256"] = "0" * 64
        backtest_path.write_text(json.dumps(backtest), encoding="utf-8")
        _refresh_request_identities(project, request_path, "backtest_manifest")
    elif case == "universe":
        backtest["pit_execution_universe"]["membership_sha256"] = "0" * 64
        backtest_path.write_text(json.dumps(backtest), encoding="utf-8")
        _refresh_request_identities(project, request_path, "backtest_manifest")
    else:
        signal_path = project / "signal.json"
        signal = json.loads(signal_path.read_text())
        signal["window_checkpoint_set_sha256"] = "0" * 64
        signal_path.write_text(json.dumps(signal), encoding="utf-8")
        backtest["signal_sources"][0]["manifest_sha256"] = sha256_file(signal_path)
        backtest_path.write_text(json.dumps(backtest), encoding="utf-8")
        _refresh_request_identities(
            project, request_path, "signal_manifest", "backtest_manifest",
        )
    with pytest.raises(CertificationError, match=message):
        certify_pit_baseline(
            request_path=request_path, audit_db=database, output_root=tmp_path / case,
            evidence_run_ids=["evidence-1"], project_root=project,
        )


def test_shareholder_sidecar_hash_mismatch_is_input_error(
    tiny_project: tuple[Path, Path, Path], tmp_path: Path,
) -> None:
    project, request_path, database = tiny_project
    holder = _write(project / "holder.parquet", b"holder")
    top10 = _write(project / "top10.parquet", b"top10")
    research_path = project / "research.yaml"
    research = yaml.safe_load(research_path.read_text())
    research["generators"] = [{"params": {
        "feature_list_id": "tiny",
        "shareholder_holder_path": "holder.parquet",
        "shareholder_holder_sha256": sha256_file(holder),
        "shareholder_top10_path": "top10.parquet",
        "shareholder_top10_sha256": sha256_file(top10),
    }}]
    research_path.write_text(yaml.safe_dump(research), encoding="utf-8")
    checkpoint_path = project / "checkpoints/w1.manifest.json"
    checkpoint = json.loads(checkpoint_path.read_text())
    checkpoint["identity"]["research_config"] = research
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
    checkpoint_set = _sha256_bytes(_canonical_bytes({
        "checkpoints": _checkpoint_set_payload([(checkpoint_path, checkpoint)])
    }))
    signal_path = project / "signal.json"
    signal = json.loads(signal_path.read_text())
    signal["window_checkpoint_set_sha256"] = checkpoint_set
    signal["feature_source_lineage"] = {
        "holder_num": {"path": "holder.parquet", "sha256": "0" * 64},
        "top10_holder_ratio": {"path": "top10.parquet", "sha256": sha256_file(top10)},
    }
    signal_path.write_text(json.dumps(signal), encoding="utf-8")
    backtest_path = project / "backtest.json"
    backtest = json.loads(backtest_path.read_text())
    backtest["signal_sources"][0]["manifest_sha256"] = sha256_file(signal_path)
    backtest_path.write_text(json.dumps(backtest), encoding="utf-8")
    request = yaml.safe_load(request_path.read_text(encoding="utf-8"))
    request["checkpoints"]["checkpoint_set_sha256"] = checkpoint_set
    request_path.write_text(yaml.safe_dump(request, sort_keys=False), encoding="utf-8")
    _refresh_request_identities(
        project, request_path, "research_config", "signal_manifest", "backtest_manifest",
    )
    with pytest.raises(CertificationError, match="holder_num signal sidecar sha256 mismatch"):
        certify_pit_baseline(
            request_path=request_path, audit_db=database, output_root=tmp_path / "shareholder",
            evidence_run_ids=["evidence-1"], project_root=project,
        )


def _configure_resolved_source_manifest(project: Path, request_path: Path) -> str:
    source_manifest = _write(project / "source-manifest.json", b"{}\n")
    source_hash = sha256_file(source_manifest)
    research_path = project / "research.yaml"
    research = yaml.safe_load(research_path.read_text())
    research["source_manifest_hash"] = source_hash
    research_path.write_text(yaml.safe_dump(research), encoding="utf-8")
    checkpoint_path = project / "checkpoints/w1.manifest.json"
    checkpoint = json.loads(checkpoint_path.read_text())
    checkpoint["identity"]["research_config"] = research
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
    checkpoint_set = _sha256_bytes(_canonical_bytes({
        "checkpoints": _checkpoint_set_payload([(checkpoint_path, checkpoint)])
    }))
    signal_path = project / "signal.json"
    signal = json.loads(signal_path.read_text())
    signal["source_manifest_hash"] = source_hash
    signal["window_checkpoint_set_sha256"] = checkpoint_set
    signal_path.write_text(json.dumps(signal), encoding="utf-8")
    backtest_path = project / "backtest.json"
    backtest = json.loads(backtest_path.read_text())
    backtest["signal_sources"][0]["manifest_sha256"] = sha256_file(signal_path)
    backtest["signal_sources"][0]["source_manifest_hash"] = source_hash
    backtest_path.write_text(json.dumps(backtest), encoding="utf-8")
    request = yaml.safe_load(request_path.read_text(encoding="utf-8"))
    request["source_manifest"] = {"path": "source-manifest.json", "sha256": source_hash}
    request["checkpoints"]["checkpoint_set_sha256"] = checkpoint_set
    request_path.write_text(yaml.safe_dump(request, sort_keys=False), encoding="utf-8")
    _refresh_request_identities(
        project, request_path, "research_config", "signal_manifest", "backtest_manifest",
    )
    return source_hash


def test_resolved_source_manifest_matches_all_backlinks(
    tiny_project: tuple[Path, Path, Path], tmp_path: Path,
) -> None:
    project, request_path, database = tiny_project
    _configure_resolved_source_manifest(project, request_path)
    result = certify_pit_baseline(
        request_path=request_path, audit_db=database, output_root=tmp_path / "resolved-source",
        evidence_run_ids=["evidence-1"], project_root=project,
    )
    assert result["status"] == "CERTIFIED"


@pytest.mark.parametrize("missing_owner", ["research", "signal", "backtest"])
def test_declared_source_manifest_requires_every_nonempty_backlink(
    tiny_project: tuple[Path, Path, Path], tmp_path: Path, missing_owner: str,
) -> None:
    project, request_path, database = tiny_project
    _configure_resolved_source_manifest(project, request_path)
    research_path = project / "research.yaml"
    signal_path = project / "signal.json"
    backtest_path = project / "backtest.json"
    if missing_owner == "research":
        research = yaml.safe_load(research_path.read_text(encoding="utf-8"))
        research.pop("source_manifest_hash")
        research_path.write_text(yaml.safe_dump(research), encoding="utf-8")
        checkpoint_path = project / "checkpoints/w1.manifest.json"
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        checkpoint["identity"]["research_config"] = research
        checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
        checkpoint_set = _sha256_bytes(_canonical_bytes({
            "checkpoints": _checkpoint_set_payload([(checkpoint_path, checkpoint)])
        }))
        signal = json.loads(signal_path.read_text(encoding="utf-8"))
        signal["window_checkpoint_set_sha256"] = checkpoint_set
        signal_path.write_text(json.dumps(signal), encoding="utf-8")
        backtest = json.loads(backtest_path.read_text(encoding="utf-8"))
        backtest["signal_sources"][0]["manifest_sha256"] = sha256_file(signal_path)
        backtest_path.write_text(json.dumps(backtest), encoding="utf-8")
        request = yaml.safe_load(request_path.read_text(encoding="utf-8"))
        request["checkpoints"]["checkpoint_set_sha256"] = checkpoint_set
        request_path.write_text(yaml.safe_dump(request, sort_keys=False), encoding="utf-8")
        _refresh_request_identities(
            project, request_path, "research_config", "signal_manifest", "backtest_manifest",
        )
    elif missing_owner == "signal":
        signal = json.loads(signal_path.read_text(encoding="utf-8"))
        signal.pop("source_manifest_hash")
        signal_path.write_text(json.dumps(signal), encoding="utf-8")
        backtest = json.loads(backtest_path.read_text(encoding="utf-8"))
        backtest["signal_sources"][0]["manifest_sha256"] = sha256_file(signal_path)
        backtest_path.write_text(json.dumps(backtest), encoding="utf-8")
        _refresh_request_identities(
            project, request_path, "signal_manifest", "backtest_manifest",
        )
    else:
        backtest = json.loads(backtest_path.read_text(encoding="utf-8"))
        backtest["signal_sources"][0]["source_manifest_hash"] = None
        backtest_path.write_text(json.dumps(backtest), encoding="utf-8")
        _refresh_request_identities(project, request_path, "backtest_manifest")
    with pytest.raises(CertificationError, match="source manifest backlinks missing"):
        certify_pit_baseline(
            request_path=request_path, audit_db=database,
            output_root=tmp_path / f"missing-{missing_owner}",
            evidence_run_ids=["evidence-1"], project_root=project,
        )


def test_formal_shareholder_dependencies_require_config_and_signal_lineage(
    tiny_project: tuple[Path, Path, Path], tmp_path: Path,
) -> None:
    project, request_path, database = tiny_project
    dependencies_path = project / "dependencies.yaml"
    dependencies = yaml.safe_load(dependencies_path.read_text(encoding="utf-8"))
    dependencies["features"][0]["dependencies"] = [
        {
            "source": "tushare", "dataset": "shareholder_holdernumber",
            "endpoint": "stk_holdernumber", "leaf_fields": ["holder_num"],
            "visibility": "announcement_date_asof", "pit_status": "evidence_required",
        },
        {
            "source": "tushare", "dataset": "shareholder_top10",
            "endpoint": "top10_holders", "leaf_fields": ["hold_ratio"],
            "visibility": "announcement_date_asof", "pit_status": "evidence_required",
        },
    ]
    dependencies_path.write_text(
        yaml.safe_dump(dependencies, sort_keys=False), encoding="utf-8",
    )
    with pytest.raises(CertificationError, match="shareholder sidecar lineage missing"):
        certify_pit_baseline(
            request_path=request_path, audit_db=database,
            output_root=tmp_path / "missing-shareholder-lineage",
            evidence_run_ids=["evidence-1"], project_root=project,
        )


def test_output_symlink_is_rejected(tiny_project: tuple[Path, Path, Path], tmp_path: Path) -> None:
    project, request, database = tiny_project
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(CertificationError, match="symlink"):
        certify_pit_baseline(
            request_path=request, audit_db=database, output_root=alias,
            evidence_run_ids=["evidence-1"], project_root=project,
        )


def test_certifier_has_no_producer_or_workflow_imports() -> None:
    imports = set()
    for path in ("qsys/pit_certification.py", "qsys/pit_datapack.py"):
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        imports.update(
            node.module for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
    assert "qsys.data.source_audit" not in imports
    assert "qsys.data.collector" not in imports
    assert not any(name.startswith("qsys.research") or name.startswith("qsys.backtest") for name in imports)
    probe = subprocess.run(
        [sys.executable, "-c", (
            "import sys; import qsys.pit_certification; "
            "assert 'qsys.data.collector' not in sys.modules; "
            "assert 'qsys.data.storage' not in sys.modules"
        )],
        cwd=ROOT, check=False, capture_output=True, text=True,
    )
    assert probe.returncode == 0, probe.stderr


def test_real_shareholder_dependencies_remain_fail_closed() -> None:
    dependencies = yaml.safe_load(REAL_DEPENDENCIES.read_text(encoding="utf-8"))
    shareholder = [
        dependency
        for feature in dependencies["features"]
        for dependency in feature["dependencies"]
        if str(dependency["dataset"]).startswith("shareholder_")
    ]

    assert shareholder
    assert all(
        "SHAREHOLDER_REVISION_CAPABILITY_UNVERIFIED"
        in dependency.get("blocker_codes", [])
        for dependency in shareholder
    )


def test_real_financial_dependencies_require_latest_known_revision_capability() -> None:
    dependencies = yaml.safe_load(REAL_DEPENDENCIES.read_text(encoding="utf-8"))
    financial = [
        dependency
        for feature in dependencies["features"]
        for dependency in feature["dependencies"]
        if dependency["endpoint"] == "fina_indicator"
    ]

    assert financial
    assert dependencies["semantic_blockers"] == [{
        "code": "FINANCIAL_LATEST_KNOWN_REVISION_CAPABILITY_UNVERIFIED",
        "required_contract": "financial_latest_known_actual_publication_v1",
        "endpoints": ["income", "balancesheet", "cashflow", "fina_indicator"],
        "reason": (
            "current first-available projection does not apply later public revisions, "
            "and same-announcement fina_indicator revisions lack an independently "
            "proven effective date"
        ),
    }]
