from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from qsys.research.pit_universe import PitUniverseStore

# Synthetic membership: three names.
#   A.SZ   member 2018-01-01 .. 2020-12-31 (continuous)
#   B.SZ   member 2019-01-01 .. 2019-12-31 (single period)
#   C.SZ   member 2018-06-01 .. 2018-12-31, then leaves and rejoins 2021-01-01..2021-12-31
SYNTH_SPANS = [
    ("000906.SH", "A.SZ", "20180101", "20201231", "tushare_index_weight", "2026-01-01", "index_weight_monthly"),
    ("000906.SH", "B.SZ", "20190101", "20191231", "tushare_index_weight", "2026-01-01", "index_weight_monthly"),
    ("000906.SH", "C.SZ", "20180601", "20181231", "tushare_index_weight", "2026-01-01", "index_weight_monthly"),
    ("000906.SH", "C.SZ", "20210101", "20211231", "tushare_index_weight", "2026-01-01", "index_weight_monthly"),
]


def _write_artifact(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(
        SYNTH_SPANS,
        columns=[
            "index_code",
            "instrument",
            "effective_from",
            "effective_to",
            "source",
            "source_date",
            "source_version",
        ],
    )
    path = directory / "membership.parquet"
    frame.to_parquet(path, index=False)
    manifest = {
        "universe_id": "csi800_pit_v1",
        "membership_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "raw_source_hash": "x" * 64,
        "source": "tushare_index_weight",
        "source_date": "2026-08-20",
        "n_snapshots": 4,
        "snapshot_date_range": ["20180101", "20211231"],
        "n_unique_instruments": 3,
        "n_membership_spans": 4,
        "description": "test fixture",
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    return path


def test_load_verifies_hash(tmp_path: Path) -> None:
    path = _write_artifact(tmp_path)
    store = PitUniverseStore(tmp_path)
    assert store.provenance.membership_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_tampered_artifact_raises(tmp_path: Path) -> None:
    path = _write_artifact(tmp_path)
    # silently alter a span after the manifest was written
    frame = pd.read_parquet(path)
    frame.loc[0, "effective_to"] = "20201230"
    frame.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="hash mismatch"):
        PitUniverseStore(tmp_path)


def test_verify_hash_can_be_disabled(tmp_path: Path) -> None:
    path = _write_artifact(tmp_path)
    frame = pd.read_parquet(path)
    frame.loc[0, "effective_to"] = "20201230"
    frame.to_parquet(path, index=False)
    store = PitUniverseStore(tmp_path, verify_hash=False)
    assert store.is_member("A.SZ", "2020-12-30")


def test_membership_as_of_and_is_member(tmp_path: Path) -> None:
    _write_artifact(tmp_path)
    store = PitUniverseStore(tmp_path)
    # mid-2018: A (continuous), C (first period); B not yet
    members = store.membership_as_of("2018-06-15")
    assert members == ["A.SZ", "C.SZ"]
    assert store.is_member("A.SZ", "2018-06-15")
    assert store.is_member("C.SZ", "2018-06-15")
    assert not store.is_member("B.SZ", "2018-06-15")
    # 2019-06-15: A + B
    assert store.membership_as_of("2019-06-15") == ["A.SZ", "B.SZ"]
    # 2021-06-15: only C (rejoined); A left end-2020
    assert store.membership_as_of("2021-06-15") == ["C.SZ"]
    # supports YYYYMMDD and Timestamp
    assert store.membership_as_of(pd.Timestamp("2019-06-15")) == ["A.SZ", "B.SZ"]
    assert store.membership_as_of("20190615") == ["A.SZ", "B.SZ"]


def test_membership_window_union(tmp_path: Path) -> None:
    _write_artifact(tmp_path)
    store = PitUniverseStore(tmp_path)
    assert store.membership_window("2018-01-01", "2018-12-31") == ["A.SZ", "C.SZ"]
    assert store.membership_window("2019-01-01", "2021-12-31") == ["A.SZ", "B.SZ", "C.SZ"]
    with pytest.raises(ValueError, match="start_date"):
        store.membership_window("2021-12-31", "2021-01-01")


def test_latest_membership_and_periods(tmp_path: Path) -> None:
    _write_artifact(tmp_path)
    store = PitUniverseStore(tmp_path)
    assert store.latest_membership() == ["C.SZ"]
    periods = store.membership_periods("C.SZ")
    assert len(periods) == 2
    assert periods.iloc[0]["effective_from"] == "20180601"
    assert periods.iloc[1]["effective_from"] == "20210101"
    assert store.membership_periods("NOPE.SZ").empty


def test_to_registry_frame_clips_to_window(tmp_path: Path) -> None:
    _write_artifact(tmp_path)
    store = PitUniverseStore(tmp_path)
    registry = store.to_registry_frame("2018-01-01", "2021-12-31")
    # A: one period in window; B: one; C: two disjoint periods
    assert len(registry) == 4
    c_rows = registry[registry["instrument"] == "C.SZ"].sort_values("start_date")
    assert c_rows.iloc[0]["start_date"] == "2018-06-01"
    assert c_rows.iloc[0]["end_date"] == "2018-12-31"
    assert c_rows.iloc[1]["start_date"] == "2021-01-01"
    # A's end clipped to window end
    a_row = registry[registry["instrument"] == "A.SZ"].iloc[0]
    assert a_row["start_date"] == "2018-01-01"
    assert a_row["end_date"] == "2020-12-31"
    # mid-window start clips the left edge
    registry2 = store.to_registry_frame("2019-06-01", "2021-12-31")
    a_row2 = registry2[registry2["instrument"] == "A.SZ"].iloc[0]
    assert a_row2["start_date"] == "2019-06-01"


def test_invalid_span_raises(tmp_path: Path) -> None:
    directory = tmp_path / "bad"
    directory.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(
        [("000906.SH", "A.SZ", "20200101", "20190101", "tushare_index_weight", "2026-01-01", "x")],
        columns=[
            "index_code",
            "instrument",
            "effective_from",
            "effective_to",
            "source",
            "source_date",
            "source_version",
        ],
    )
    path = directory / "membership.parquet"
    frame.to_parquet(path, index=False)
    manifest = {
        "universe_id": "x",
        "membership_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="effective_from > effective_to"):
        PitUniverseStore(directory)


@pytest.mark.skipif(
    not (Path("data/research/universes/csi800_pit_v2/membership.parquet").is_file()),
    reason="csi800_pit_v2 artifact not present",
)
def test_real_artifact_consistency() -> None:
    """Integration: the committed-locally artifact must satisfy core invariants."""
    store = PitUniverseStore()
    prov = store.provenance
    assert prov.universe_id == "csi800_pit_v2"
    assert prov.n_unique_instruments == len(store.instruments)
    assert prov.n_membership_spans == len(store.spans)
    latest = store.latest_membership()
    assert len(latest) == 800, f"latest membership has {len(latest)} names, expected 800"
    # spot-check point-in-time sizes near a mid-window date
    for probe in ("2018-03-13", "2020-12-31", "2023-06-30"):
        size = len(store.membership_as_of(probe))
        assert size == 800, f"membership on {probe} has {size} names"
    # a known delisted-era name must resolve on its PIT membership date
    assert store.is_member("000002.SZ", "2026-07-31")
