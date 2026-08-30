from __future__ import annotations

import json
import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from qsys.data.collector import TushareCollector
from qsys.data.source_audit import SourceAuditStore
from qsys.ops.shareholder_sync import (
    AUDITED_SNAPSHOT_CONTRACT,
    ShareholderProjectionError,
    _load_audited_shareholder_payload,
    _calendar_year_chunks,
    _paged_call,
    fetch_shareholder_backfill,
    inspect_shareholder_sidecar_health,
    materialize_audited_shareholder_snapshot,
    merge_shareholder_rows,
    normalise_holder_rows,
    normalise_top10_rows,
    run_shareholder_history_repair,
)


CONTRACT = {
    "source": "tushare.stk_holdernumber+tushare.top10_holders",
    "availability_rule": "announcement_date_asof",
    "min_coverage": 1.0,
    "features": {
        "holder_num_stale_days": {"max_median_days": 200, "max_row_days": 365},
        "top10_holder_stale_days": {"max_median_days": 250, "max_row_days": 365},
    },
}


def test_historical_shareholder_pages_emit_exact_durable_receipts(tmp_path: Path) -> None:
    holder = pd.DataFrame({
        "ts_code": ["000001.SZ"], "ann_date": ["20200430"],
        "end_date": ["20191231"], "holder_num": [1000],
    })
    top10 = pd.DataFrame({
        "ts_code": ["000001.SZ"], "ann_date": ["20200430"],
        "end_date": ["20191231"], "holder_name": ["holder"],
        "hold_ratio": [10.0],
    })
    top10_calls: list[dict[str, object]] = []

    def top10_api(**kwargs: object) -> pd.DataFrame:
        top10_calls.append(kwargs)
        return top10.copy() if kwargs.get("ann_date") == "20200430" else pd.DataFrame()

    collector = TushareCollector.__new__(TushareCollector)
    collector.max_retries = 1
    collector._collector_interfaces = {}
    collector.pro = SimpleNamespace(
        stk_holdernumber=lambda **_kwargs: holder.copy(),
        top10_holders=top10_api,
    )
    audit_root = tmp_path / "audit"
    audit = SourceAuditStore(audit_root / "audit.db")
    run_id = "shareholder-history"
    audit.append_event(run_id, "run_started", {
        "entrypoint": "scripts/data_sync.py", "universe": "csi1800",
        "target_date": "20200430", "range_start": "20200429",
    })

    holder_rows, top10_rows, fetch = fetch_shareholder_backfill(
        collector,
        start_date="2020-04-29",
        end_date="2020-04-30",
        run_id=run_id,
        audit_store=audit,
        scope_key="csi1800",
        universe="csi1800",
        evidence_symbols=["000001.SZ", "000002.SZ"],
    )

    assert len(holder_rows) == 1
    assert len(top10_rows) == 1
    assert [call["ann_date"] for call in top10_calls] == ["20200429", "20200430"]
    assert all("start_date" not in call and "end_date" not in call for call in top10_calls)
    assert fetch["quarter_periods"] == []
    assert fetch["top10_announcement_chunks"] == [{
        "start_date": "2020-04-29",
        "end_date": "2020-04-30",
        "request_count": 2,
        "rows": 1,
    }]
    with sqlite3.connect(audit_root / "audit.db") as conn:
        receipts = conn.execute(
            "SELECT endpoint,status,requested_scope_json,payload_path,response_date_max "
            "FROM fetch_receipts "
            "WHERE run_id=? ORDER BY rowid",
            (run_id,),
        ).fetchall()
        links = set(conn.execute(
            "SELECT dataset,field_name FROM field_receipt_links WHERE run_id=?",
            (run_id,),
        ).fetchall())
    assert {row[0] for row in receipts} == {"stk_holdernumber", "top10_holders"}
    for _, _, scope_json, _, _ in receipts:
        scope = json.loads(scope_json)
        assert scope["symbols"] == ["000001.SZ", "000002.SZ"]
        assert scope["symbol_count"] == 2
        assert scope["checkpoint_key"]
        assert "offset=" in scope["request_variant"]
    assert any(row[1] == "success" and row[3] for row in receipts)
    top10_receipt = next(
        row for row in receipts if row[0] == "top10_holders" and row[1] == "success"
    )
    empty_top10_receipt = next(
        row for row in receipts if row[0] == "top10_holders" and row[1] == "empty"
    )
    empty_top10_scope = json.loads(empty_top10_receipt[2])
    assert empty_top10_scope["date_start"] == empty_top10_scope["date_end"] == "20200429"
    assert empty_top10_scope["checkpoint_key"]
    assert empty_top10_receipt[3] is None
    assert top10_receipt[4] == "20200430"
    top10_scope = json.loads(top10_receipt[2])
    assert top10_scope["date_start"] == top10_scope["date_end"] == "20200430"
    assert top10_scope["request_variant"] == "announcement_date:20200430:offset=0"
    assert ("shareholder_holdernumber", "holder_num") in links
    assert ("shareholder_top10", "hold_ratio") in links


