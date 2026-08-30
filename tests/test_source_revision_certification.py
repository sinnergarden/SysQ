from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

import qsys.pit_certification as pit_certification
from qsys.pit_certification import (
    CertificationError,
    _audit_financial_revision_terminal,
    _financial_asof_samples,
    _latest_shareholder_vintage_values,
    _lineage_path_matches,
    _validate_r3_source_blockers,
    _validate_source_revision_count_contract,
    audit_source_revision_capabilities,
    classify_financial_revision_events,
    classify_shareholder_vintage_events,
    resolve_financial_events_as_of,
    sha256_file,
)


ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _frozen_r3_blocker_fixture(project: Path) -> tuple[dict[str, object], dict[str, object]]:
    predictions = project / "predictions.parquet"
    predictions.write_bytes(b"frozen predictions")
    signal_manifest_source = _write_json(
        project / "signal-manifest.json",
        {"predictions_file": "predictions.parquet", "predictions_sha256": sha256_file(predictions)},
    )
    signal_manifest_sha = sha256_file(signal_manifest_source)
    signal_dir = (
        project / "data/research/frozen_outputs/signal_runs" / signal_manifest_sha
    )
    signal_dir.mkdir(parents=True)
    signal_manifest = signal_dir / "manifest.json"
    signal_manifest.write_bytes(signal_manifest_source.read_bytes())
    signal_predictions = signal_dir / "predictions.parquet"
    signal_predictions.write_bytes(predictions.read_bytes())

    backtest_source = _write_json(project / "backtest-manifest.json", {"artifacts": {}})
    backtest_sha = sha256_file(backtest_source)
    backtest_dir = (
        project / "data/research/frozen_outputs/backtest_runs" / backtest_sha
    )
    backtest_dir.mkdir(parents=True)
    backtest_manifest = backtest_dir / "manifest.json"
    backtest_manifest.write_bytes(backtest_source.read_bytes())

    exceptions = project / "exceptions.parquet"
    pd.DataFrame([
        {
            "reason_code": "SHAREHOLDER_REVISION_CAPABILITY_UNVERIFIED",
            "affected_features_json": json.dumps(["holder_num"]),
        },
        {
            "reason_code": "FINANCIAL_LATEST_KNOWN_REVISION_CAPABILITY_UNVERIFIED",
            "affected_features_json": json.dumps(["roe"]),
        },
    ]).to_parquet(exceptions, index=False)
    audit_id = "a" * 64
    receipt = _write_json(project / "audit_receipt.json", {
        "audit_id": audit_id,
        "baseline_status": "BLOCKED",
        "artifacts": {"exceptions.parquet": sha256_file(exceptions)},
        "input_identities": {"identities": {
            "signal_manifest": {
                "path": signal_manifest.relative_to(project).as_posix(),
                "sha256": signal_manifest_sha,
            },
            "signal_predictions": {
                "path": signal_predictions.relative_to(project).as_posix(),
                "sha256": sha256_file(signal_predictions),
            },
            "backtest_manifest": {
                "path": backtest_manifest.relative_to(project).as_posix(),
                "sha256": backtest_sha,
            },
        }},
    })
    spec = {
        "audit_id": audit_id,
        "audit_receipt": {
            "path": receipt.relative_to(project).as_posix(),
            "sha256": sha256_file(receipt),
        },
        "exceptions": {
            "path": exceptions.relative_to(project).as_posix(),
            "sha256": sha256_file(exceptions),
        },
        "expected": {
            "exception_rows": 2,
            "shareholder_unique_features": 1,
            "financial_unique_features": 1,
        },
    }
    return spec, receipt


def _income_row(
    *, ann_date: str, update_flag: str, n_income: float,
    f_ann_date: str | None = None,
) -> dict[str, object]:
    return {
        "ts_code": "000001.SZ",
        "end_date": "20231231",
        "report_type": "1",
        "comp_type": "1",
        "end_type": "4",
        "ann_date": ann_date,
        "f_ann_date": f_ann_date,
        "update_flag": update_flag,
        "n_income": n_income,
        "revenue": 100.0,
        "oper_cost": 60.0,
    }


