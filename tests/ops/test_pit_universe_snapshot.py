from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from qsys.ops.pit_universe_snapshot import (
    resolve_csi1800_pit_snapshot,
    write_current_qlib_registry,
)


class _Collector:
    def __init__(self, *, future_only: bool = False, overlap: bool = False):
        self.future_only = future_only
        self.overlap = overlap

    def get_index_weights(self, index_code: str) -> pd.DataFrame:
        count = 800 if index_code == "000906.SH" else 1000
        prefix = "A" if index_code == "000906.SH" else "B"
        current = [f"{prefix}{number:04d}.SZ" for number in range(count)]
        if self.overlap and index_code == "000852.SH":
            current[0] = "A0000.SZ"
        dates = ["20260731", "20260831"]
        if self.future_only:
            dates = ["20260831"]
        rows = []
        for trade_date in dates:
            rows.extend(
                {
                    "index_code": index_code,
                    "con_code": instrument,
                    "trade_date": trade_date,
                    "weight": 1.0,
                }
                for instrument in current
            )
        return pd.DataFrame(rows)


def test_snapshot_uses_latest_published_date_not_future(tmp_path: Path):
    result = resolve_csi1800_pit_snapshot(
        _Collector(),
        as_of_date="2026-08-21",
        project_root=tmp_path,
        apply=False,
    )

    assert len(result.instruments) == 1800
    assert result.source_snapshot_dates == {
        "000852.SH": "20260731",
        "000906.SH": "20260731",
    }
    assert result.artifact_dir is None
    assert not (tmp_path / "data").exists()


def test_snapshot_is_immutable_and_idempotently_reused(tmp_path: Path):
    first = resolve_csi1800_pit_snapshot(
        _Collector(),
        as_of_date="20260821",
        project_root=tmp_path,
        apply=True,
    )
    second = resolve_csi1800_pit_snapshot(
        _Collector(),
        as_of_date="20260821",
        project_root=tmp_path,
        apply=True,
    )

    assert first.artifact_dir == second.artifact_dir
    assert first.reused is False
    assert second.reused is True
    assert first.membership_sha256 == second.membership_sha256
    manifest = json.loads((first.artifact_dir / "manifest.json").read_text())
    assert manifest["constituent_count"] == 1800
    assert manifest["semantic_sha256"] == first.semantic_sha256


def test_snapshot_fails_closed_without_asof_source():
    with pytest.raises(ValueError, match="on or before target"):
        resolve_csi1800_pit_snapshot(
            _Collector(future_only=True),
            as_of_date="20260821",
            project_root=Path("/unused"),
            apply=False,
        )


def test_snapshot_rejects_overlap_between_indices():
    with pytest.raises(ValueError, match="overlap"):
        resolve_csi1800_pit_snapshot(
            _Collector(overlap=True),
            as_of_date="20260821",
            project_root=Path("/unused"),
            apply=False,
        )


def test_current_registry_requires_all_1800_members(tmp_path: Path):
    instruments = [f"A{number:04d}.SZ" for number in range(1800)]
    registry_dir = tmp_path / "instruments"
    registry_dir.mkdir(parents=True)
    pd.DataFrame(
        {
            "instrument": instruments,
            "start": ["2018-01-01"] * 1800,
            "end": ["2026-08-20"] * 1800,
        }
    ).to_csv(
        registry_dir / "all.txt", sep="\t", header=False, index=False
    )

    result = write_current_qlib_registry(
        qlib_dir=tmp_path,
        universe="csi1800",
        instruments=instruments,
        as_of_date="20260821",
    )

    assert result["instrument_count"] == 1800
    written = pd.read_csv(
        registry_dir / "csi1800.txt", sep="\t", header=None, dtype=str
    )
    assert len(written) == 1800
    assert set(written[2]) == {"2026-08-21"}
