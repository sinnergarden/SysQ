from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from qsys.ops import data_coverage
from qsys.ops.data_coverage import (
    HISTORICAL_BACKFILL_PLAN_FIELDS,
    HISTORICAL_QLIB_GAP_FIELDS,
    HISTORICAL_RAW_GAP_FIELDS,
    HistoricalGapDetailLimitExceeded,
    apply_suspension_overrides,
    build_historical_backfill_plan,
    build_historical_gap_summary,
    classify_gap,
    classify_historical_recommended_action,
    decide_root_cause,
    inspect_collector_status,
    load_local_suspension_evidence,
    scan_historical_qlib_gaps,
    scan_historical_raw_gaps,
)
from qsys.data.source_audit import (
    REQUIRED_TERMINAL_GATES,
    SourceAuditStore,
    checkpoint_requested_scope,
    normalized_response_metadata,
    stable_scope_hash,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_suspension_terminal_receipt(
    root: Path,
    *,
    symbols: set[str],
    start_date: str,
    end_date: str,
    events_by_symbol: dict[str, list[str]],
    universe: str = "csi1800",
) -> Path:
    data_root = root / "data"
    run_id = "suspension-history"
    audit_root = data_root / "audit"
    store = SourceAuditStore(audit_root / "audit.db")
    for symbol in sorted(symbols):
        dates = events_by_symbol.get(symbol, [])
        frame = pd.DataFrame({
            "ts_code": [symbol] * len(dates),
            "trade_date": dates,
        })
        scope = checkpoint_requested_scope(
            {
                "date_start": start_date,
                "date_end": end_date,
                "symbol_count": 1,
                "symbols": [symbol],
                "symbols_sha256": stable_scope_hash([symbol]),
            },
            source="tushare",
            endpoint="suspend_d",
            contract_version="1",
            scope_key=universe,
            universe=universe,
        )
        store.record_fetch(
            run_id=run_id,
            source="tushare",
            endpoint="suspend_d",
            contract_version="1",
            status="success" if dates else "empty",
            requested_scope=scope,
            returned_rows=len(frame),
            attempt_count=1,
            payload_frame=frame if dates else None,
            **normalized_response_metadata(frame),
        )
    finalized = store.finalize_run(
        run_id=run_id,
        source="tushare",
        scope_key=universe,
        range_start=start_date,
        range_end=end_date,
        fields=["suspend_d"],
        gates={name: True for name in REQUIRED_TERMINAL_GATES},
        receipt_root=audit_root / "source_runs",
        allow_initial_history=True,
    )
    assert finalized["status"] == "trusted"
    return Path(finalized["receipt_path"])


def test_classify_gap_raw_stale() -> None:
    gap_type, reason = classify_gap(
        raw_last_date="2026-04-03",
        qlib_last_date="2026-04-03",
        instrument_end_date="2026-04-03",
        last_qlib_date="2026-04-17",
    )
    assert gap_type == "raw_stale"
    assert "raw data" in reason


def test_classify_gap_qlib_stale() -> None:
    gap_type, _ = classify_gap(
        raw_last_date="2026-04-17",
        qlib_last_date="2026-04-03",
        instrument_end_date="2026-04-03",
        last_qlib_date="2026-04-17",
    )
    assert gap_type == "qlib_stale"


def test_classify_gap_instrument_registry_stale() -> None:
    gap_type, _ = classify_gap(
        raw_last_date="2026-04-17",
        qlib_last_date="2026-04-17",
        instrument_end_date="2026-04-03",
        last_qlib_date="2026-04-17",
    )
    assert gap_type == "instrument_registry_stale"


def test_classify_gap_aligned() -> None:
    gap_type, _ = classify_gap(
        raw_last_date="2026-04-17",
        qlib_last_date="2026-04-17",
        instrument_end_date="2026-04-17",
        last_qlib_date="2026-04-17",
    )
    assert gap_type == "raw_and_qlib_aligned"


def test_collector_summary_warns_incomplete_all_universe(tmp_path) -> None:
    summary = inspect_collector_status(
        project_root=tmp_path,
        all_instrument_count=348,
        csi300_instrument_count=300,
        raw_symbol_count=348,
        raw_latest_count=50,
        qlib_latest_count=50,
    )
    assert summary["warning"] == "all universe appears incomplete for A-share full universe"


def test_collector_summary_classifies_partial_raw_update(tmp_path) -> None:
    summary = inspect_collector_status(
        project_root=tmp_path,
        all_instrument_count=348,
        csi300_instrument_count=300,
        raw_symbol_count=348,
        raw_latest_count=50,
        qlib_latest_count=50,
    )
    root = decide_root_cause(
        raw_summary={"raw_symbol_count": 348, "symbols_with_raw_on_latest": 50},
        qlib_summary={"symbols_with_qlib_on_latest": 50},
        collector_summary={**summary, "stock_list_count": 5494},
    )
    assert root["root_cause"] == "raw_update_partial"


def test_artifact_contract_payloads() -> None:
    raw_summary = {
        "raw_file_count": 1,
        "raw_symbol_count": 1,
        "raw_latest_date": "2026-04-17",
        "symbols_with_raw_on_latest": 1,
        "csi300_symbols_with_raw_on_latest": 1,
        "all_symbols_with_raw_on_latest": 1,
    }
    qlib_summary = {
        "qlib_calendar_last_date": "2026-04-17",
        "qlib_symbol_count": 1,
        "symbols_with_qlib_on_latest": 1,
        "csi300_symbols_with_qlib_on_latest": 1,
        "all_symbols_with_qlib_on_latest": 1,
    }
    collector_summary = {
        "update_script": "scripts/update_data_all.py",
        "collector_mode": "by_symbol_batch_range",
        "raw_store_symbol_count": 1,
        "stock_list_count": 1,
        "all_instrument_count": 348,
        "csi300_instrument_count": 300,
        "suspected_issue": "stock_list_incomplete",
        "recommendation": "refresh stock list / index constituents before next qlib dump",
        "warning": "all universe appears incomplete for A-share full universe",
    }
    root = decide_root_cause(raw_summary=raw_summary, qlib_summary=qlib_summary, collector_summary=collector_summary)
    assert set(raw_summary) == {
        "raw_file_count",
        "raw_symbol_count",
        "raw_latest_date",
        "symbols_with_raw_on_latest",
        "csi300_symbols_with_raw_on_latest",
        "all_symbols_with_raw_on_latest",
    }
    assert set(qlib_summary) == {
        "qlib_calendar_last_date",
        "qlib_symbol_count",
        "symbols_with_qlib_on_latest",
        "csi300_symbols_with_qlib_on_latest",
        "all_symbols_with_qlib_on_latest",
    }
    assert set(root) == {"root_cause", "recommendation"}


def test_historical_recommendation_classification() -> None:
    assert classify_historical_recommended_action(raw_has_gap=True, qlib_has_gap=True) == "raw_backfill_then_qlib_refresh"
    assert classify_historical_recommended_action(raw_has_gap=False, qlib_has_gap=True) == "qlib_refresh"
    assert classify_historical_recommended_action(raw_has_gap=True, qlib_has_gap=False) == "manual_investigation"
    assert classify_historical_recommended_action(raw_has_gap=False, qlib_has_gap=False) == "none"


def test_historical_gap_summary_contract() -> None:
    raw_gap_rows = [
        {
            "symbol": "000001.SZ",
            "date": "2025-01-03",
            "raw_available": False,
            "required_fields_available": True,
            "required_fields_non_null": False,
            "missing_fields": "",
            "gap_type": "raw_missing",
            "reason": "raw row missing on expected trading date",
        },
    ]
    qlib_gap_rows = [
        {
            "symbol": "000001.SZ",
            "date": "2025-01-03",
            "qlib_available": False,
            "core_fields_available": False,
            "core_fields_non_null": False,
            "missing_fields": "$open,$high,$low,$close,$volume,$amount",
            "gap_type": "qlib_missing",
            "reason": "qlib row missing on expected trading date",
        },
    ]
    raw_scan_summary = {
        "expected_symbol_date_count": 2,
        "raw_missing_count": 1,
        "raw_field_issue_count": 0,
        "raw_ok_count": 1,
        "raw_suspended_count": 0,
        "retained_gap_detail_count": 1,
    }
    qlib_scan_summary = {
        "expected_symbol_date_count": 2,
        "qlib_missing_count": 1,
        "qlib_field_issue_count": 0,
        "qlib_ok_count": 1,
        "qlib_suspended_count": 0,
        "retained_gap_detail_count": 1,
        "qlib_audit_mode": "full_symbol_scan",
    }
    calendar_dates = [pd.Timestamp("2025-01-02"), pd.Timestamp("2025-01-03")]
    plan_rows = build_historical_backfill_plan(
        symbols={"000001.SZ"},
        calendar_dates=calendar_dates,
        raw_gap_rows=raw_gap_rows,
        qlib_gap_rows=qlib_gap_rows,
    )
    summary = build_historical_gap_summary(
        universe="csi300",
        start_date="2025-01-01",
        end_date="2025-01-17",
        symbols={"000001.SZ"},
        calendar_dates=calendar_dates,
        raw_gap_rows=raw_gap_rows,
        qlib_gap_rows=qlib_gap_rows,
        raw_scan_summary=raw_scan_summary,
        qlib_scan_summary=qlib_scan_summary,
        backfill_plan_rows=plan_rows,
        qlib_audit_mode="full_symbol_scan",
    )
    assert set(raw_gap_rows[0]) == set(HISTORICAL_RAW_GAP_FIELDS)
    assert set(qlib_gap_rows[0]) == set(HISTORICAL_QLIB_GAP_FIELDS)
    assert set(plan_rows[0]) == set(HISTORICAL_BACKFILL_PLAN_FIELDS)
    assert set(summary) >= {
        "universe",
        "start_date",
        "end_date",
        "symbol_count",
        "trading_date_count",
        "expected_symbol_date_count",
        "raw_missing_count",
        "raw_field_issue_count",
        "qlib_missing_count",
        "qlib_field_issue_count",
        "aligned_ok_count",
        "worst_symbols",
        "worst_dates",
        "root_cause",
        "recommendation",
    }
    assert summary["raw_missing_count"] == 1
    assert summary["raw_ok_count"] == 1
    assert summary["qlib_missing_count"] == 1
    assert summary["qlib_ok_count"] == 1
    assert summary["aligned_ok_count"] == 1
    assert summary["suspended_count"] == 0
    assert summary["root_cause"] == "mixed"


def test_suspension_override_removes_false_backfill_plan() -> None:
    raw_gap_rows = [
        {
            "symbol": "601059.SH",
            "date": "2025-11-20",
            "raw_available": False,
            "required_fields_available": True,
            "required_fields_non_null": False,
            "missing_fields": "",
            "gap_type": "raw_missing",
            "reason": "raw row missing on expected trading date",
        }
    ]
    qlib_gap_rows = [
        {
            "symbol": "601059.SH",
            "date": "2025-11-20",
            "qlib_available": False,
            "core_fields_available": False,
            "core_fields_non_null": False,
            "missing_fields": "$open,$high,$low,$close,$volume,$amount",
            "gap_type": "qlib_missing",
            "reason": "qlib row missing on expected trading date",
        }
    ]
    raw_scan_summary = {
        "expected_symbol_date_count": 1,
        "raw_missing_count": 1,
        "raw_field_issue_count": 0,
        "raw_ok_count": 0,
        "raw_suspended_count": 0,
        "retained_gap_detail_count": 1,
    }
    qlib_scan_summary = {
        "expected_symbol_date_count": 1,
        "qlib_missing_count": 1,
        "qlib_field_issue_count": 0,
        "qlib_ok_count": 0,
        "qlib_suspended_count": 0,
        "retained_gap_detail_count": 1,
        "qlib_audit_mode": "full_symbol_scan",
    }
    raw_gap_rows, qlib_gap_rows = apply_suspension_overrides(
        raw_gap_rows=raw_gap_rows,
        qlib_gap_rows=qlib_gap_rows,
        raw_summary=raw_scan_summary,
        qlib_summary=qlib_scan_summary,
        suspended_dates_by_symbol={"601059.SH": {"2025-11-20"}},
    )
    plan_rows = build_historical_backfill_plan(
        symbols={"601059.SH"},
        calendar_dates=[pd.Timestamp("2025-11-20")],
        raw_gap_rows=raw_gap_rows,
        qlib_gap_rows=qlib_gap_rows,
    )
    summary = build_historical_gap_summary(
        universe="csi300",
        start_date="2025-11-20",
        end_date="2025-11-20",
        symbols={"601059.SH"},
        calendar_dates=[pd.Timestamp("2025-11-20")],
        raw_gap_rows=raw_gap_rows,
        qlib_gap_rows=qlib_gap_rows,
        raw_scan_summary=raw_scan_summary,
        qlib_scan_summary=qlib_scan_summary,
        backfill_plan_rows=plan_rows,
        qlib_audit_mode="full_symbol_scan",
    )
    assert raw_gap_rows == []
    assert qlib_gap_rows == []
    assert plan_rows == []
    assert summary["raw_missing_count"] == 0
    assert summary["qlib_missing_count"] == 0
    assert summary["suspended_count"] == 1
    assert summary["aligned_ok_count"] == 1
    assert summary["recommended_action_counts"] == {"none": 1}
    assert summary["root_cause"] == "clean"


def test_historical_gap_range_merge() -> None:
    calendar_dates = [
        pd.Timestamp("2025-01-02"),
        pd.Timestamp("2025-01-03"),
        pd.Timestamp("2025-01-06"),
    ]
    raw_gap_rows = [
        {
            "symbol": "000001.SZ",
            "date": "2025-01-02",
            "raw_available": False,
            "required_fields_available": True,
            "required_fields_non_null": False,
            "missing_fields": "",
            "gap_type": "raw_missing",
            "reason": "raw row missing on expected trading date",
        },
        {
            "symbol": "000001.SZ",
            "date": "2025-01-03",
            "raw_available": False,
            "required_fields_available": True,
            "required_fields_non_null": False,
            "missing_fields": "",
            "gap_type": "raw_missing",
            "reason": "raw row missing on expected trading date",
        },
        {
            "symbol": "000001.SZ",
            "date": "2025-01-06",
            "raw_available": True,
            "required_fields_available": True,
            "required_fields_non_null": True,
            "missing_fields": "",
            "gap_type": "raw_ok",
            "reason": "ok",
        },
    ]
    qlib_gap_rows = [
        {
            "symbol": "000001.SZ",
            "date": "2025-01-02",
            "qlib_available": False,
            "core_fields_available": False,
            "core_fields_non_null": False,
            "missing_fields": "$open",
            "gap_type": "qlib_missing",
            "reason": "qlib row missing on expected trading date",
        },
        {
            "symbol": "000001.SZ",
            "date": "2025-01-03",
            "qlib_available": False,
            "core_fields_available": False,
            "core_fields_non_null": False,
            "missing_fields": "$open",
            "gap_type": "qlib_missing",
            "reason": "qlib row missing on expected trading date",
        },
        {
            "symbol": "000001.SZ",
            "date": "2025-01-06",
            "qlib_available": True,
            "core_fields_available": True,
            "core_fields_non_null": True,
            "missing_fields": "",
            "gap_type": "qlib_ok",
            "reason": "ok",
        },
    ]
    plan_rows = build_historical_backfill_plan(
        symbols={"000001.SZ"},
        calendar_dates=calendar_dates,
        raw_gap_rows=raw_gap_rows,
        qlib_gap_rows=qlib_gap_rows,
    )
    assert len(plan_rows) == 1
    assert plan_rows[0]["gap_start"] == "2025-01-02"
    assert plan_rows[0]["gap_end"] == "2025-01-03"
    assert plan_rows[0]["recommended_action"] == "raw_backfill_then_qlib_refresh"


def test_historical_gap_range_split() -> None:
    calendar_dates = [
        pd.Timestamp("2025-01-02"),
        pd.Timestamp("2025-01-03"),
        pd.Timestamp("2025-01-06"),
    ]
    raw_gap_rows = [
        {
            "symbol": "000001.SZ",
            "date": "2025-01-02",
            "raw_available": False,
            "required_fields_available": True,
            "required_fields_non_null": False,
            "missing_fields": "",
            "gap_type": "raw_missing",
            "reason": "raw row missing on expected trading date",
        },
        {
            "symbol": "000001.SZ",
            "date": "2025-01-03",
            "raw_available": True,
            "required_fields_available": True,
            "required_fields_non_null": True,
            "missing_fields": "",
            "gap_type": "raw_ok",
            "reason": "ok",
        },
        {
            "symbol": "000001.SZ",
            "date": "2025-01-06",
            "raw_available": False,
            "required_fields_available": True,
            "required_fields_non_null": False,
            "missing_fields": "",
            "gap_type": "raw_missing",
            "reason": "raw row missing on expected trading date",
        },
    ]
    qlib_gap_rows = [
        {
            "symbol": "000001.SZ",
            "date": "2025-01-02",
            "qlib_available": False,
            "core_fields_available": False,
            "core_fields_non_null": False,
            "missing_fields": "$open",
            "gap_type": "qlib_missing",
            "reason": "qlib row missing on expected trading date",
        },
        {
            "symbol": "000001.SZ",
            "date": "2025-01-03",
            "qlib_available": True,
            "core_fields_available": True,
            "core_fields_non_null": True,
            "missing_fields": "",
            "gap_type": "qlib_ok",
            "reason": "ok",
        },
        {
            "symbol": "000001.SZ",
            "date": "2025-01-06",
            "qlib_available": False,
            "core_fields_available": False,
            "core_fields_non_null": False,
            "missing_fields": "$open",
            "gap_type": "qlib_missing",
            "reason": "qlib row missing on expected trading date",
        },
    ]
    plan_rows = build_historical_backfill_plan(
        symbols={"000001.SZ"},
        calendar_dates=calendar_dates,
        raw_gap_rows=raw_gap_rows,
        qlib_gap_rows=qlib_gap_rows,
    )
    assert len(plan_rows) == 2
    assert [(row["gap_start"], row["gap_end"]) for row in plan_rows] == [
        ("2025-01-02", "2025-01-02"),
        ("2025-01-06", "2025-01-06"),
    ]


def test_historical_scans_retain_only_gap_details(monkeypatch, tmp_path) -> None:
    symbol = "000001.SZ"
    calendar_dates = list(pd.date_range("2010-01-01", periods=2_000, freq="D"))
    instrument_rows = pd.DataFrame({
        "instrument": [symbol],
        "start_date": [calendar_dates[0]],
        "end_date": [calendar_dates[-1]],
    })
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / f"{symbol}.feather").touch()
    raw_frame = pd.DataFrame({
        "trade_date": calendar_dates,
        "open": 1.0,
        "high": 1.0,
        "low": 1.0,
        "close": 1.0,
        "volume": 1.0,
        "amount": 1.0,
    })
    raw_frame.loc[1_000, "close"] = None
    monkeypatch.setattr(pd, "read_feather", lambda _path: raw_frame)

    raw_rows, raw_summary = scan_historical_raw_gaps(
        raw_dir,
        symbols={symbol},
        instrument_rows=instrument_rows,
        calendar_dates=calendar_dates,
        start_date=calendar_dates[0].strftime("%Y-%m-%d"),
        end_date=calendar_dates[-1].strftime("%Y-%m-%d"),
        max_gap_details=10,
    )

    class Adapter:
        def init_qlib(self) -> None:
            return None

    qlib_dates = calendar_dates[:1_000] + calendar_dates[1_001:]
    qlib_index = pd.MultiIndex.from_tuples(
        [(symbol, date) for date in qlib_dates],
        names=["instrument", "datetime"],
    )
    qlib_frame = pd.DataFrame(
        {
            field: [float(index) for index in range(len(qlib_dates))]
            for field in data_coverage.QLIB_REQUIRED_FIELDS
        },
        index=qlib_index,
    )
    class FeatureProvider:
        @staticmethod
        def features(*_args, **_kwargs):
            return qlib_frame

    monkeypatch.setattr(data_coverage, "D", FeatureProvider())
    qlib_rows, qlib_summary = scan_historical_qlib_gaps(
        Adapter(),
        symbols={symbol},
        instrument_rows=instrument_rows,
        calendar_dates=calendar_dates,
        start_date=calendar_dates[0].strftime("%Y-%m-%d"),
        end_date=calendar_dates[-1].strftime("%Y-%m-%d"),
        max_gap_details=10,
    )

    assert len(raw_rows) == len(qlib_rows) == 1
    assert raw_summary["expected_symbol_date_count"] == 2_000
    assert raw_summary["raw_ok_count"] == 1_999
    assert raw_summary["retained_gap_detail_count"] == 1
    assert qlib_summary["expected_symbol_date_count"] == 2_000
    assert qlib_summary["qlib_ok_count"] == 1_999
    assert qlib_summary["retained_gap_detail_count"] == 1