def test_financial_same_publication_conflict_is_blocked() -> None:
    raw = pd.DataFrame([
        _income_row(ann_date="20240131", update_flag="0", n_income=10.0),
        _income_row(ann_date="20240131", update_flag="1", n_income=11.0),
    ])

    events, exceptions, stats = classify_financial_revision_events(
        raw, endpoint="income", availability_cutoff="20240731",
    )

    assert stats["complete_keys"] == 0
    assert stats["blocked_keys"] == 1
    assert stats["same_publication_conflict_keys"] == 1
    assert events.empty
    assert exceptions["reason_code"].tolist() == [
        "SAME_PUBLICATION_VALUE_CONFLICT"
    ]


def test_financial_later_flag_zero_does_not_repair_missing_initial() -> None:
    raw = pd.DataFrame([
        _income_row(ann_date="20230429", update_flag="1", n_income=10.0),
        _income_row(ann_date="20230714", update_flag="0", n_income=11.0),
    ])

    events, exceptions, stats = classify_financial_revision_events(
        raw, endpoint="income", availability_cutoff="20240731",
    )

    assert stats["complete_keys"] == 0
    assert stats["missing_initial_keys"] == 1
    assert stats["proven_events"] == 0
    assert events["capability_status"].eq("BLOCKED_INCOMPLETE_KEY").all()
    assert events["event_kind"].tolist() == [
        "RIGHT_CENSORED_FIRST_OBSERVED",
        "UNORDERED_REVISION_CANDIDATE",
    ]
    assert "INITIAL_PUBLICATION_VALUE_MISSING" in set(exceptions["reason_code"])


def test_financial_flag_one_with_later_final_date_is_still_right_censored() -> None:
    raw = pd.DataFrame([
        _income_row(
            ann_date="20230429", f_ann_date="20230512",
            update_flag="1", n_income=10.0,
        ),
    ])

    events, exceptions, stats = classify_financial_revision_events(
        raw, endpoint="income", availability_cutoff="20240731",
    )

    assert stats["complete_keys"] == 0
    assert stats["missing_initial_keys"] == 1
    assert events["publication_date"].tolist() == ["20230512"]
    assert events["event_kind"].tolist() == ["RIGHT_CENSORED_FIRST_OBSERVED"]
    assert events["capability_status"].eq("BLOCKED_INCOMPLETE_KEY").all()
    assert exceptions["reason_code"].tolist() == [
        "INITIAL_PUBLICATION_VALUE_MISSING"
    ]


def test_financial_cutoff_and_equal_same_day_duplicates() -> None:
    raw = pd.DataFrame([
        _income_row(ann_date="20240131", update_flag="0", n_income=10.0),
        _income_row(ann_date="20240131", update_flag="1", n_income=10.0),
        _income_row(ann_date="20240229", update_flag="1", n_income=10.0),
        _income_row(ann_date="20240801", update_flag="1", n_income=11.0),
    ])

    events, exceptions, stats = classify_financial_revision_events(
        raw, endpoint="income", availability_cutoff="20240731",
    )

    assert exceptions.empty
    assert stats["complete_keys"] == 1
    assert stats["excluded_future_rows"] == 1
    assert stats["equivalent_rows_collapsed"] == 1
    assert events["publication_date"].tolist() == ["20240131", "20240229"]
    assert events["event_kind"].tolist() == [
        "INITIAL_PUBLICATION", "EQUIVALENT_REPUBLICATION",
    ]
    assert stats["proven_revision_events"] == 0


def test_financial_missing_end_type_is_reported_outside_revision_keys() -> None:
    row = _income_row(ann_date="20240131", update_flag="0", n_income=10.0)
    row["end_type"] = None

    events, exceptions, stats = classify_financial_revision_events(
        pd.DataFrame([row]), endpoint="income", availability_cutoff="20240731",
    )

    assert events.empty
    assert exceptions.empty
    assert stats["logical_keys"] == 0
    assert stats["excluded_missing_end_type_keys"] == 1
    assert stats["excluded_missing_end_type_rows"] == 1