def test_materializes_terminal_backed_immutable_shareholder_snapshot(
    tmp_path: Path,
) -> None:
    holder = pd.DataFrame({
        "ts_code": ["000001.SZ", "600000.SH"],
        "ann_date": ["20200430", "20200430"],
        "end_date": ["20191231", "20191231"], "holder_num": [1000, 2000],
    })
    top10 = pd.DataFrame({
        "ts_code": ["000001.SZ"] * 10 + ["600000.SH", "C18001"],
        "ann_date": ["20200430"] * 12,
        "end_date": ["20191231"] * 12,
        "holder_name": [f"holder-{index}" for index in range(10)]
        + ["outside", "non-equity"],
        "hold_ratio": [float(index) for index in range(1, 11)] + [50.0, 25.0],
    })
    collector = TushareCollector.__new__(TushareCollector)
    collector.max_retries = 1
    collector._collector_interfaces = {}
    collector.pro = SimpleNamespace(
        stk_holdernumber=lambda **_kwargs: holder.copy(),
        top10_holders=lambda **kwargs: (
            top10.copy() if kwargs.get("ann_date") == "20200430" else pd.DataFrame()
        ),
    )
    data_root = tmp_path / "data"
    audit = SourceAuditStore(data_root / "audit" / "audit.db")
    run_id = "shareholder-materialize"
    audit.append_event(run_id, "run_started", {
        "entrypoint": "scripts/data_sync.py", "universe": "csi1800",
        "target_date": "20200430", "range_start": "20200429",
    })
    fetch_shareholder_backfill(
        collector, start_date="2020-04-29", end_date="2020-04-30",
        run_id=run_id, audit_store=audit, scope_key="csi1800",
        universe="csi1800", evidence_symbols=["000001.SZ", "000002.SZ"],
    )
    terminal = audit.finalize_run(
        run_id=run_id, source="tushare", scope_key="csi1800",
        range_start="20200429", range_end="20200430",
        fields=("ann_date", "holder_num", "hold_ratio"),
        gates={name: True for name in (
            "fetch", "raw_payloads", "canonical_commit", "qlib_readback",
            "readiness", "contiguous_range",
        )},
        receipt_root=data_root / "audit" / "source_runs",
        allow_initial_history=True,
    )
    result = materialize_audited_shareholder_snapshot(
        terminal_receipt_path=terminal["receipt_path"], source_run_id=run_id,
        scope_key="csi1800", range_start="20200429", range_end="20200430",
        output_root=data_root / "research" / "source_snapshots" / "shareholder",
    )
    assert result["status"] == "published"
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert manifest["schema_version"] == 2
    assert manifest["artifact_type"] == "audited_shareholder_pit_sidecars_v2"
    assert manifest["contracts"]["transform"] == AUDITED_SNAPSHOT_CONTRACT
    assert manifest["source_evidence"]["terminal_receipt_sha256"] == terminal[
        "terminal_receipt_sha256"
    ]
    assert manifest["scope"]["symbols"] == ["000001.SZ", "000002.SZ"]
    assert manifest["projection"]["excluded_outside_union_rows"] == 3
    assert manifest["projection"]["excluded_non_equity_identifier_rows"] == 1
    projected = pd.read_parquet(result["top10_path"])
    assert projected.loc[0, "top10_ratio"] == 55.0
    assert set(projected["inst"]) == {"000001.SZ"}
    projected_holder = pd.read_parquet(result["holder_path"])
    assert set(projected_holder["inst"]) == {"000001.SZ"}
    reused = materialize_audited_shareholder_snapshot(
        terminal_receipt_path=terminal["receipt_path"], source_run_id=run_id,
        scope_key="csi1800", range_start="20200429", range_end="20200430",
        output_root=data_root / "research" / "source_snapshots" / "shareholder",
    )
    assert reused["status"] == "reused"

    terminal_path = Path(terminal["receipt_path"])
    trusted_terminal_bytes = terminal_path.read_bytes()
    untrusted_terminal = json.loads(trusted_terminal_bytes)
    untrusted_terminal["trust_state"] = "untrusted"
    terminal_path.write_text(json.dumps(untrusted_terminal), encoding="utf-8")
    with pytest.raises(RuntimeError, match="terminal receipt is not trusted"):
        materialize_audited_shareholder_snapshot(
            terminal_receipt_path=terminal_path, source_run_id=run_id,
            scope_key="csi1800", range_start="20200429", range_end="20200430",
            output_root=data_root / "untrusted",
        )
    terminal_path.write_bytes(trusted_terminal_bytes)
    with pytest.raises(RuntimeError, match="watermark does not cover scope"):
        materialize_audited_shareholder_snapshot(
            terminal_receipt_path=terminal_path, source_run_id=run_id,
            scope_key="csi1800", range_start="20200428", range_end="20200430",
            output_root=data_root / "scope-gap",
        )

    payload_path = next(
        data_root / row[0]
        for row in sqlite3.connect(data_root / "audit" / "audit.db").execute(
            "SELECT payload_path FROM fetch_receipts WHERE run_id=? AND status='success'",
            (run_id,),
        )
    )
    payload_path.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="payload sha256 mismatch"):
        materialize_audited_shareholder_snapshot(
            terminal_receipt_path=terminal["receipt_path"], source_run_id=run_id,
            scope_key="csi1800", range_start="20200429", range_end="20200430",
            output_root=data_root / "other",
        )


