from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

import qsys.research.pit_universe as pit_module
from qsys.research.pit_universe import (
    PitUniverseStore,
    build_membership_spans,
    rebuild_pit_universes_v2,
)


META = {
    "source": "test_snapshots",
    "source_date": "2026-08-22",
    "source_version": "monthly",
}


def _build(rows: list[tuple[str, str, object]]) -> pd.DataFrame:
    raw = pd.DataFrame(rows, columns=["index_code", "con_code", "trade_date"])
    return build_membership_spans(raw, **META)


def test_carries_membership_until_next_absent_snapshot() -> None:
    spans = _build(
        [
            ("index-a", "a.sz", "2024-01-31"),
            ("index-a", "A.SZ", "2024/02/29"),
            ("index-a", "B.SZ", "20240331"),
        ]
    )

    a = spans[spans["instrument"] == "A.SZ"].iloc[0]
    assert a["effective_from"] == "20240131"
    assert a["effective_to"] == "20240330"
    assert a["index_code"] == "INDEX-A"


def test_absence_and_reentry_produce_a_real_gap() -> None:
    spans = _build(
        [
            ("I", "A", "20240131"),
            ("I", "B", "20240229"),
            ("I", "A", "20240331"),
        ]
    )

    a = spans[spans["instrument"] == "A"].reset_index(drop=True)
    assert a[["effective_from", "effective_to"]].values.tolist() == [
        ["20240131", "20240228"],
        ["20240331", "20240331"],
    ]


def test_each_index_uses_its_own_snapshot_axis() -> None:
    spans = _build(
        [
            ("I1", "A", "20240131"),
            ("I1", "A", "20240331"),
            ("I2", "A", "20240229"),
            ("I2", "B", "20240315"),
        ]
    )

    i1 = spans[(spans["index_code"] == "I1") & (spans["instrument"] == "A")]
    i2 = spans[(spans["index_code"] == "I2") & (spans["instrument"] == "A")]
    assert i1[["effective_from", "effective_to"]].values.tolist() == [
        ["20240131", "20240331"]
    ]
    assert i2[["effective_from", "effective_to"]].values.tolist() == [
        ["20240229", "20240314"]
    ]


