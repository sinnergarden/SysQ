from __future__ import annotations

import ast
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
REAL_REQUEST = yaml.safe_load(
    (ROOT / "configs/audit/csi1800_s180_baseline_v1_r1.yaml").read_text(encoding="utf-8")
)


def test_receipt_shards_must_cover_the_instrument_interval_without_gap() -> None:
    def proof(left: str, right: str, symbols: list[str]) -> dict:
        return {"receipt": {"requested_scope": {
            "date_start": left, "date_end": right, "symbols": symbols,
        }}}

    scope = {
        "instrument": "000001.SZ", "date_start": "20200101", "date_end": "20201231",
    }
    covered = _proofs_cover_scope([
        proof("20200101", "20200630", ["000001.SZ"]),
        proof("20200630", "20201231", ["000001.SZ"]),
        proof("20200101", "20201231", ["000002.SZ"]),
    ], scope)
    assert len(covered) == 2
    assert _proofs_cover_scope([
        proof("20200101", "20200629", ["000001.SZ"]),
        proof("20200701", "20201231", ["000001.SZ"]),
    ], scope) == []


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
    assert set(income["dependencies"][0]["blocker_codes"]) == {
        "UNBOUND_SOURCE_ARTIFACT", "REVISION_VISIBILITY_UNPROVEN",
    }
    industry = next(row for row in dependencies["features"] if row["feature"] == "rps_industry_60d")
    classification = next(row for row in industry["dependencies"] if row["endpoint"] == "stock_basic")
    assert classification["source"] == "tushare"
    assert classification["dataset"] == "stock_basic_classification"
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
    scope = load_checkpoint_scope(
        checkpoint_root=ROOT / REAL_REQUEST["checkpoints"]["path"],
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