def test_financial_terminal_accepts_all_empty_endpoint_receipts(tmp_path) -> None:
    terminal = {
        "run_id": "empty-financial-terminal",
        "range_end": "20240731",
        "fetch_receipts": [
            {"endpoint": endpoint, "status": "empty", "payload_path": None}
            for endpoint in ("income", "balancesheet", "cashflow", "fina_indicator")
        ],
    }

    events, exceptions, summary = _audit_financial_revision_terminal(
        terminal=terminal,
        data_root=tmp_path,
        availability_cutoff="20240731",
    )

    assert events.empty
    assert exceptions.empty
    assert all(
        endpoint_summary["receipt_count"] == 1
        for endpoint_summary in summary["endpoint_summaries"].values()
    )


def test_financial_revision_visibility_uses_strict_trade_boundary() -> None:
    raw = pd.DataFrame([
        _income_row(ann_date="20230131", update_flag="0", n_income=10.0),
        _income_row(ann_date="20240131", update_flag="1", n_income=11.0),
    ])
    events, exceptions, stats = classify_financial_revision_events(
        raw, endpoint="income", availability_cutoff="20240731",
    )
    assert exceptions.empty
    assert stats["proven_revision_events"] == 1

    on_publication = resolve_financial_events_as_of(
        events, trade_date="20240131",
    )
    first_later_trade = resolve_financial_events_as_of(
        events, trade_date="20240201",
    )
    assert on_publication.iloc[0]["value_sha256"] == events.iloc[0]["value_sha256"]
    assert first_later_trade.iloc[0]["value_sha256"] == events.iloc[1]["value_sha256"]

    samples = _financial_asof_samples(
        events, trade_dates=["20230131", "20230201", "20240131", "20240201"],
    )
    assert samples["trade_date"].tolist() == ["20240131", "20240201"]
    assert samples["status"].eq("PASS").all()


def test_shareholder_event_key_keeps_end_date_and_vintage() -> None:
    raw = pd.DataFrame([
        {
            "kind": "holder_num", "inst": "000001.SZ",
            "ann_date": "20240131", "end_date": "20230930",
            "value_sha256": "a", "vintage_id": "v1",
        },
        {
            "kind": "holder_num", "inst": "000001.SZ",
            "ann_date": "20240131", "end_date": "20231231",
            "value_sha256": "b", "vintage_id": "v1",
        },
        {
            "kind": "holder_num", "inst": "000001.SZ",
            "ann_date": "20240131", "end_date": "20231231",
            "value_sha256": "c", "vintage_id": "v2",
        },
    ])

    annotated, distinct = classify_shareholder_vintage_events(raw)

    assert len(distinct) == 2
    older = annotated.loc[annotated["end_date"].eq("20230930")].iloc[0]
    latest = annotated.loc[annotated["end_date"].eq("20231231")]
    assert older["vintage_count"] == 1
    assert latest["vintage_count"].eq(2).all()
    assert latest["revision_visibility_status"].eq(
        "OBSERVED_REVISION_UPPER_BOUND_ONLY"
    ).all()


def test_shareholder_duplicate_exact_fact_vintage_fails_closed() -> None:
    row = {
        "kind": "top10_holder_ratio", "inst": "000001.SZ",
        "ann_date": "20240131", "end_date": "20231231",
        "value_sha256": "a", "vintage_id": "v1",
    }
    with pytest.raises(CertificationError, match="duplicate exact"):
        classify_shareholder_vintage_events(pd.DataFrame([row, row]))


def test_legacy_comparator_uses_latest_observed_shareholder_vintage() -> None:
    base = {
        "kind": "holder_num", "inst": "000001.SZ",
        "ann_date": "20240131", "end_date": "20231231",
    }
    current = pd.DataFrame([
        {**base, "value": 10.0, "value_sha256": "a", "vintage_id": "v1",
         "observed_at": "2026-08-29T10:00:00Z"},
        {**base, "value": 11.0, "value_sha256": "b", "vintage_id": "v2",
         "observed_at": "2026-08-30T10:00:00Z"},
    ])

    latest = _latest_shareholder_vintage_values(current)

    assert latest["value"].tolist() == [11.0]


