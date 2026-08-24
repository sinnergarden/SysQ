from __future__ import annotations

import hashlib
import json

import pandas as pd
import pytest

from qsys.backtest.accounting import (
    BacktestAccount,
    CorporateActionStore,
    normalize_tushare_dividend,
    write_corporate_action_artifact,
)


def _held() -> BacktestAccount:
    account = BacktestAccount(100_000)
    account.start_day("2026-01-01")
    account.update_after_deal("A", 100, 10, 0, "buy")
    account.start_day("2026-01-02")
    return account


def test_artifact_name_is_bare() -> None:
    with pytest.raises(ValueError):
        CorporateActionStore("/tmp", "../escape")
    with pytest.raises(ValueError):
        write_corporate_action_artifact(pd.DataFrame(), "/tmp", artifact_name="/absolute")


@pytest.mark.parametrize("kind,multiplier", [
    ("stock_dividend", 1.1), ("bonus_shares", 2.0), ("split", 2.0), ("consolidation", 0.5),
])
def test_share_actions_preserve_total_basis(kind: str, multiplier: float) -> None:
    account = _held()
    event = {"event_id": kind, "instrument": "A", "effective_date": "2026-01-02",
             "event_type": kind, "share_multiplier": multiplier,
             "settlement_date": "2026-01-03" if kind in {"stock_dividend", "bonus_shares"} else "2026-01-02"}
    account.apply_corporate_action(event)
    assert account.positions["A"].total_basis == pytest.approx(1000)
    assert account.positions["A"].total_amount == int(100 * multiplier)


def test_non_integer_share_action_fails_closed() -> None:
    account = _held()
    with pytest.raises(ValueError, match="non-integer"):
        account.apply_corporate_action({"event_id": "bad", "instrument": "A",
                                        "event_type": "split", "share_multiplier": 1.005})


def test_stock_dividend_is_not_sellable_until_list_date() -> None:
    account = _held()
    account.apply_corporate_action({"event_id": "sd", "instrument": "A",
                                    "effective_date": "2026-01-02", "event_type": "stock_dividend",
                                    "share_multiplier": 1.1, "settlement_date": "2026-01-04"})
    assert account.positions["A"].total_amount == 110
    assert account.positions["A"].sellable_amount == 100
    account.start_day("2026-01-03")
    assert account.positions["A"].sellable_amount == 100
    account.start_day("2026-01-04")
    assert account.positions["A"].sellable_amount == 110
    assert account.corporate_action_ledger_rows[-1]["status"] == "settled"


def test_cash_entitlement_precedes_share_adjustment() -> None:
    account = _held()
    account.apply_corporate_actions([
        {"event_id": "a_share", "instrument": "A", "effective_date": "2026-01-02",
         "event_type": "stock_dividend", "share_multiplier": 2,
         "settlement_date": "2026-01-03"},
        {"event_id": "z_cash", "instrument": "A", "effective_date": "2026-01-02",
         "event_type": "cash_dividend", "cash_per_share": 1,
         "settlement_date": "2026-01-03"},
    ], "2026-01-02")
    assert account.dividend_receivable == 100


def test_normalize_filters_unimplemented_and_retains_source_fields(tmp_path) -> None:
    raw = pd.DataFrame([
        {"ts_code": "A", "div_proc": "预案", "ex_date": "2026-01-02"},
        {"ts_code": "A", "div_proc": "实施", "ex_date": "2026-01-02",
         "pay_date": "2026-01-04", "imp_ann_date": "2026-01-01",
         "record_date": "2026-01-03", "cash_div": 0.5, "cash_div_tax": 1.0,
         "stk_bo_rate": 0.1, "div_listdate": "2026-01-04"},
    ])
    events = normalize_tushare_dividend(raw)
    assert set(events["event_type"]) == {"cash_dividend", "stock_dividend"}
    assert events.loc[events["event_type"] == "cash_dividend", "cash_per_share"].iloc[0] == 1.0
    assert set(events["share_multiplier"]) == {1.0, 1.1}
    root = write_corporate_action_artifact(events, tmp_path, artifact_name="fixture_v1")
    store = CorporateActionStore(tmp_path, "fixture_v1")
    assert len(store.for_date("2026-01-02")) == 2
    digest = hashlib.sha256((root / "events.parquet").read_bytes()).hexdigest()
    assert json.loads((root / "manifest.json").read_text())["events_sha256"] == digest


