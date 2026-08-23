from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from qsys.ops.shareholder_sync import (
    _paged_call,
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
    assert repair["status"] == "healthy"
    assert Path(repair["summary_path"]) == (
        output_dir / "shareholder_repair_summary.json"
    )


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
