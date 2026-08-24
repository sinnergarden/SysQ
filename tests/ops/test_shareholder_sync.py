from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from qsys.ops.shareholder_sync import (
    _calendar_year_chunks,
    _paged_call,
    fetch_shareholder_backfill,
    inspect_shareholder_sidecar_health,
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