def test_shareholder_payload_requires_canonical_receipt_layout(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    receipt_id = "a" * 32
    run_id = "source-run"
    endpoint = "stk_holdernumber"
    canonical = (
        data_root / "raw" / "evidence" / "tushare" / endpoint
        / run_id / f"{receipt_id}.parquet"
    )
    canonical.parent.mkdir(parents=True)
    pd.DataFrame({
        "ts_code": ["000001.SZ"], "ann_date": ["20200430"],
        "end_date": ["20191231"], "holder_num": [1000],
    }).to_parquet(canonical, index=False)
    fetch = {
        "status": "success", "payload_kind": "raw_supplier",
        "source": "tushare", "endpoint": endpoint, "run_id": run_id,
        "receipt_id": receipt_id,
        "payload_path": canonical.relative_to(data_root).as_posix(),
        "payload_sha256": hashlib.sha256(canonical.read_bytes()).hexdigest(),
        "requested_scope": {"date_start": "20200401", "date_end": "20200430"},
    }
    frame, stats = _load_audited_shareholder_payload(
        fetch, data_root=data_root, endpoint=endpoint,
        expected_symbols={"000001.SZ"}, range_start="20200401",
        range_end="20200430",
    )
    assert len(frame) == 1
    assert stats["excluded_outside_union_rows"] == 0

    for bad_path in (
        f"raw/evidence/tushare/top10_holders/{run_id}/{receipt_id}.parquet",
        f"raw/evidence/tushare/{endpoint}/other-run/{receipt_id}.parquet",
        "../outside.parquet",
    ):
        with pytest.raises(RuntimeError, match="identity|canonical evidence layout"):
            _load_audited_shareholder_payload(
                {**fetch, "payload_path": bad_path},
                data_root=data_root, endpoint=endpoint,
                expected_symbols={"000001.SZ"}, range_start="20200401",
                range_end="20200430",
            )


def test_audited_top10_response_outside_requested_announcement_date_fails_closed(
    tmp_path: Path,
) -> None:
    wrong_day = pd.DataFrame({
        "ts_code": ["000001.SZ"], "ann_date": ["20200501"],
        "end_date": ["20191231"], "holder_name": ["holder"],
        "hold_ratio": [10.0],
    })
    collector = TushareCollector.__new__(TushareCollector)
    collector.max_retries = 1
    collector._collector_interfaces = {}
    collector.pro = SimpleNamespace(
        stk_holdernumber=lambda **_kwargs: pd.DataFrame(),
        top10_holders=lambda **_kwargs: wrong_day.copy(),
    )
    audit = SourceAuditStore(tmp_path / "audit" / "audit.db")

    with pytest.raises(RuntimeError, match="escaped requested announcement date"):
        fetch_shareholder_backfill(
            collector,
            start_date="2020-04-30",
            end_date="2020-04-30",
            run_id="wrong-top10-announcement",
            audit_store=audit,
            scope_key="csi1800",
            universe="csi1800",
            evidence_symbols=["000001.SZ"],
        )


def test_normalises_corrupt_period_and_aggregates_top10() -> None:
    raw = pd.DataFrame(
        {
            "ts_code": ["600415.SH", "600415.SH", "600415.SH"],
            "ann_date": ["20260423"] * 3,
            "end_date": ["('20260331", "20260331", "20260331"],
            "holder_name": ["A", "A", "B"],
            "hold_ratio": [10.0, 10.0, 20.0],
        }
    )
    result = normalise_top10_rows(raw)
    assert result.to_dict("records") == [
        {
            "inst": "600415.SH",
            "ann_date": "2026-04-23",
            "end_date": "2026-03-31",
            "top10_ratio": 30.0,
        }
    ]


def test_holder_latest_null_period_does_not_borrow_older_same_day_value() -> None:
    raw = pd.DataFrame({
        "ts_code": ["000001.SZ", "000001.SZ"],
        "ann_date": ["20260430", "20260430"],
        "end_date": ["20251231", "20260331"],
        "holder_num": [1000, pd.NA],
    })

    assert normalise_holder_rows(raw, strict_raw=True).empty


def test_holder_merge_removes_same_announcement_when_latest_period_is_null() -> None:
    existing = pd.DataFrame({
        "inst": ["000001.SZ"], "ann_date": ["2026-04-30"],
        "end_date": ["2025-12-31"], "holder_num": [1000],
    })
    incoming = pd.DataFrame({
        "ts_code": ["000001.SZ"], "ann_date": ["20260430"],
        "end_date": ["20260331"], "holder_num": [pd.NA],
    })

    assert merge_shareholder_rows(existing, incoming, kind="holder_num").empty


def test_holder_conflicting_exact_event_fails_closed() -> None:
    raw = pd.DataFrame({
        "ts_code": ["000001.SZ", "000001.SZ"],
        "ann_date": ["20260430", "20260430"],
        "end_date": ["20260331", "20260331"],
        "holder_num": [1000, 999],
    })

    with pytest.raises(ShareholderProjectionError, match="conflicting holder_num"):
        normalise_holder_rows(raw, strict_raw=True)


def test_strict_top10_normalises_identity_and_requires_complete_exact_event() -> None:
    raw = pd.DataFrame({
        "ts_code": ["000001.SZ"] * 11,
        "ann_date": ["20260430"] * 11,
        "end_date": ["20260331"] * 11,
        "holder_name": ["A-B", "A－B"] + [f"holder-{index}" for index in range(2, 11)],
        "hold_ratio": [10.0, 10.0] + [5.0] * 9,
    })

    projected = normalise_top10_rows(raw, require_complete_raw=True)

    assert projected["top10_ratio"].tolist() == [55.0]


def test_strict_top10_rejects_partial_latest_period_instead_of_using_older() -> None:
    older = pd.DataFrame({
        "ts_code": ["000001.SZ"] * 10,
        "ann_date": ["20260430"] * 10,
        "end_date": ["20251231"] * 10,
        "holder_name": [f"old-{index}" for index in range(10)],
        "hold_ratio": [5.0] * 10,
    })
    newer = pd.DataFrame({
        "ts_code": ["000001.SZ"] * 2,
        "ann_date": ["20260430"] * 2,
        "end_date": ["20260331"] * 2,
        "holder_name": ["new-a", "new-b"],
        "hold_ratio": [5.0, 5.0],
    })

    projected = normalise_top10_rows(
        pd.concat([older, newer], ignore_index=True),
        require_complete_raw=True,
    )

    assert projected.empty
    assert projected.attrs["projection_stats"]["excluded_incomplete_event_count"] == 1


def test_strict_top10_rejects_normalized_holder_ratio_conflict() -> None:
    raw = pd.DataFrame({
        "ts_code": ["000001.SZ", "000001.SZ"],
        "ann_date": ["20260430", "20260430"],
        "end_date": ["20260331", "20260331"],
        "holder_name": ["A-B", "A－B"],
        "hold_ratio": [10.0, 9.0],
    })

    projected = normalise_top10_rows(raw, require_complete_raw=True)

    assert projected.empty
    assert (
        projected.attrs["projection_stats"]["excluded_conflicting_ratio_event_count"]
        == 1
    )


def test_strict_top10_accepts_cutoff_ties_but_excludes_ambiguous_overfill() -> None:
    base = {
        "ts_code": ["000001.SZ"] * 11,
        "ann_date": ["20260430"] * 11,
        "end_date": ["20260331"] * 11,
        "holder_name": [f"holder-{index}" for index in range(11)],
    }
    tied = normalise_top10_rows(
        pd.DataFrame({**base, "hold_ratio": list(range(10, 1, -1)) + [1.0, 1.0]}),
        require_complete_raw=True,
    )
    ambiguous = normalise_top10_rows(
        pd.DataFrame({**base, "hold_ratio": list(range(11, 0, -1))}),
        require_complete_raw=True,
    )

    assert tied["top10_ratio"].tolist() == [55.0]
    assert tied.attrs["projection_stats"]["accepted_cutoff_tie_event_count"] == 1
    assert ambiguous.empty
    assert (
        ambiguous.attrs["projection_stats"]["excluded_ambiguous_overfull_event_count"]
        == 1
    )


def test_health_uses_announcement_date_asof_and_fails_stale_rows(tmp_path: Path) -> None:
    canonical = tmp_path / "data" / "canonical"
    canonical.mkdir(parents=True)
    pd.DataFrame(
        {
            "inst": ["A", "B", "A", "B"],
            "ann_date": ["2025-01-01", "2025-01-01", "2026-08-08", "2026-08-08"],
            "end_date": ["2024-12-31", "2024-12-31", "2026-06-30", "2026-06-30"],
            "holder_num": [1, 2, 3, 4],
        }
    ).to_parquet(canonical / "holder_num.parquet", index=False)
    pd.DataFrame(
        {
            "inst": ["A", "B"],
            "ann_date": ["2025-01-01", "2025-01-01"],
            "end_date": ["2024-12-31", "2024-12-31"],
            "top10_ratio": [20.0, 30.0],
        }
    ).to_parquet(canonical / "top10_holder_ratio.parquet", index=False)

    health = inspect_shareholder_sidecar_health(
        project_root=tmp_path,
        symbols=["A", "B"],
        as_of_date="2026-08-07",
        contract=CONTRACT,
    )
    assert health["status"] == "fail"
    assert health["sources"]["holder_num"]["latest_ann_date"] == "2026-08-08"
    assert health["sources"]["holder_num"]["median_stale_days"] > 365
    assert health["snapshot_hash"]


def test_health_uses_explicit_data_root_not_runtime_checkout(tmp_path: Path) -> None:
    data_root = tmp_path / "production" / "data"
    canonical = data_root / "canonical"
    canonical.mkdir(parents=True)
    pd.DataFrame(
        {
            "inst": ["A", "B"],
            "ann_date": ["2026-08-01", "2026-08-01"],
            "end_date": ["2026-06-30", "2026-06-30"],
            "holder_num": [10, 20],
        }
    ).to_parquet(canonical / "holder_num.parquet", index=False)
    pd.DataFrame(
        {
            "inst": ["A", "B"],
            "ann_date": ["2026-08-01", "2026-08-01"],
            "end_date": ["2026-06-30", "2026-06-30"],
            "top10_ratio": [30.0, 40.0],
        }
    ).to_parquet(canonical / "top10_holder_ratio.parquet", index=False)

    health = inspect_shareholder_sidecar_health(
        data_root=data_root,
        symbols=["A", "B"],
        as_of_date="2026-08-07",
        contract=CONTRACT,
    )

    assert health["status"] == "pass"
    assert health["sources"]["holder_num"]["path"].startswith("data/canonical/")

    output_dir = data_root / "audit" / "data_sync" / "run" / "shareholder"
    repair = run_shareholder_history_repair(
        data_root=data_root,
        symbols=["A", "B"],
        end_date="2026-08-07",
        contract=CONTRACT,
        apply=False,
        output_dir=output_dir,
    )
    assert repair["status"] == "planned"
    assert repair["bootstrap_required"] is True
    assert repair["state_before"] == {}
    assert repair["state_after"] == {}
    assert Path(repair["summary_path"]) == (
        output_dir / "shareholder_repair_summary.json"
    )


def _seed_shareholder_sidecars(data_root: Path, state: dict[str, object]) -> Path:
    canonical = data_root / "canonical"
    canonical.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "inst": ["A", "B"],
            "ann_date": ["2026-08-20", "2026-08-20"],
            "end_date": ["2026-06-30", "2026-06-30"],
            "holder_num": [10, 20],
        }
    ).to_parquet(canonical / "holder_num.parquet", index=False)
    pd.DataFrame(
        {
            "inst": ["A", "B"],
            "ann_date": ["2026-08-20", "2026-08-20"],
            "end_date": ["2026-06-30", "2026-06-30"],
            "top10_ratio": [30.0, 40.0],
        }
    ).to_parquet(canonical / "top10_holder_ratio.parquet", index=False)
    state_path = canonical / "shareholder_sync_state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return state_path