def test_legacy_comparator_rejects_same_observation_time_conflict() -> None:
    base = {
        "kind": "holder_num", "inst": "000001.SZ",
        "ann_date": "20240131", "end_date": "20231231",
        "observed_at": "2026-08-30T10:00:00Z",
    }
    current = pd.DataFrame([
        {**base, "value": 10.0, "value_sha256": "a", "vintage_id": "v1"},
        {**base, "value": 11.0, "value_sha256": "b", "vintage_id": "v2"},
    ])

    with pytest.raises(CertificationError, match="conflict at one observation"):
        _latest_shareholder_vintage_values(current)


def test_source_revision_count_contract_rejects_false_green() -> None:
    endpoint = {
        "logical_keys": 2, "complete_keys": 1, "blocked_keys": 1,
        "missing_initial_keys": 1, "same_publication_conflict_keys": 0,
        "proven_events": 1, "proven_revision_events": 0,
        "excluded_missing_end_type_keys": 0, "excluded_future_rows": 0,
    }
    financial = {
        "endpoint_summaries": {"income": endpoint},
        "complete_key_count": 1, "blocked_key_count": 1,
        "proven_event_count": 1, "proven_revision_event_count": 0,
    }
    shareholder = {
        "source_vintage_count": 1, "unique_event_key_count": 3,
        "actual_publication_timestamp_event_key_count": 0,
    }
    expected_financial = {
        "logical_keys": 2, "complete_keys": 1, "blocked_keys": 1,
        "right_censored_keys": 1, "same_publication_conflict_keys": 0,
        "orderable_events": 1, "value_revisions": 0,
        "excluded_missing_end_type_keys": 0, "excluded_future_rows": 0,
        "by_endpoint": {
            "income": {
                "logical_keys": 2, "complete_keys": 1, "blocked_keys": 1,
                "right_censored_keys": 1,
                "same_publication_conflict_keys": 0,
                "orderable_events": 1, "value_revisions": 0,
            }
        },
    }
    request = {
        "financial": {"expected_r3_counts": expected_financial},
        "shareholder": {"expected_r3_counts": {
            "source_vintages": 1, "exact_event_keys": 3,
            "historical_revision_timeline_proven_keys": 0,
            "asof_samples": 2,
        }},
    }

    result = _validate_source_revision_count_contract(
        request=request, financial=financial, shareholder=shareholder,
        sample_count=2,
    )
    assert result["status"] == "PASS"

    request["financial"]["expected_r3_counts"] = {
        **expected_financial, "blocked_keys": 0,
    }
    with pytest.raises(CertificationError, match="blocked_keys"):
        _validate_source_revision_count_contract(
            request=request, financial=financial, shareholder=shareholder,
            sample_count=2,
        )


def test_source_revision_rejects_replaced_upstream_r3_output(tmp_path: Path) -> None:
    spec, receipt_path = _frozen_r3_blocker_fixture(tmp_path)
    context = _validate_r3_source_blockers(
        project=tmp_path, spec=spec, feature_date_end="20260731",
    )
    predictions_spec = context["output_identities"]["signal_predictions"]
    assert predictions_spec["sha256"] == sha256_file(
        tmp_path / predictions_spec["path"]
    )

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    predictions_path = (
        tmp_path
        / receipt["input_identities"]["identities"]["signal_predictions"]["path"]
    )
    predictions_path.write_bytes(b"replacement")
    with pytest.raises(CertificationError, match="output signal_predictions sha256 mismatch"):
        _validate_r3_source_blockers(
            project=tmp_path, spec=spec, feature_date_end="20260731",
        )