def test_normalize_requires_list_date_and_rejects_known_at_lookahead() -> None:
    base = {"ts_code": "A", "div_proc": "实施", "ex_date": "2026-01-02",
            "stk_bo_rate": 0.1}
    with pytest.raises(ValueError, match="div_listdate"):
        normalize_tushare_dividend(pd.DataFrame([base]))
    late = {**base, "div_listdate": "2026-01-04", "imp_ann_date": "2026-01-03"}
    with pytest.raises(ValueError, match="known_at"):
        normalize_tushare_dividend(pd.DataFrame([late]))


def test_normalize_rejects_settlement_before_ex_date() -> None:
    cash = {"ts_code": "A", "div_proc": "实施", "ex_date": "2026-01-03",
            "pay_date": "2026-01-02", "cash_div_tax": 1.0}
    with pytest.raises(ValueError, match="precedes effective_date"):
        normalize_tushare_dividend(pd.DataFrame([cash]))
    shares = {"ts_code": "A", "div_proc": "实施", "ex_date": "2026-01-03",
              "div_listdate": "2026-01-02", "stk_bo_rate": 0.1}
    with pytest.raises(ValueError, match="precedes effective_date"):
        normalize_tushare_dividend(pd.DataFrame([shares]))


def test_normalize_combines_bo_and_co_without_compounding() -> None:
    raw = pd.DataFrame([{
        "ts_code": "A", "div_proc": "实施", "ex_date": "2026-01-02",
        "div_listdate": "2026-01-04", "imp_ann_date": "2026-01-01",
        "stk_bo_rate": 0.1, "stk_co_rate": 0.2,
    }])
    events = normalize_tushare_dividend(raw)
    assert len(events) == 1
    assert events.iloc[0]["share_multiplier"] == pytest.approx(1.3)


def test_normalize_stk_div_total_without_components_is_not_dropped() -> None:
    row = _implementation_row(
        cash_div_tax=0.0,
        cash_div=0.0,
        stk_div=0.1,
        stk_bo_rate=float("nan"),
        stk_co_rate=float("nan"),
    )
    events = normalize_tushare_dividend(pd.DataFrame([row]))

    assert len(events) == 1
    assert events.iloc[0]["event_type"] == "stock_dividend"
    assert events.iloc[0]["share_multiplier"] == pytest.approx(1.1)


def test_normalize_stk_div_and_components_produce_one_consistent_event() -> None:
    row = _implementation_row(stk_div=0.3, stk_bo_rate=0.1, stk_co_rate=0.2)
    events = normalize_tushare_dividend(pd.DataFrame([row]))

    assert len(events) == 2  # one cash event and one share event
    share = events[events["event_type"] == "stock_dividend"]
    assert len(share) == 1
    assert share.iloc[0]["share_multiplier"] == pytest.approx(1.3)


def test_normalize_stk_div_component_conflict_fails_closed() -> None:
    row = _implementation_row(stk_div=0.4, stk_bo_rate=0.1, stk_co_rate=0.2)
    with pytest.raises(ValueError, match="inconsistent stk_div"):
        normalize_tushare_dividend(pd.DataFrame([row]))


def _implementation_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "ts_code": "A",
        "div_proc": "实施",
        "ex_date": "2025-05-23",
        "end_date": "2025-12-31",
        "record_date": "2025-05-22",
        "pay_date": "2025-06-01",
        "div_listdate": "2025-06-02",
        "cash_div": 0.4,
        "cash_div_tax": 0.506,
        "stk_div": 0.1,
        "stk_bo_rate": 0.1,
        "stk_co_rate": 0.0,
        "ann_date": "2025-05-01",
        "imp_ann_date": "2025-05-01",
    }
    row.update(overrides)
    return row