def _run_stateful_repair(
    data_root: Path,
    *,
    state: dict[str, object],
    required_start: str,
    fetch_name: str,
    fetch_side_effect=None,
):
    state_path = _seed_shareholder_sidecars(data_root, state)
    calls: list[tuple[str, str]] = []

    def fake_fetch(_collector, *, start_date: str, end_date: str):
        calls.append((start_date, end_date))
        if fetch_side_effect is not None:
            return fetch_side_effect(start_date, end_date)
        return pd.DataFrame(), pd.DataFrame(), {
            "mode": fetch_name,
            "start_date": start_date,
            "end_date": end_date,
            "holder_source_rows": 0,
            "top10_source_rows": 0,
        }

    with patch(
        f"qsys.ops.shareholder_sync.fetch_shareholder_{fetch_name}",
        side_effect=fake_fetch,
    ):
        result = run_shareholder_history_repair(
            data_root=data_root,
            symbols=["A", "B"],
            end_date="2026-08-21",
            contract=CONTRACT,
            apply=True,
            output_dir=data_root / "audit",
            collector=object(),
            required_history_start_date=required_start,
        )
    return result, calls, state_path


def test_v1_state_triggers_required_history_bootstrap(tmp_path: Path) -> None:
    result, calls, state_path = _run_stateful_repair(
        tmp_path,
        state={"schema_version": 1, "checked_through": "2026-08-20"},
        required_start="2022-01-01",
        fetch_name="backfill",
    )

    assert result["status"] == "success"
    assert result["bootstrap_required"] is True
    assert calls == [("2022-01-01", "2026-08-21")]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["schema_version"] == 2
    assert state["history_start_date"] == "2022-01-01"
    assert state["checked_through"] == "2026-08-21"
    assert state["last_successful_mode"] == "backfill"
    assert state["holder_num_sha256"]
    assert state["top10_holder_ratio_sha256"]
    assert state["completed_at"]


