"""Tests for qsys.research.manifest — RunManifest."""

from __future__ import annotations

from pathlib import Path

import pytest

from qsys.research.manifest import (
    RunManifest,
    load_run_manifest,
    write_run_manifest,
)


def _sample_manifest(**overrides) -> RunManifest:
    kwargs = dict(
        run_id="20260528_a1b2c3d4e5f6g7h8",
        run_name="alpha_v1_20260501",
        created_at="2026-05-28T10:00:00",
        model_id="alpha_v1_20260525",
        feature_set_id="csi300_daily_v3",
        label_id="forward_return_5d",
    )
    kwargs.update(overrides)
    return RunManifest(**kwargs)


class TestRunManifestConstruction:
    def test_minimal(self) -> None:
        m = RunManifest(run_id="r1", run_name="test", created_at="now")
        assert m.run_id == "r1"
        assert m.model_id is None

    def test_full(self) -> None:
        m = _sample_manifest()
        assert m.model_id == "alpha_v1_20260525"
        assert m.feature_set_id == "csi300_daily_v3"

    def test_rejects_empty_run_id(self) -> None:
        with pytest.raises(ValueError):
            RunManifest(run_id="", run_name="x", created_at="now")

    def test_rejects_empty_run_name(self) -> None:
        with pytest.raises(ValueError):
            RunManifest(run_id="r1", run_name="", created_at="now")

    def test_tags_and_params(self) -> None:
        m = RunManifest(
            run_id="r1", run_name="t", created_at="now",
            tags={"env": "test"}, params={"top_n": 20},
        )
        assert m.tags["env"] == "test"
        assert m.params["top_n"] == 20


class TestRunManifestRoundtrip:
    def test_to_dict_roundtrip(self) -> None:
        m1 = _sample_manifest(description="hello")
        d = m1.to_dict()
        m2 = RunManifest.from_dict(d)
        assert m1 == m2

    def test_json_roundtrip(self, tmp_path: Path) -> None:
        m1 = _sample_manifest()
        text = m1.to_json()
        m2 = RunManifest.from_json(text)
        assert m1 == m2

    def test_write_and_load(self, tmp_path: Path) -> None:
        m1 = _sample_manifest()
        write_run_manifest(m1, tmp_path)
        assert (tmp_path / "manifest.json").exists()
        m2 = load_run_manifest(tmp_path)
        assert m1 == m2

    def test_load_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_run_manifest(tmp_path / "nonexistent")