def test_normalize_deduplicates_same_implementation_and_keeps_latest_announcement() -> None:
    older = _implementation_row(ann_date="2025-05-01", imp_ann_date="2025-05-01")
    newer = _implementation_row(ann_date="2025-05-10", imp_ann_date="2025-05-09")
    events = normalize_tushare_dividend(pd.DataFrame([older, newer]))

    assert set(events["event_type"]) == {"cash_dividend", "stock_dividend"}
    assert len(events) == 2
    # The selected row is the one with the latest ann_date; its available
    # implementation announcement metadata is retained in the normalized row.
    assert set(events["imp_ann_date"]) == {"2025-05-09"}


def test_normalize_keeps_distinct_same_day_entitlements() -> None:
    first = _implementation_row(cash_div_tax=0.506, ann_date="2025-05-01")
    second = _implementation_row(cash_div_tax=0.507, ann_date="2025-05-02")
    events = normalize_tushare_dividend(pd.DataFrame([first, second]))

    cash = events[events["event_type"] == "cash_dividend"]
    assert len(cash) == 2
    assert set(cash["cash_per_share"]) == {0.506, 0.507}


def test_normalize_economic_deduplication_is_input_order_independent() -> None:
    rows = [
        _implementation_row(ann_date="2025-05-01", imp_ann_date="2025-05-01"),
        _implementation_row(ann_date="2025-05-10", imp_ann_date="2025-05-09"),
        _implementation_row(cash_div_tax=0.507, ann_date="2025-05-02"),
    ]
    forward = normalize_tushare_dividend(pd.DataFrame(rows))
    reverse = normalize_tushare_dividend(pd.DataFrame(list(reversed(rows))))
    pd.testing.assert_frame_equal(
        forward.reset_index(drop=True), reverse.reset_index(drop=True)
    )


def test_manifest_keeps_raw_artifact_hash_separate(tmp_path) -> None:
    events = pd.DataFrame([{
        "event_id": "s1", "instrument": "A", "effective_date": "2026-01-02",
        "event_type": "split", "share_multiplier": 2.0,
    }])
    raw_path = tmp_path / "raw_dividend.bin"
    raw_path.write_bytes(b"raw dividend source")
    raw_digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    root = write_corporate_action_artifact(
        events, tmp_path, artifact_name="raw_bound", source_raw_artifact_sha256=raw_digest,
        source_raw_path=str(raw_path),
    )
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["source_raw_artifact_sha256"] == raw_digest
    assert manifest["source_raw_path"] == "source/raw_dividend.bin"
    assert manifest["source_event_rows_sha256"] != raw_digest
    CorporateActionStore(tmp_path, "raw_bound")
    (root / manifest["source_raw_path"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="raw-source SHA256"):
        CorporateActionStore(tmp_path, "raw_bound")
    with pytest.raises(FileExistsError):
        write_corporate_action_artifact(events, tmp_path, artifact_name="raw_bound")


def test_writer_and_store_reject_invalid_settlement_dates(tmp_path) -> None:
    invalid = pd.DataFrame([{
        "event_id": "d1", "instrument": "A", "effective_date": "2026-01-03",
        "event_type": "cash_dividend", "cash_per_share": 1.0,
        "share_multiplier": 1.0, "settlement_date": "2026-01-02",
    }])
    with pytest.raises(ValueError, match="precedes effective_date"):
        write_corporate_action_artifact(invalid, tmp_path, artifact_name="invalid_writer")

    valid = invalid.copy()
    valid["settlement_date"] = "2026-01-04"
    root = write_corporate_action_artifact(valid, tmp_path, artifact_name="invalid_store")
    frame = pd.read_parquet(root / "events.parquet")
    frame["settlement_date"] = "2026-01-02"
    frame.to_parquet(root / "events.parquet", index=False)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["events_sha256"] = hashlib.sha256((root / "events.parquet").read_bytes()).hexdigest()
    core = {key: value for key, value in manifest.items() if key not in {"manifest_sha256", "identity"}}
    manifest_sha = hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    manifest["manifest_sha256"] = manifest_sha
    manifest["identity"] = {"name": "invalid_store", "events_sha256": manifest["events_sha256"],
                            "manifest_sha256": manifest_sha}
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="precedes effective_date"):
        CorporateActionStore(tmp_path, "invalid_store")