def test_v2_state_with_required_history_uses_incremental(tmp_path: Path) -> None:
    result, calls, _ = _run_stateful_repair(
        tmp_path,
        state={
            "schema_version": 2,
            "history_start_date": "2022-01-01",
            "checked_through": "2026-08-20",
        },
        required_start="2022-01-01",
        fetch_name="incremental",
    )

    assert result["status"] == "success"
    assert result["bootstrap_required"] is False
    assert calls == [("2026-08-21", "2026-08-21")]
    assert result["state_after"]["history_start_date"] == "2022-01-01"
    assert result["state_after"]["last_successful_mode"] == "incremental"


def test_expanded_required_history_triggers_earlier_backfill(tmp_path: Path) -> None:
    result, calls, _ = _run_stateful_repair(
        tmp_path,
        state={
            "schema_version": 2,
            "history_start_date": "2024-01-01",
            "checked_through": "2026-08-20",
        },
        required_start="2022-01-01",
        fetch_name="backfill",
    )

    assert result["bootstrap_required"] is True
    assert calls == [("2022-01-01", "2026-08-21")]
    assert result["state_after"]["history_start_date"] == "2022-01-01"


def test_failed_repair_does_not_advance_successful_state(tmp_path: Path) -> None:
    original = {
        "schema_version": 2,
        "history_start_date": "2022-01-01",
        "checked_through": "2026-08-20",
        "last_successful_start": "2026-08-20",
    }

    def fail(_start: str, _end: str):
        raise RuntimeError("source failed")

    result, calls, state_path = _run_stateful_repair(
        tmp_path,
        state=original,
        required_start="2022-01-01",
        fetch_name="incremental",
        fetch_side_effect=fail,
    )

    assert result["status"] == "failed"
    assert calls == [("2026-08-21", "2026-08-21")]
    assert result["state_before"] == original
    assert result["state_after"] == original
    assert json.loads(state_path.read_text(encoding="utf-8")) == original


