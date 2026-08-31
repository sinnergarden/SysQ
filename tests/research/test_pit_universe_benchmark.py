from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from qsys.research.pit_universe_benchmark import (
    validate_pit_universe_benchmark,
    write_pit_universe_benchmark,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_universe(path: Path) -> None:
    path.mkdir(parents=True)
    membership = pd.DataFrame([
        {
            "index_code": "TEST",
            "instrument": instrument,
            "effective_from": "20200101",
            "effective_to": "20241231",
            "source": "fixture",
            "source_date": "20240101",
            "source_version": "v1",
        }
        for instrument in ("A.SZ", "B.SZ")
    ])
    membership.to_parquet(path / "membership.parquet", index=False)
    (path / "manifest.json").write_text(
        json.dumps({
            "universe_id": "test_pit_union",
            "membership_sha256": _sha256(path / "membership.parquet"),
            "raw_source_hash": "fixture",
            "source": "fixture",
            "source_date": "20240101",
            "n_snapshots": 1,
            "snapshot_date_range": ["20200101", "20241231"],
            "n_unique_instruments": 2,
            "n_membership_spans": 2,
            "description": "fixture",
        }),
        encoding="utf-8",
    )


def _write_market_data(path: Path) -> None:
    path.mkdir(parents=True)
    pd.DataFrame({
        "trade_date": ["20231229", "20240102", "20240103", "20240104"],
        "open": [10.0, 10.0, 11.0, 11.5],
        "close": [10.0, 11.0, 11.5, 12.0],
        "factor": [1.0, 1.0, 1.0, 1.0],
        "circ_mv": [100.0, 110.0, 115.0, 120.0],
    }).to_feather(path / "A.SZ.feather")
    pd.DataFrame({
        "trade_date": ["20231229", "20240102", "20240104"],
        "open": [20.0, 20.0, 10.0],
        "close": [20.0, 20.0, 10.0],
        "factor": [1.0, 1.0, 2.0],
        "circ_mv": [300.0, 300.0, 300.0],
    }).to_feather(path / "B.SZ.feather")


def test_pit_benchmark_uses_strict_prior_caps_and_zero_carries_suspension(
    tmp_path: Path,
) -> None:
    universe = tmp_path / "universe"
    market = tmp_path / "market"
    output = tmp_path / "benchmark"
    calendar = tmp_path / "calendar.csv"
    _write_universe(universe)
    _write_market_data(market)
    pd.DataFrame({"trade_date": ["20240102", "20240103", "20240104"]}).to_csv(
        calendar, index=False
    )

    result = write_pit_universe_benchmark(
        benchmark_id="test_pit_float_cap_total_return_proxy",
        universe_artifact=universe,
        canonical_data_root=market,
        calendar_csv=calendar,
        output_dir=output,
        start_date="2024-01-02",
        end_date="2024-01-04",
        holdout_start="2025-01-02",
        min_constituent_coverage=1.0,
    )

    benchmark = pd.read_csv(output / "benchmark.csv")
    coverage = pd.read_csv(output / "daily_constituent_coverage.csv")
    assert benchmark["daily_return"].tolist() == pytest.approx([
        100.0 / 400.0 * 0.10,
        110.0 / 410.0 * (11.5 / 11.0 - 1.0),
        115.0 / 415.0 * (12.0 / 11.5 - 1.0),
    ])
    assert coverage["zero_return_carry_count"].tolist() == [0, 1, 0]
    assert coverage["constituent_count_coverage"].tolist() == [1.0, 1.0, 1.0]
    validation = validate_pit_universe_benchmark(output)
    assert validation["validation"] == "passed"
    assert validation["benchmark_identity_sha256"] == result[
        "benchmark_identity_sha256"
    ]


def test_pit_benchmark_rejects_holdout_overlap(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="overlaps declared holdout"):
        write_pit_universe_benchmark(
            benchmark_id="blocked",
            universe_artifact=tmp_path / "not_read",
            canonical_data_root=tmp_path / "not_read",
            calendar_csv=tmp_path / "not_read.csv",
            output_dir=tmp_path / "not_written",
            start_date="2024-01-02",
            end_date="2025-01-02",
            holdout_start="2025-01-02",
        )