@pytest.mark.parametrize(
    ("rows", "match"),
    [
        ([], "empty"),
        ([(None, "A", "20240131")], "null index_code"),
        ([("I", " ", "20240131")], "blank con_code"),
        ([("I", "A", "20240231")], "cannot normalize"),
        (
            [("i", "a", "2024-01-31"), (" I ", " A ", "20240131")],
            "duplicate",
        ),
    ],
)
def test_invalid_snapshot_inputs_fail(
    rows: list[tuple[str | None, str, object]], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        _build(rows)


def test_missing_required_column_fails() -> None:
    raw = pd.DataFrame({"index_code": ["I"], "trade_date": ["20240131"]})
    with pytest.raises(ValueError, match="missing columns.*con_code"):
        build_membership_spans(raw, **META)


def _write_source_artifact(
    root: Path,
    artifact_id: str,
    raw: pd.DataFrame,
) -> None:
    artifact = root / "data" / "research" / "universes" / artifact_id
    raw_dir = artifact / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / "index_weight_snapshots.parquet"
    raw.to_parquet(raw_path, index=False)
    (artifact / "manifest.json").write_text(
        json.dumps(
            {
                "universe_id": artifact_id,
                "source": "synthetic_index_weight",
                "source_endpoint": "fixture.index_weight",
                "source_date": "2026-08-22",
                "source_version": "fixture_monthly",
                "raw_source_hash": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )


def _constant_snapshot_raw(
    index_code: str,
    size: int,
    dates: list[str],
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            (index_code, f"{number:06d}.SZ", date, 1.0)
            for date in dates
            for number in range(size)
        ],
        columns=["index_code", "con_code", "trade_date", "weight"],
    )


def _synthetic_project(root: Path) -> None:
    dates = ["20180102", "20180131", "20180228"]
    csi800 = _constant_snapshot_raw("000906.SH", 800, dates)
    final_rows = csi800.index[csi800["trade_date"] == dates[-1]]
    csi800.loc[final_rows[0], "con_code"] = "900000.SZ"
    csi1800 = _constant_snapshot_raw("000999.SH", 1800, dates)
    _write_source_artifact(root, "csi800_pit_v1", csi800)
    _write_source_artifact(root, "csi1800_pit_v1", csi1800)
    calendar = root / "data" / "qlib_bin" / "calendars" / "day.txt"
    calendar.parent.mkdir(parents=True, exist_ok=True)
    calendar.write_text(
        "2018-01-02\n2018-01-15\n2018-01-31\n2018-02-15\n2018-02-28\n",
        encoding="utf-8",
    )


def _clean_git_provenance(_root: Path) -> dict[str, object]:
    return {
        "git_commit_full": "a" * 40,
        "git_commit_short": "a" * 8,
        "git_worktree_dirty": False,
        "git_scoped_dirty": False,
        "git_scoped_paths": ["qsys/research/pit_universe.py"],
    }


def test_rebuild_publishes_valid_v2_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _synthetic_project(tmp_path)
    monkeypatch.setattr(pit_module, "_git_provenance", _clean_git_provenance)

    result = rebuild_pit_universes_v2(tmp_path)

    assert set(result) == {"csi800_pit_v2", "csi1800_pit_v2"}
    for artifact_id, expected in (
        ("csi800_pit_v2", 800),
        ("csi1800_pit_v2", 1800),
    ):
        artifact = tmp_path / "data" / "research" / "universes" / artifact_id
        manifest = json.loads((artifact / "manifest.json").read_text())
        store = PitUniverseStore(artifact)
        assert manifest["schema_version"] == "pit_universe_manifest_v2"
        assert manifest["interval_semantics"] == "snapshot_asof_carry_forward"
        assert manifest["expected_daily_membership"] == expected
        assert manifest["n_validated_trading_dates"] == 5
        assert len(store.membership_as_of("2018-02-15")) == expected
        assert manifest["membership_sha256"] == hashlib.sha256(
            (artifact / "membership.parquet").read_bytes()
        ).hexdigest()
        source_manifest = (
            tmp_path
            / "data"
            / "research"
            / "universes"
            / artifact_id.replace("_v2", "_v1")
            / "manifest.json"
        )
        assert manifest["source_manifest_sha256"] == hashlib.sha256(
            source_manifest.read_bytes()
        ).hexdigest()
        registry = (
            tmp_path
            / "data"
            / "qlib_bin"
            / "instruments"
            / f"{artifact_id.removesuffix('_v2')}_union.txt"
        )
        assert manifest["registry_sha256"] == hashlib.sha256(
            registry.read_bytes()
        ).hexdigest()


def test_rebuild_refuses_existing_v2_without_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _synthetic_project(tmp_path)
    monkeypatch.setattr(pit_module, "_git_provenance", _clean_git_provenance)
    rebuild_pit_universes_v2(tmp_path)

    with pytest.raises(FileExistsError, match="already exist"):
        rebuild_pit_universes_v2(tmp_path)


def test_rebuild_refuses_tampered_source_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _synthetic_project(tmp_path)
    monkeypatch.setattr(pit_module, "_git_provenance", _clean_git_provenance)
    raw_path = (
        tmp_path
        / "data/research/universes/csi800_pit_v1/raw/index_weight_snapshots.parquet"
    )
    raw_path.write_bytes(raw_path.read_bytes() + b"tampered")

    with pytest.raises(ValueError, match="raw snapshot hash mismatch"):
        rebuild_pit_universes_v2(tmp_path)


def test_store_refuses_manifest_without_membership_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _synthetic_project(tmp_path)
    monkeypatch.setattr(pit_module, "_git_provenance", _clean_git_provenance)
    rebuild_pit_universes_v2(tmp_path)
    artifact = tmp_path / "data/research/universes/csi800_pit_v2"
    manifest_path = artifact / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.pop("membership_sha256")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="lacks membership_sha256"):
        PitUniverseStore(artifact)