def test_pagination_rejects_repeated_full_page() -> None:
    def broken_api(*, limit: int, offset: int) -> pd.DataFrame:
        return pd.DataFrame({"value": range(limit)})

    with pytest.raises(RuntimeError, match="repeated"):
        _paged_call(broken_api, limit=2)


def test_holder_normaliser_keeps_latest_period_for_same_announcement() -> None:
    result = normalise_holder_rows(
        pd.DataFrame(
            {
                "ts_code": ["A", "A"],
                "ann_date": ["20260401", "20260401"],
                "end_date": ["20251231", "20260331"],
                "holder_num": [100, 90],
            }
        )
    )
    assert result.iloc[0]["end_date"] == "2026-03-31"
    assert result.iloc[0]["holder_num"] == 90


def test_calendar_year_chunks_are_complete_and_non_overlapping() -> None:
    assert _calendar_year_chunks("2022-06-30", "2024-01-01") == [
        ("2022-06-30", "2022-12-31"),
        ("2023-01-01", "2023-12-31"),
        ("2024-01-01", "2024-01-01"),
    ]
    assert _calendar_year_chunks("2023-02-01", "2023-02-01") == [
        ("2023-02-01", "2023-02-01")
    ]
    with pytest.raises(ValueError, match="on or before"):
        _calendar_year_chunks("2023-02-02", "2023-02-01")


