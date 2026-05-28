"""Tests for qsys.label.store."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from qsys.label.schema import LabelRecord, LabelSpec
from qsys.label.store import LabelStore


class TestLabelStore:
    def test_save_and_load(self, tmp_path: Path) -> None:
        store = LabelStore(tmp_path / "labels")
        records = [
            LabelRecord("2026-05-01", "000001.SZ", 0.05),
            LabelRecord("2026-05-01", "000002.SZ", -0.02, weight=0.8),
        ]
        path = store.save("fr_5d", records)
        assert path.exists()
        assert path.name == "fr_5d.parquet"

        df = store.load("fr_5d")
        assert len(df) == 2
        assert list(df.columns) == ["date", "instrument", "value", "weight"]
        assert df["value"].iloc[0] == 0.05

    def test_load_missing_raises(self, tmp_path: Path) -> None:
        store = LabelStore(tmp_path / "labels")
        with pytest.raises(FileNotFoundError):
            store.load("nonexistent")

    def test_list(self, tmp_path: Path) -> None:
        store = LabelStore(tmp_path / "labels")
        store.save("a", [LabelRecord("d", "i", 1.0)])
        store.save("b", [LabelRecord("d", "i", 2.0)])
        assert store.list() == ["a", "b"]

    def test_list_empty(self, tmp_path: Path) -> None:
        store = LabelStore(tmp_path / "labels")
        assert store.list() == []

    def test_save_with_spec(self, tmp_path: Path) -> None:
        store = LabelStore(tmp_path / "labels")
        spec = LabelSpec(label_id="fr_5d", kind="forward_return", horizon=5)
        store.save("fr_5d", [], spec=spec)
        spec_path = tmp_path / "labels" / "fr_5d.spec.json"
        assert spec_path.exists()

        loaded = store.load_spec("fr_5d")
        assert loaded is not None
        assert loaded.label_id == "fr_5d"
        assert loaded.horizon == 5

    def test_load_spec_missing(self, tmp_path: Path) -> None:
        store = LabelStore(tmp_path / "labels")
        assert store.load_spec("nonexistent") is None

    def test_save_empty_records(self, tmp_path: Path) -> None:
        store = LabelStore(tmp_path / "labels")
        store.save("empty", [])
        df = store.load("empty")
        assert df.empty
        assert list(df.columns) == ["date", "instrument", "value", "weight"]