def test_source_revision_output_is_create_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    r3_spec, _receipt = _frozen_r3_blocker_fixture(project)
    calendar = project / "calendar.txt"
    calendar.write_text("2026-07-30\n2026-07-31\n", encoding="utf-8")
    contract = project / "source-contract.yaml"
    contract.write_text("schema_version: test\n", encoding="utf-8")
    database = project / "audit.db"
    database.write_bytes(b"read-only database fixture")
    request_path = project / "request.yaml"
    request_path.write_text(yaml.safe_dump({
        "schema_version": "source_revision_audit_request_v1",
        "audit_name": "create_only",
        "scope": {"feature_date_end": "20260731"},
        "upstream_r3_certification": r3_spec,
        "trade_calendar": {
            "path": calendar.relative_to(project).as_posix(),
            "sha256": sha256_file(calendar),
        },
        "source_contract": {
            "path": contract.relative_to(project).as_posix(),
            "sha256": sha256_file(contract),
        },
        "financial": {"terminal": {"run_id": "financial", "sha256": "b" * 64}},
        "shareholder": {"vintages": [{}]},
    }, sort_keys=False), encoding="utf-8")

    empty_exceptions = pd.DataFrame(
        columns=pit_certification.SOURCE_REVISION_EXCEPTION_COLUMNS
    )
    monkeypatch.setattr(
        pit_certification, "_load_revision_terminal",
        lambda **_kwargs: ({"run_id": "financial", "range_end": "20260731"}, "b" * 64),
    )
    monkeypatch.setattr(
        pit_certification, "_audit_financial_revision_terminal",
        lambda **_kwargs: (
            pd.DataFrame(columns=pit_certification.FINANCIAL_EVENT_COLUMNS),
            empty_exceptions,
            {"proven_event_count": 0},
        ),
    )
    monkeypatch.setattr(
        pit_certification, "_audit_shareholder_vintages",
        lambda **_kwargs: (
            pd.DataFrame(columns=pit_certification.SHAREHOLDER_VINTAGE_COLUMNS),
            empty_exceptions,
            {},
        ),
    )
    monkeypatch.setattr(
        pit_certification, "_shareholder_legacy_comparator_deltas",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        pit_certification, "_financial_asof_samples",
        lambda *_args, **_kwargs: pd.DataFrame([{"status": "PASS"}]),
    )
    monkeypatch.setattr(
        pit_certification, "_validate_source_revision_count_contract",
        lambda **_kwargs: {"status": "PASS"},
    )
    monkeypatch.setattr(
        pit_certification, "_source_revision_report",
        lambda **_kwargs: "source revision report\n",
    )
    implementation = project / "qsys/pit_certification.py"
    implementation.parent.mkdir(parents=True)
    implementation.write_text("# fixture implementation\n", encoding="utf-8")
    entrypoint = project / "scripts/research/certify_pit_baseline.py"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("# fixture entrypoint\n", encoding="utf-8")
    monkeypatch.setattr(pit_certification, "__file__", str(implementation))

    output_root = project / "outputs"
    first = audit_source_revision_capabilities(
        request_path=request_path,
        audit_db=database,
        output_root=output_root,
        project_root=project,
    )
    assert first["status"] == "CERTIFIED"
    with pytest.raises(FileExistsError, match="output already exists"):
        audit_source_revision_capabilities(
            request_path=request_path,
            audit_db=database,
            output_root=output_root,
            project_root=project,
        )


def test_historical_r3_request_uses_frozen_source_contract() -> None:
    request = yaml.safe_load(
        (ROOT / "configs/audit/csi1800_s180_baseline_v1_r3.yaml").read_text(
            encoding="utf-8"
        )
    )
    contract = request["portable_datapack"]["source_contracts"][0]
    assert contract == {
        "path": (
            "docs/requirements/contracts/frozen/"
            "ca61a95d9226930b7da0f7a9379c60a3978a094f77cbff396f72601704caae08/"
            "tushare_daily.yaml"
        ),
        "sha256": "ca61a95d9226930b7da0f7a9379c60a3978a094f77cbff396f72601704caae08",
    }
    assert sha256_file(ROOT / contract["path"]) == contract["sha256"]
    assert sha256_file(ROOT / "docs/requirements/contracts/tushare_daily.yaml") != (
        contract["sha256"]
    )


def test_lineage_path_is_portable_across_checkout_prefixes(tmp_path: Path) -> None:
    project = tmp_path / "new-checkout"
    artifact = project / "data/research/source_snapshots/value.parquet"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"value")

    assert _lineage_path_matches(
        project, "/old/checkout/data/research/source_snapshots/value.parquet", artifact,
    )
    assert _lineage_path_matches(
        project, "data/research/source_snapshots/value.parquet", artifact,
    )
    assert not _lineage_path_matches(project, "../value.parquet", artifact)
    assert not _lineage_path_matches(
        project, "/old/checkout/data/research/source_snapshots/other.parquet", artifact,
    )