def test_backfill_fetches_holder_by_year_and_audits_rows() -> None:
    holder_calls: list[dict[str, object]] = []

    def holder_api(**kwargs: object) -> pd.DataFrame:
        holder_calls.append(kwargs)
        return pd.DataFrame(
            {
                "ts_code": [f"A{kwargs['start_date']}"],
                "ann_date": [kwargs["start_date"]],
                "end_date": [kwargs["start_date"]],
                "holder_num": [1],
            }
        )

    def top10_api(**kwargs: object) -> pd.DataFrame:
        return pd.DataFrame()

    class Collector:
        class pro:
            stk_holdernumber = staticmethod(holder_api)
            top10_holders = staticmethod(top10_api)

    holder, top10, audit = fetch_shareholder_backfill(
        Collector(), start_date="2022-06-30", end_date="2024-01-01"
    )

    assert top10.empty
    assert len(holder) == 3
    assert [
        (call["start_date"], call["end_date"], call["limit"])
        for call in holder_calls
    ] == [
        ("20220630", "20221231", 3000),
        ("20230101", "20231231", 3000),
        ("20240101", "20240101", 3000),
    ]
    assert audit["holder_chunks"] == [
        {"start_date": "2022-06-30", "end_date": "2022-12-31", "rows": 1},
        {"start_date": "2023-01-01", "end_date": "2023-12-31", "rows": 1},
        {"start_date": "2024-01-01", "end_date": "2024-01-01", "rows": 1},
    ]
    assert audit["holder_source_rows"] == sum(
        chunk["rows"] for chunk in audit["holder_chunks"]
    )


def test_backfill_fails_on_holder_chunk_and_preserves_call_order() -> None:
    holder_calls: list[tuple[str, str]] = []

    def holder_api(**kwargs: object) -> pd.DataFrame:
        holder_calls.append((str(kwargs["start_date"]), str(kwargs["end_date"])))
        if kwargs["start_date"] == "20230101":
            raise RuntimeError("second holder chunk failed")
        return pd.DataFrame(
            {
                "ts_code": ["A"],
                "ann_date": [kwargs["start_date"]],
                "end_date": [kwargs["start_date"]],
                "holder_num": [1],
            }
        )

    def top10_api(**kwargs: object) -> pd.DataFrame:
        raise AssertionError("top10 must not run after holder chunk failure")

    class Collector:
        class pro:
            stk_holdernumber = staticmethod(holder_api)
            top10_holders = staticmethod(top10_api)

    with pytest.raises(RuntimeError, match="second holder chunk failed"):
        fetch_shareholder_backfill(
            Collector(), start_date="2022-01-01", end_date="2024-01-01"
        )
    assert holder_calls == [
        ("20220101", "20221231"),
        ("20230101", "20231231"),
    ]