def test_historical_gap_detail_cap_fails_closed(tmp_path) -> None:
    calendar_dates = list(pd.date_range("2025-01-01", periods=3, freq="D"))
    with pytest.raises(
        HistoricalGapDetailLimitExceeded,
        match="raw historical gap detail limit exceeded: max_gap_details=2",
    ):
        scan_historical_raw_gaps(
            tmp_path,
            symbols={"000001.SZ"},
            instrument_rows=pd.DataFrame(),
            calendar_dates=calendar_dates,
            start_date="2025-01-01",
            end_date="2025-01-03",
            max_gap_details=2,
        )


def test_local_suspension_terminal_receipt_is_offline_and_hash_bound(
    monkeypatch, tmp_path
) -> None:
    symbols = {"000001.SZ", "000002.SZ"}
    receipt = _write_suspension_terminal_receipt(
        tmp_path,
        symbols=symbols,
        start_date="2025-01-01",
        end_date="2025-01-03",
        events_by_symbol={"000001.SZ": ["2025-01-02"]},
    )

    def forbidden_collector() -> None:
        raise AssertionError("offline audit must not construct TushareCollector")

    monkeypatch.setattr("qsys.data.collector.TushareCollector", forbidden_collector)
    result = load_local_suspension_evidence(
        receipt,
        symbols=symbols,
        start_date="2025-01-01",
        end_date="2025-01-03",
        universe="csi1800",
    )

    assert result["status"] == "trusted_complete"
    assert result["sha256"] == _sha256(receipt)
    assert result["run_id"] == "suspension-history"
    assert result["scope_key"] == "csi1800"
    assert result["shard_count"] == 2
    assert result["payload_count"] == 1
    assert result["row_count"] == 1
    assert result["suspended_dates_by_symbol"] == {
        "000001.SZ": {"2025-01-02"}
    }


