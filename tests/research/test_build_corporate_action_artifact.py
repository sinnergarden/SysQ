"""Independent tests for the signal-independent corporate-action builder."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from zipfile import ZipFile

import pandas as pd
import pytest

from qsys.backtest.accounting import CorporateActionStore
import scripts.research.build_corporate_action_artifact as builder
from scripts.research.build_corporate_action_artifact import (
    BuildError,
    build_corporate_action_artifact,
)


RAW_COLUMNS = [
    "ts_code", "end_date", "ann_date", "div_proc", "stk_div",
    "stk_bo_rate", "stk_co_rate", "cash_div", "cash_div_tax",
    "record_date", "ex_date", "pay_date", "div_listdate", "imp_ann_date",
]


def _raw_row(**overrides):
    row = {
        "ts_code": "000426.SZ",
        "end_date": "20221231",
        "ann_date": "20230630",
        "div_proc": "实施",
        "stk_div": 0.0,
        "stk_bo_rate": None,
        "stk_co_rate": None,
        "cash_div": 0.017,
        "cash_div_tax": 0.017,
        "record_date": "20230828",
        "ex_date": "20230829",
        "pay_date": "20230829",
        "div_listdate": None,
        "imp_ann_date": "20230823",
    }
    row.update(overrides)
    return row


def _write_input_bundle(path: Path, rows: list[dict]) -> None:
    frame = pd.DataFrame(rows, columns=RAW_COLUMNS)
    payload = io.BytesIO()
    frame.to_parquet(payload, index=False)
    with ZipFile(path, "w") as archive:
        archive.writestr("raw_all_market.parquet", payload.getvalue())
        # The builder intentionally does not copy this timestamp-bearing file.
        archive.writestr("query_coverage.json", '{"created_at":"test-only"}')


def _source_zip(target: Path) -> Path:
    source = target / "input.zip"
    _write_input_bundle(
        source,
        [
            _raw_row(),
            _raw_row(
                ts_code="000001.SZ",
                ex_date="20230830",
                ann_date="20230801",
                imp_ann_date="20230802",
                record_date="20230829",
                pay_date="20230830",
            ),
        ],
    )
    return source


def _read_source_artifact(target: Path) -> tuple[dict, dict, bytes]:
    source = next((target / "source").glob("*.zip"))
    with ZipFile(source) as archive:
        manifest = json.loads(archive.read("build_manifest.json"))
        coverage = json.loads(archive.read("raw_coverage.json"))
        return manifest, coverage, source.read_bytes()


def _assert_no_created_at(value):
    if isinstance(value, dict):
        assert "created_at" not in value
        for item in value.values():
            _assert_no_created_at(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_created_at(item)


def test_builder_is_signal_independent_and_filters_exact_dates(tmp_path):
    source = _source_zip(tmp_path)
    target = build_corporate_action_artifact(
        source,
        tmp_path / "research",
        artifact_name="all-actions",
        start_date="2023-08-29",
        end_date="2023-08-29",
    )

    events = CorporateActionStore(tmp_path / "research", "all-actions").events
    assert set(events["instrument"]) == {"000426.SZ"}
    assert len(events) == 1
    assert events.iloc[0]["event_type"] == "cash_dividend"
    assert events.iloc[0]["cash_per_share"] == pytest.approx(0.017)
    manifest, coverage, _ = _read_source_artifact(target)
    assert coverage["input_raw_row_count"] == 2
    assert coverage["date_filtered_raw_row_count"] == 1
    assert coverage["filtered_raw_row_count"] == 1
    assert manifest["pit_filter"]["signal_independent"] is True
    with ZipFile(io.BytesIO(_read_source_artifact(target)[2])) as archive:
        original = pd.read_parquet(io.BytesIO(archive.read("raw_all_market.parquet")))
        filtered = pd.read_parquet(io.BytesIO(archive.read("pit_filtered_raw.parquet")))
        assert len(original) == 2
        assert len(filtered) == 1
        assert "candidate_filter.json" not in archive.namelist()
        assert "candidate_raw.jsonl" not in archive.namelist()
        assert json.loads(archive.read("query_coverage.json")) == {}


def test_rejection_fails_closed_by_default(tmp_path):
    source = tmp_path / "input.zip"
    _write_input_bundle(
        source,
        [_raw_row(stk_div=1.0, stk_co_rate=1.0, cash_div=0.0,
                  cash_div_tax=0.0, div_listdate=None)],
    )
    with pytest.raises(BuildError, match="rejected"):
        build_corporate_action_artifact(
            source,
            tmp_path / "research",
            artifact_name="rejects",
            start_date="2023-08-29",
            end_date="2023-08-29",
        )
    assert not (tmp_path / "research" / "corporate_actions" / "rejects").exists()


def test_allow_rejections_quarantines_audit_and_keeps_valid_cash(tmp_path):
    source = tmp_path / "input.zip"
    _write_input_bundle(
        source,
        [
            _raw_row(),
            _raw_row(ts_code="000002.SZ", stk_div=1.0, stk_co_rate=1.0,
                     cash_div=0.0, cash_div_tax=0.0, div_listdate=None),
        ],
    )
    target = build_corporate_action_artifact(
        source,
        tmp_path / "research",
        artifact_name="quarantine",
        start_date="2023-08-29",
        end_date="2023-08-29",
        allow_rejections=True,
    )
    manifest, coverage, source_bytes = _read_source_artifact(target)
    assert coverage["filtered_raw_row_count"] == 2
    assert coverage["rejected_raw_row_count"] == 1
    assert len(manifest["rejections"]) == 1
    assert manifest["rejections"][0]["raw_row_hash"]
    assert "div_listdate" in manifest["rejections"][0]["reason"]
    assert manifest["input_bundle_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest["pit_universe_identity"] is None
    with ZipFile(io.BytesIO(source_bytes)) as archive:
        rejection = json.loads(archive.read("rejections.json"))
        assert rejection == manifest["rejections"]
        for name in archive.namelist():
            if name.endswith(".json"):
                _assert_no_created_at(json.loads(archive.read(name)))
    events = CorporateActionStore(tmp_path / "research", "quarantine").events
    assert set(events["instrument"]) == {"000426.SZ"}


def test_existing_artifact_is_not_overwritten(tmp_path):
    source = _source_zip(tmp_path)
    research = tmp_path / "research"
    target = build_corporate_action_artifact(
        source, research, artifact_name="immutable", start_date="2023-08-29", end_date="2023-08-29"
    )
    before = {
        path.relative_to(target).as_posix(): path.read_bytes()
        for path in target.rglob("*") if path.is_file()
    }
    with pytest.raises(FileExistsError):
        build_corporate_action_artifact(
            source, research, artifact_name="immutable", start_date="2023-08-29", end_date="2023-08-29"
        )
    after = {
        path.relative_to(target).as_posix(): path.read_bytes()
        for path in target.rglob("*") if path.is_file()
    }
    assert after == before


def test_source_bundle_hash_is_deterministic_without_created_at(tmp_path):
    source = _source_zip(tmp_path)
    research = tmp_path / "research"
    first = build_corporate_action_artifact(
        source, research, artifact_name="deterministic-a", start_date="2023-08-29", end_date="2023-08-29"
    )
    second = build_corporate_action_artifact(
        source, research, artifact_name="deterministic-b", start_date="2023-08-29", end_date="2023-08-29"
    )
    _, _, first_bytes = _read_source_artifact(first)
    _, _, second_bytes = _read_source_artifact(second)
    assert first_bytes == second_bytes
    assert hashlib.sha256(first_bytes).hexdigest() == hashlib.sha256(second_bytes).hexdigest()


def test_pit_filter_is_rowwise_and_audited(tmp_path, monkeypatch):
    source = _source_zip(tmp_path)

    class FakePitStore:
        def is_member(self, instrument, as_of_date):
            return instrument == "000426.SZ" and as_of_date == "2023-08-29"

    identity = {
        "universe_id": "fake-pit",
        "membership_sha256": "m" * 64,
    }
    monkeypatch.setattr(
        builder,
        "_pit_context",
        lambda _: (FakePitStore(), identity, "u" * 64),
    )
    target = build_corporate_action_artifact(
        source,
        tmp_path / "research",
        artifact_name="pit-filtered",
        start_date="2023-08-29",
        end_date="2023-08-30",
        pit_universe_artifact="fake",
    )
    manifest, coverage, source_bytes = _read_source_artifact(target)
    assert coverage["date_filtered_raw_row_count"] == 2
    assert coverage["filtered_raw_row_count"] == 1
    assert manifest["pit_filter"] == {
        "enabled": True,
        "rule": "member_on_ex_date",
        "input_row_count": 2,
        "output_row_count": 1,
        "drop_row_count": 1,
        "universe_id": "fake-pit",
        "universe_manifest_sha256": "u" * 64,
        "membership_sha256": "m" * 64,
        "signal_independent": True,
    }
    with ZipFile(io.BytesIO(source_bytes)) as archive:
        assert json.loads(archive.read("pit_filter.json")) == manifest["pit_filter"]
