from __future__ import annotations

import pandas as pd

from qsys.ops.data_coverage import (
    HISTORICAL_BACKFILL_PLAN_FIELDS,
    HISTORICAL_QLIB_GAP_FIELDS,
    HISTORICAL_RAW_GAP_FIELDS,
    apply_suspension_overrides,
    build_historical_backfill_plan,
    build_historical_gap_summary,
    classify_gap,
    classify_historical_recommended_action,
    decide_root_cause,
    inspect_collector_status,
)


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
            "date": "2025-01-02",
            "raw_available": True,
            "required_fields_available": True,
            "required_fields_non_null": True,
            "missing_fields": "",
            "gap_type": "raw_ok",
            "reason": "ok",
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
    ]
    qlib_gap_rows = [
        {
            "symbol": "000001.SZ",
            "date": "2025-01-02",
            "qlib_available": True,
            "core_fields_available": True,
            "core_fields_non_null": True,
            "missing_fields": "",
            "gap_type": "qlib_ok",
            "reason": "ok",
        },
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
    assert summary["qlib_missing_count"] == 1
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
    raw_gap_rows, qlib_gap_rows = apply_suspension_overrides(
        raw_gap_rows=raw_gap_rows,
        qlib_gap_rows=qlib_gap_rows,
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
        backfill_plan_rows=plan_rows,
        qlib_audit_mode="full_symbol_scan",
    )
    assert raw_gap_rows[0]["gap_type"] == "raw_suspended"
    assert qlib_gap_rows[0]["gap_type"] == "qlib_suspended"
    assert plan_rows[0]["recommended_action"] == "none"
    assert summary["raw_missing_count"] == 0
    assert summary["qlib_missing_count"] == 0
    assert summary["suspended_count"] == 1
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