def test_local_suspension_receipt_rejects_missing_plain_and_forged_files(
    tmp_path,
) -> None:
    symbols = {"000001.SZ"}
    with pytest.raises(ValueError, match="does not exist"):
        load_local_suspension_evidence(
            tmp_path / "missing.json",
            symbols=symbols,
            start_date="2025-01-01",
            end_date="2025-01-03",
            universe="csi1800",
        )

    plain = tmp_path / "plain.csv"
    plain.write_text("ts_code,trade_date\n000001.SZ,2025-01-02\n", encoding="utf-8")
    with pytest.raises(ValueError, match="terminal receipt JSON"):
        load_local_suspension_evidence(
            plain,
            symbols=symbols,
            start_date="2025-01-01",
            end_date="2025-01-03",
            universe="csi1800",
        )

    receipt = _write_suspension_terminal_receipt(
        tmp_path / "forged",
        symbols=symbols,
        start_date="2025-01-01",
        end_date="2025-01-03",
        events_by_symbol={"000001.SZ": ["2025-01-02"]},
    )
    terminal = json.loads(receipt.read_text(encoding="utf-8"))
    terminal["exported_at"] = "2099-01-01T00:00:00Z"
    receipt.write_text(json.dumps(terminal) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="trusted watermark backlink"):
        load_local_suspension_evidence(
            receipt,
            symbols=symbols,
            start_date="2025-01-01",
            end_date="2025-01-03",
            universe="csi1800",
        )


