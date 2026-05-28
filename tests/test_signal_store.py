"""Tests for qsys.signal.store."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from qsys.signal.schema import SignalRecord, SignalSpec
from qsys.signal.store import SignalStore


class TestSignalStore:
    def test_save_and_load(self, tmp_path: Path) -> None:
        store = SignalStore(tmp_path / "signals")
        records = [
            SignalRecord("2026-05-01", "000001.SZ", 1.5),
            SignalRecord("2026-05-01", "000002.SZ", -0.3),
        ]
        path = store.save("alpha_v1_blended", records)
        assert path.exists()
        assert path.name == "alpha_v1_blended.parquet"

        df = store.load("alpha_v1_blended")
        assert len(df) == 2
        assert list(df.columns) == ["date", "instrument", "value"]
        assert df["value"].iloc[0] == 1.5

    def test_load_missing_raises(self, tmp_path: Path) -> None:
        store = SignalStore(tmp_path / "signals")
        with pytest.raises(FileNotFoundError):
            store.load("nonexistent")

    def test_list(self, tmp_path: Path) -> None:
        store = SignalStore(tmp_path / "signals")
        store.save("a", [SignalRecord("d", "i", 1.0)])
        store.save("b", [SignalRecord("d", "i", 2.0)])
        assert store.list() == ["a", "b"]

    def test_list_empty(self, tmp_path: Path) -> None:
        store = SignalStore(tmp_path / "signals")
        assert store.list() == []

    def test_save_with_spec(self, tmp_path: Path) -> None:
        store = SignalStore(tmp_path / "signals")
        spec = SignalSpec(signal_id="test", kind="score")
        store.save("test", [], spec=spec)
        spec_path = tmp_path / "signals" / "test.spec.json"
        assert spec_path.exists()

        loaded = store.load_spec("test")
        assert loaded is not None
        assert loaded.signal_id == "test"

    def test_load_spec_missing(self, tmp_path: Path) -> None:
        store = SignalStore(tmp_path / "signals")
        assert store.load_spec("nonexistent") is None

    def test_save_empty_records(self, tmp_path: Path) -> None:
        store = SignalStore(tmp_path / "signals")
        store.save("empty", [])
        df = store.load("empty")
        assert df.empty
        assert list(df.columns) == ["date", "instrument", "value"]
