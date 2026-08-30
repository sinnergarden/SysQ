from __future__ import annotations

import pandas as pd
import pytest

from qsys.pit_certification import (
    CertificationError,
    _audit_financial_revision_terminal,
    _financial_asof_samples,
    _latest_shareholder_vintage_values,
    _validate_source_revision_count_contract,
    classify_financial_revision_events,
    classify_shareholder_vintage_events,
    resolve_financial_events_as_of,
)


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