def test_local_suspension_receipt_rejects_untrusted_or_wrong_scope(tmp_path) -> None:
    symbols = {"000001.SZ"}
    receipt = _write_suspension_terminal_receipt(
        tmp_path / "untrusted",
        symbols=symbols,
        start_date="2025-01-01",
        end_date="2025-01-03",
        events_by_symbol={},
    )
    terminal = json.loads(receipt.read_text(encoding="utf-8"))
    terminal["trust_state"] = "untrusted"
    receipt.write_text(json.dumps(terminal) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not trusted with all gates true"):
        load_local_suspension_evidence(
            receipt,
            symbols=symbols,
            start_date="2025-01-01",
            end_date="2025-01-03",
            universe="csi1800",
        )

    receipt = _write_suspension_terminal_receipt(
        tmp_path / "wrong-scope",
        symbols=symbols,
        start_date="2025-01-01",
        end_date="2025-01-03",
        events_by_symbol={},
    )
    with pytest.raises(ValueError, match="exact range"):
        load_local_suspension_evidence(
            receipt,
            symbols=symbols,
            start_date="2025-01-02",
            end_date="2025-01-03",
            universe="csi1800",
        )

    receipt = _write_suspension_terminal_receipt(
        tmp_path / "watermark-range",
        symbols=symbols,
        start_date="2025-01-01",
        end_date="2025-01-03",
        events_by_symbol={},
    )
    audit_db = receipt.parents[3] / "audit" / "audit.db"
    with sqlite3.connect(audit_db) as connection:
        connection.execute(
            "UPDATE trusted_watermarks SET range_start=? WHERE run_id=?",
            ("2025-01-02", "suspension-history"),
        )
    with pytest.raises(ValueError, match="watermark backlink does not cover"):
        load_local_suspension_evidence(
            receipt,
            symbols=symbols,
            start_date="2025-01-01",
            end_date="2025-01-03",
            universe="csi1800",
        )


def test_local_suspension_receipt_rejects_missing_symbol_tamper_and_escape(
    tmp_path,
) -> None:
    receipt = _write_suspension_terminal_receipt(
        tmp_path / "missing",
        symbols={"000001.SZ"},
        start_date="2025-01-01",
        end_date="2025-01-03",
        events_by_symbol={},
    )
    with pytest.raises(ValueError, match="does not cover the requested symbol set"):
        load_local_suspension_evidence(
            receipt,
            symbols={"000001.SZ", "000002.SZ"},
            start_date="2025-01-01",
            end_date="2025-01-03",
            universe="csi1800",
        )

    receipt = _write_suspension_terminal_receipt(
        tmp_path / "tampered",
        symbols={"000001.SZ"},
        start_date="2025-01-01",
        end_date="2025-01-03",
        events_by_symbol={"000001.SZ": ["2025-01-02"]},
    )
    terminal = json.loads(receipt.read_text(encoding="utf-8"))
    fetch = terminal["fetch_receipts"][0]
    payload_path = receipt.parents[3] / fetch["payload_path"]
    payload_path.write_bytes(payload_path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="payload sha256 mismatch"):
        load_local_suspension_evidence(
            receipt,
            symbols={"000001.SZ"},
            start_date="2025-01-01",
            end_date="2025-01-03",
            universe="csi1800",
        )

    receipt = _write_suspension_terminal_receipt(
        tmp_path / "escaped",
        symbols={"000001.SZ"},
        start_date="2025-01-01",
        end_date="2025-01-03",
        events_by_symbol={"000001.SZ": ["2025-01-04"]},
    )
    with pytest.raises(ValueError, match="payload date escaped requested scope"):
        load_local_suspension_evidence(
            receipt,
            symbols={"000001.SZ"},
            start_date="2025-01-01",
            end_date="2025-01-03",
            universe="csi1800",
        )


def test_local_suspension_receipt_fails_when_payload_cannot_be_hashed(
    monkeypatch, tmp_path
) -> None:
    symbols = {"000001.SZ"}
    receipt = _write_suspension_terminal_receipt(
        tmp_path,
        symbols=symbols,
        start_date="2025-01-01",
        end_date="2025-01-03",
        events_by_symbol={"000001.SZ": ["2025-01-02"]},
    )
    original_open = Path.open

    def deny_event_hash(self: Path, mode: str = "r", *args, **kwargs):
        if self.suffix == ".parquet" and mode == "rb":
            raise OSError("denied")
        return original_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_event_hash)
    with pytest.raises(ValueError, match="cannot hash suspension evidence payload"):
        load_local_suspension_evidence(
            receipt,
            symbols=symbols,
            start_date="2025-01-01",
            end_date="2025-01-03",
            universe="csi1800",
        )
