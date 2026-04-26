from __future__ import annotations

from qsys.ops.data_coverage import classify_gap, decide_root_cause, inspect_collector_status


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
