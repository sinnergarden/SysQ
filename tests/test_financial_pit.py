from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from qsys.data._merge_helpers import (
    FINANCIAL_AVAILABILITY_CONTRACT,
    FinancialAvailabilityError,
    select_first_available_financial_rows,
)
from qsys.data.collector import TushareCollector
from qsys.data.source_audit import SourceAuditStore, stable_scope_hash
from qsys.data.storage import StockDataStore


TARGET = "20260821"
START = "20180313"
CODE = "000001.SZ"


def _income_row(**overrides) -> dict:
    row = {
        "ts_code": CODE,
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


def test_statement_visibility_uses_max_ann_and_f_ann_cutoff() -> None:
    raw = pd.DataFrame([_income_row(f_ann_date="20260822")])
    before, before_stats = select_first_available_financial_rows(
        raw, endpoint="income", availability_cutoff=TARGET,
    )
    publication_day, _ = select_first_available_financial_rows(
        raw, endpoint="income", availability_cutoff="20260822",
    )
    assert before.empty
    assert before_stats["excluded_future_rows"] == 1
    assert publication_day["publication_date"].tolist() == ["20260822"]
    assert publication_day["availability_date"].tolist() == ["20260822"]


def test_indicator_future_ann_is_excluded() -> None:
    raw = pd.DataFrame({
        "ts_code": [CODE], "ann_date": ["20260822"], "end_date": ["20260630"],
        "update_flag": ["0"], "roe": [10.0],
    })
    projected, stats = select_first_available_financial_rows(
        raw, endpoint="fina_indicator", availability_cutoff=TARGET,
    )
    assert projected.empty
    assert stats["excluded_future_rows"] == 1


def test_unproven_only_flag_one_is_excluded_but_later_statement_f_ann_is_usable() -> None:
    unproven, stats = select_first_available_financial_rows(
        pd.DataFrame([_income_row(update_flag="1")]),
        endpoint="income", availability_cutoff=TARGET,
    )
    assert unproven.empty
    assert stats["revision_timeline_unproven_excluded_keys"] == 1
    assert stats["excluded_later_revision_rows"] == 0
    later, later_stats = select_first_available_financial_rows(
        pd.DataFrame([
            _income_row(
                ann_date="20260818", f_ann_date="20260820", update_flag="1",
            )
        ]),
        endpoint="income", availability_cutoff=TARGET,
    )
    assert later["publication_date"].tolist() == ["20260820"]
    assert later["availability_date"].tolist() == ["20260820"]
    assert later_stats["revision_timeline_unproven_excluded_keys"] == 0


def test_indicator_only_flag_one_is_excluded() -> None:
    raw = pd.DataFrame({
        "ts_code": [CODE], "ann_date": ["20260820"], "end_date": ["20260630"],
        "update_flag": ["1"], "roe": [10.0],
    })
    projected, stats = select_first_available_financial_rows(
        raw, endpoint="fina_indicator", availability_cutoff=TARGET,
    )
    assert projected.empty
    assert stats["revision_timeline_unproven_excluded_rows"] == 1


def test_mixed_only_flag_one_rows_separate_unproven_from_later_revisions() -> None:
    raw = pd.DataFrame([
        _income_row(
            ann_date="20260818", f_ann_date="20260820", update_flag="1",
            revenue=10.0,
        ),
        _income_row(
            ann_date="20260819", f_ann_date="20260819", update_flag="1",
            revenue=99.0,
        ),
    ])

    projected, stats = select_first_available_financial_rows(
        raw, endpoint="income", availability_cutoff=TARGET,
    )

    assert projected["revenue"].tolist() == [10.0]
    assert stats["excluded_later_revision_rows"] == 0
    assert stats["revision_timeline_unproven_excluded_keys"] == 1
    assert stats["revision_timeline_unproven_excluded_rows"] == 1


def test_indicator_supplier_cap_is_partial_before_projection() -> None:
    raw = pd.DataFrame({
        "ts_code": [CODE] * 100,
        "ann_date": [f"2024{month:02d}{day:02d}" for month in range(1, 11) for day in range(1, 11)],
        "end_date": [f"{year}1231" for year in range(1925, 2025)],
        "update_flag": ["0"] * 100,
        "roe": range(100),
    })
    with pytest.raises(FinancialAvailabilityError, match="possible_truncation"):
        select_first_available_financial_rows(
            raw, endpoint="fina_indicator", availability_cutoff=TARGET,
        )


def test_same_availability_prefers_flag_zero_independent_of_order() -> None:
    raw = pd.DataFrame([
        _income_row(update_flag="1", revenue=99.0),
        _income_row(update_flag="0", revenue=10.0),
    ])
    projected, _ = select_first_available_financial_rows(
        raw.iloc[::-1], endpoint="income", availability_cutoff=TARGET,
    )
    assert projected.iloc[0]["update_flag"] == "0"
    assert projected.iloc[0]["revenue"] == 10.0


def test_same_priority_payload_conflict_fails_closed() -> None:
    raw = pd.DataFrame([
        _income_row(update_flag="0", revenue=10.0),
        _income_row(update_flag="0", revenue=11.0),
    ])
    with pytest.raises(FinancialAvailabilityError, match="same_priority_payload_conflict"):
        select_first_available_financial_rows(
            raw, endpoint="income", availability_cutoff=TARGET,
        )


def test_expected_end_type_wins_and_non_consumed_balance_conflict_is_diagnostic() -> None:
    common = {
        "ts_code": "000627.SZ", "ann_date": "20230422", "f_ann_date": "20230422",
        "end_date": "20221231", "report_type": "1", "end_type": "4",
        "update_flag": "0", "total_assets": 100.0,
        "total_hldr_eqy_exc_min_int": 20.0, "total_cur_liab": 10.0,
    }
    raw = pd.DataFrame([
        {**common, "comp_type": "3", "total_cur_assets": None},
        {**common, "comp_type": "1", "total_cur_assets": 30.0},
        {**common, "comp_type": "1", "end_type": None,
         "f_ann_date": "20260430", "total_assets": 999.0},
    ])
    projected, stats = select_first_available_financial_rows(
        raw, endpoint="balancesheet", availability_cutoff=TARGET,
    )
    assert len(projected) == 1
    assert pd.isna(projected.iloc[0]["total_cur_assets"])
    assert projected.iloc[0]["total_assets"] == 100.0
    assert stats["non_consumed_branch_exception_count"] == 1
    assert stats["collapsed_equivalent_branch_rows"] == 0
    assert stats["non_consumed_branch_exceptions"][0]["fields"] == ["total_cur_assets"]


def test_missing_end_type_projection_diagnostic_is_portable_json() -> None:
    raw = pd.DataFrame([{
        "ts_code": "001233.SZ", "ann_date": "20210101",
        "f_ann_date": "20210101", "end_date": "20201231",
        "report_type": "1", "comp_type": "1", "end_type": pd.NA,
        "update_flag": "1", "total_assets": 100.0,
        "total_hldr_eqy_exc_min_int": 20.0,
    }])

    projected, stats = select_first_available_financial_rows(
        raw, endpoint="balancesheet", availability_cutoff=TARGET,
    )

    assert projected.empty
    assert stats["missing_end_type_fallback_keys"] == 1
    exception = stats["revision_timeline_unproven_exceptions"][0]
    assert exception["logical_key"]["end_type"] is None
    assert json.loads(json.dumps(stats, allow_nan=False)) == stats


def test_consumed_company_type_conflict_fails_closed() -> None:
    raw = pd.DataFrame([
        {
            **_income_row(), "comp_type": "1", "revenue": 10.0,
        },
        {
            **_income_row(), "comp_type": "2", "revenue": 11.0,
        },
    ])
    with pytest.raises(
        FinancialAvailabilityError, match="canonical_company_type_branch_conflict",
    ):
        select_first_available_financial_rows(
            raw, endpoint="income", availability_cutoff=TARGET,
        )


def _collector(responses: dict[str, pd.DataFrame], calls: list[tuple[str, dict]]) -> TushareCollector:
    collector = TushareCollector.__new__(TushareCollector)
    collector.max_retries = 1
    collector._collector_interfaces = {
        "income": {"fields": "ts_code,ann_date,end_date,f_ann_date,report_type,comp_type,end_type,update_flag,n_income,revenue,oper_cost"},
        "balancesheet": {"fields": "ts_code,ann_date,end_date,f_ann_date,report_type,comp_type,end_type,update_flag,total_assets,total_hldr_eqy_exc_min_int,total_cur_assets,total_cur_liab"},
        "cashflow": {"fields": "ts_code,ann_date,end_date,f_ann_date,report_type,comp_type,end_type,update_flag,n_cashflow_act"},
        "fina_indicator": {"fields": "ts_code,ann_date,end_date,update_flag,roe,grossprofit_margin,debt_to_assets"},
    }
    collector._percent_financial_cols = {"roe", "grossprofit_margin", "debt_to_assets"}
    collector._percent_like_threshold = 3.0

    def endpoint_api(name):
        def fetch(**kwargs):
            calls.append((name, dict(kwargs)))
            return responses[name].copy()
        return fetch

    collector._get_interface_api = endpoint_api
    collector._fetch_with_retry = lambda api, **kwargs: api(**kwargs)
    return collector


def test_raw_future_payload_is_preserved_while_canonical_projection_excludes_it(
    tmp_path: Path,
) -> None:
    future = _income_row(ann_date="20260820", f_ann_date="20260822", end_date="20260630")
    responses = {
        "income": pd.DataFrame([_income_row(end_date="20260331", end_type="1"), future]),
        "balancesheet": pd.DataFrame(), "cashflow": pd.DataFrame(),
        "fina_indicator": pd.DataFrame(),
    }
    collector = _collector(responses, [])
    audit = SourceAuditStore(tmp_path / "audit" / "audit.db")
    projected = collector._fetch_financials(
        START, TARGET, ts_code=CODE, run_id="future-raw", audit_store=audit,
        scope_key="csi1800", universe="csi1800",
    )
    assert projected["availability_date"].tolist() == ["20260820"]
    assert projected["revenue"].tolist() == [10.0]
    with sqlite3.connect(tmp_path / "audit" / "audit.db") as connection:
        payload = connection.execute(
            "SELECT payload_path FROM fetch_receipts WHERE run_id=? AND endpoint='income'",
            ("future-raw",),
        ).fetchone()[0]
    raw = pd.read_parquet(tmp_path / payload)
    assert set(raw["end_date"]) == {"20260331", "20260630"}
    events = [
        row["payload"] for row in audit.run_evidence_summary("future-raw")["events"]
        if row["event_type"] == "financial_availability_projection"
    ]
    assert next(row for row in events if row["endpoint"] == "income")["excluded_future_rows"] == 1


def test_independent_endpoint_events_carry_earlier_balance_into_later_income(
    tmp_path: Path,
) -> None:
    statement = {
        "ts_code": CODE, "end_date": "20260630", "report_type": "1",
        "comp_type": "1", "end_type": "2", "update_flag": "0",
    }
    responses = {
        "income": pd.DataFrame([{
            **statement, "ann_date": "20260820", "f_ann_date": "20260820",
            "n_income": 2.0, "revenue": 10.0, "oper_cost": 6.0,
        }]),
        "balancesheet": pd.DataFrame([{
            **statement, "ann_date": "20260818", "f_ann_date": "20260818",
            "total_assets": 20.0, "total_hldr_eqy_exc_min_int": 8.0,
        }]),
        "cashflow": pd.DataFrame(),
        "fina_indicator": pd.DataFrame(),
    }
    collector = _collector(responses, [])
    audit = SourceAuditStore(tmp_path / "audit" / "audit.db")

    events = collector._fetch_financials(
        START, TARGET, ts_code=CODE, run_id="endpoint-events",
        audit_store=audit, scope_key="csi1800", universe="csi1800",
    )

    assert events["availability_date"].tolist() == ["20260818", "20260820"]
    assert events["total_assets"].tolist() == [20.0, 20.0]
    assert pd.isna(events.iloc[0]["net_income"])
    assert events.iloc[1]["net_income"] == 2.0


def test_financial_endpoint_events_do_not_regress_to_late_old_report(
    tmp_path: Path,
) -> None:
    responses = {
        "income": pd.DataFrame([
            _income_row(
                ann_date="20260818", f_ann_date="20260818",
                end_date="20260630", end_type="2", revenue=20.0,
            ),
            _income_row(
                ann_date="20260820", f_ann_date="20260820",
                end_date="20260331", end_type="1", revenue=10.0,
            ),
        ]),
        "balancesheet": pd.DataFrame(), "cashflow": pd.DataFrame(),
        "fina_indicator": pd.DataFrame(),
    }
    collector = _collector(responses, [])
    audit = SourceAuditStore(tmp_path / "audit" / "audit.db")

    events = collector._fetch_financials(
        START, TARGET, ts_code=CODE, run_id="late-old-report",
        audit_store=audit, scope_key="csi1800", universe="csi1800",
    )

    assert events["availability_date"].tolist() == ["20260818"]
    assert events["revenue"].tolist() == [20.0]


def test_new_financial_report_missing_value_does_not_carry_prior_quarter(
    tmp_path: Path,
) -> None:
    responses = {
        "income": pd.DataFrame([
            _income_row(
                ann_date="20260818", f_ann_date="20260818",
                end_date="20260331", end_type="1", revenue=10.0,
            ),
            _income_row(
                ann_date="20260820", f_ann_date="20260820",
                end_date="20260630", end_type="2", revenue=pd.NA,
            ),
        ]),
        "balancesheet": pd.DataFrame(), "cashflow": pd.DataFrame(),
        "fina_indicator": pd.DataFrame(),
    }
    collector = _collector(responses, [])
    audit = SourceAuditStore(tmp_path / "audit" / "audit.db")

    events = collector._fetch_financials(
        START, TARGET, ts_code=CODE, run_id="missing-new-report-value",
        audit_store=audit, scope_key="csi1800", universe="csi1800",
    )

    assert events["revenue"].iloc[0] == 10.0
    assert pd.isna(events["revenue"].iloc[1])


def test_daily_financial_candidate_uses_exact_ann_date_for_all_endpoints() -> None:
    statement = {
        "ts_code": CODE, "ann_date": TARGET, "f_ann_date": TARGET,
        "end_date": "20260630", "report_type": "1", "comp_type": "1",
        "end_type": "2", "update_flag": "0",
    }
    responses = {
        "income": pd.DataFrame([{**statement, "n_income": 2.0, "revenue": 10.0,
                                  "oper_cost": 6.0}]),
        "balancesheet": pd.DataFrame([{**statement, "total_assets": 20.0,
                                        "total_hldr_eqy_exc_min_int": 8.0}]),
        "cashflow": pd.DataFrame([{**statement, "n_cashflow_act": 4.0}]),
        "fina_indicator": pd.DataFrame([{
            "ts_code": CODE, "ann_date": TARGET, "end_date": "20260630",
            "update_flag": "0", "roe": 0.25,
            "grossprofit_margin": 0.4, "debt_to_assets": 0.6,
        }]),
    }
    calls: list[tuple[str, dict]] = []
    collector = _collector(responses, calls)
    collector._discover_financial_announcement_codes = (
        lambda target, requested, **kwargs: {CODE}
    )

    daily = collector._fetch_financials_for_daily(TARGET, {CODE})

    assert not daily.empty
    assert {name for name, _ in calls} == set(responses)
    for _, kwargs in calls:
        assert kwargs["ann_date"] == TARGET
        assert "start_date" not in kwargs
        assert "end_date" not in kwargs


def test_daily_actual_date_uses_original_ann_date_and_projects_later_f_ann() -> None:
    original_ann = "20260818"
    statement = {
        "ts_code": CODE, "ann_date": original_ann, "f_ann_date": TARGET,
        "end_date": "20260630", "report_type": "1", "comp_type": "1",
        "end_type": "2", "update_flag": "0",
    }
    responses = {
        "income": pd.DataFrame([{**statement, "n_income": 2.0, "revenue": 10.0,
                                  "oper_cost": 6.0}]),
        "balancesheet": pd.DataFrame(), "cashflow": pd.DataFrame(),
        "fina_indicator": pd.DataFrame(),
    }
    calls: list[tuple[str, dict]] = []
    collector = _collector(responses, calls)
    collector._discover_financial_announcement_codes = (
        lambda target, requested, **kwargs: {CODE: {original_ann}}
    )

    daily = collector._fetch_financials_for_daily(TARGET, {CODE})

    assert daily["availability_date"].tolist() == [TARGET]
    assert daily["net_income"].tolist() == [2.0]
    assert calls
    assert all(kwargs["ann_date"] == original_ann for _, kwargs in calls)


def test_discovery_preserves_original_ann_date_for_actual_date_match() -> None:
    collector = TushareCollector.__new__(TushareCollector)
    collector._collector_interfaces = {
        "disclosure_date": {
            "fields": "ts_code,ann_date,end_date,pre_date,actual_date",
        }
    }

    def fetch(_endpoint, *, request_variant, **kwargs):
        if request_variant == "actual_date":
            return pd.DataFrame({
                "ts_code": [CODE], "ann_date": ["20260818"],
                "actual_date": [TARGET], "end_date": ["20260630"],
            }), "actual-receipt"
        return pd.DataFrame(), "ann-receipt"

    collector._fetch_daily_endpoint_with_receipt = fetch

    assert collector._discover_financial_announcement_codes(TARGET, {CODE}) == {
        CODE: {"20260818"}
    }


def test_daily_multiple_original_ann_dates_coalesce_without_period_regression() -> None:
    collector = TushareCollector.__new__(TushareCollector)
    collector._discover_financial_announcement_codes = (
        lambda target, requested: {CODE: {"20260818", "20260819"}}
    )

    def fetch(start, end, ts_code, **kwargs):
        if kwargs["exact_ann_date"] == "20260818":
            return pd.DataFrame({
                "ts_code": [ts_code], "availability_date": [TARGET],
                "_financial_period_end": ["20260331"], "revenue": [10.0],
                "total_assets": [pd.NA],
            })
        return pd.DataFrame({
            "ts_code": [ts_code], "availability_date": [TARGET],
            "_financial_period_end": ["20260630"], "revenue": [pd.NA],
            "total_assets": [20.0],
        })

    collector._fetch_financials = fetch

    daily = collector._fetch_financials_for_daily(TARGET, {CODE})

    assert len(daily) == 1
    assert daily.iloc[0]["_financial_period_end"] == "20260630"
    assert daily.iloc[0]["revenue"] == 10.0
    assert daily.iloc[0]["total_assets"] == 20.0


def test_wrong_symbol_financial_response_is_partial_and_fails_closed(
    tmp_path: Path,
) -> None:
    responses = {
        "income": pd.DataFrame([_income_row(ts_code="999999.SZ")]),
        "balancesheet": pd.DataFrame(), "cashflow": pd.DataFrame(),
        "fina_indicator": pd.DataFrame(),
    }
    collector = _collector(responses, [])
    audit = SourceAuditStore(tmp_path / "audit" / "audit.db")

    with pytest.raises(RuntimeError, match="symbol_mismatch"):
        collector._fetch_financials(
            START, TARGET, ts_code=CODE, run_id="wrong-financial-symbol",
            audit_store=audit, scope_key="csi1800", universe="csi1800",
        )

    with sqlite3.connect(audit.db_path) as connection:
        status, error_json = connection.execute(
            "SELECT status,error_json FROM fetch_receipts WHERE run_id=?",
            ("wrong-financial-symbol",),
        ).fetchone()
    assert status == "partial"
    assert json.loads(error_json)["detail"]["kind"] == "response_validation_failed"


def _run_started(audit: SourceAuditStore, run_id: str) -> None:
    audit.append_event(run_id, "run_started", {
        "entrypoint": "scripts/data_sync.py", "universe": "csi1800",
        "target_date": TARGET, "range_start": START,
    })


def _proof(audit: SourceAuditStore, root: Path, run_id: str) -> dict:
    audit.record_crash_receipt(
        run_id=run_id, receipt_root=root / "source_runs",
        entrypoint="scripts/data_sync.py", error="injected",
    )
    return audit.validate_resume_run(
        resume_from_run_id=run_id, expected_entrypoint="scripts/data_sync.py",
        universe="csi1800", target_date=TARGET, range_start=START,
    )


def test_legacy_financial_resume_reprojects_offline_with_new_identity(tmp_path: Path) -> None:
    statement = {
        "ts_code": CODE, "ann_date": "20260820", "f_ann_date": "20260820",
        "end_date": "20260331", "report_type": "1", "comp_type": "1",
        "end_type": "1", "update_flag": "0",
    }
    responses = {
        "income": pd.DataFrame([{**statement, "n_income": 2.0, "revenue": 10.0,
                                  "oper_cost": 6.0}]),
        "balancesheet": pd.DataFrame([{**statement, "total_assets": 20.0,
                                        "total_hldr_eqy_exc_min_int": 8.0}]),
        "cashflow": pd.DataFrame([{**statement, "n_cashflow_act": 4.0}]),
        "fina_indicator": pd.DataFrame([{
            "ts_code": CODE, "ann_date": "20260820", "end_date": "20260331",
            "update_flag": "0", "roe": 0.25,
            "grossprofit_margin": 0.4, "debt_to_assets": 0.6,
        }]),
    }
    calls: list[tuple[str, dict]] = []
    collector = _collector(responses, calls)
    root = tmp_path / "audit"
    audit = SourceAuditStore(root / "audit.db")
    old_run = "legacy-financial"
    _run_started(audit, old_run)
    old_scope = {
        "date_start": START, "date_end": TARGET, "symbol_count": 1,
        "symbols": [CODE], "symbols_sha256": stable_scope_hash([CODE]),
    }
    old_receipts = {}
    for endpoint in responses:
        old_frame, old_receipt = collector._fetch_daily_endpoint_with_receipt(
            endpoint, run_id=old_run, audit_store=audit, requested_scope=old_scope,
            scope_key="csi1800", universe="csi1800",
            identity_columns=("ts_code", "ann_date"), ts_code=CODE,
            start_date=START, end_date=TARGET,
            fields=collector._get_interface_fields(endpoint),
        )
        assert not old_frame.empty and old_receipt
        old_receipts[endpoint] = old_receipt
    proof = _proof(audit, root, old_run)
    new_run = "availability-financial"
    _run_started(audit, new_run)
    calls.clear()
    projected = collector._fetch_financials(
        START, TARGET, ts_code=CODE, run_id=new_run, audit_store=audit,
        resume_proof=proof, scope_key="csi1800", universe="csi1800",
    )
    assert calls == []
    assert projected.iloc[0]["availability_date"] == "20260820"
    assert projected.iloc[0]["net_income"] == 2.0
    assert projected.iloc[0]["total_assets"] == 20.0
    with sqlite3.connect(root / "audit.db") as connection:
        old_paths = {
            endpoint: connection.execute(
                "SELECT payload_path FROM fetch_receipts WHERE receipt_id=?",
                (receipt_id,),
            ).fetchone()[0]
            for endpoint, receipt_id in old_receipts.items()
        }
        new_rows = connection.execute(
            "SELECT endpoint,requested_scope_json,payload_path FROM fetch_receipts "
            "WHERE run_id=?", (new_run,),
        ).fetchall()
    assert {endpoint for endpoint, _, _ in new_rows} == set(responses)
    for endpoint, scope_json, new_path in new_rows:
        new_scope = json.loads(scope_json)
        assert new_path == old_paths[endpoint]
        assert new_scope["request_variant"] == FINANCIAL_AVAILABILITY_CONTRACT
        assert new_scope["availability_cutoff"] == TARGET
    assert any(
        row["event_type"] == "financial_legacy_shard_reprojected"
        for row in audit.run_evidence_summary(new_run)["events"]
    )

    # A legacy payload reprojected into B remains exactly reusable by C; the
    # immutable payload path may still physically belong to A.
    proof_b = _proof(audit, root, new_run)
    final_run = "availability-financial-hop-c"
    _run_started(audit, final_run)
    calls.clear()
    final = collector._fetch_financials(
        START, TARGET, ts_code=CODE, run_id=final_run, audit_store=audit,
        resume_proof=proof_b, scope_key="csi1800", universe="csi1800",
    )
    assert calls == []
    pd.testing.assert_frame_equal(final, projected)
    reused = [
        row for row in audit.run_evidence_summary(final_run)["events"]
        if row["event_type"] == "fetch_shard_reused"
    ]
    assert len(reused) == 4


def test_full_range_reprojection_clears_legacy_value_excluded_as_unproven(
    tmp_path: Path,
) -> None:
    projected, stats = select_first_available_financial_rows(
        pd.DataFrame([_income_row(update_flag="1")]),
        endpoint="income", availability_cutoff=TARGET,
    )
    assert projected.empty
    assert stats["revision_timeline_unproven_excluded_keys"] == 1

    canonical = tmp_path / "canonical"
    canonical.mkdir()
    old = pd.DataFrame({
        "ts_code": [CODE], "trade_date": [TARGET], "close": [10.0],
        "net_income": [999.0],
    })
    old.to_feather(canonical / f"{CODE}.feather")
    store = StockDataStore.__new__(StockDataStore)
    store.canonical_dir = canonical
    store.update_latest_date = lambda code, latest: None
    incoming = pd.DataFrame({
        "ts_code": [CODE], "trade_date": [TARGET], "close": [10.0],
        "net_income": [pd.NA],
    })

    store.save_daily(incoming, CODE, existing_df=old)

    saved = pd.read_feather(canonical / f"{CODE}.feather")
    assert pd.isna(saved.loc[0, "net_income"])
