"""Tests for qsys.label.store."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from qsys.label.store import LabelStore


def _make_valid_frame(label_id: str = "fr_5d", n: int = 3) -> pd.DataFrame:
    return pd.DataFrame({
        "trade_date": [f"2026-05-{d:02d}" for d in range(1, n + 1)],
        "instrument": [f"00000{i}.SZ" for i in range(n)],
        "label_id": [label_id] * n,
        "horizon": [5] * n,
        "label_value": [0.01 * i for i in range(n)],
    })


class TestLabelStoreSave:
    def test_save_and_load(self, tmp_path: Path) -> None:
        store = LabelStore(str(tmp_path))
        frame = _make_valid_frame()
        path = store.save_labels("fr_5d", frame)
        assert path.exists()
        loaded = store.load_labels("fr_5d")
        assert len(loaded) == 3
        assert list(loaded.columns)[:5] == ["trade_date", "instrument", "label_id", "horizon", "label_value"]

    def test_manifest_written(self, tmp_path: Path) -> None:
        store = LabelStore(str(tmp_path))
        store.save_labels("fr_5d", _make_valid_frame())
        mf = store.load_manifest("fr_5d")
        assert mf["label_id"] == "fr_5d"
        assert mf["row_count"] == 3
        assert "created_at" in mf

    def test_missing_required_column_fails(self, tmp_path: Path) -> None:
        store = LabelStore(str(tmp_path))
        bad = pd.DataFrame({"trade_date": ["2026-05-01"], "instrument": ["i"]})
        with pytest.raises(ValueError, match="Missing required columns"):
            store.save_labels("fr_5d", bad)

    def test_mismatched_label_id_fails(self, tmp_path: Path) -> None:
        store = LabelStore(str(tmp_path))
        frame = _make_valid_frame(label_id="other_id")
        with pytest.raises(ValueError, match="label_id != 'fr_5d'"):
            store.save_labels("fr_5d", frame)

    def test_overwrite_false_protects_existing(self, tmp_path: Path) -> None:
        store = LabelStore(str(tmp_path))
        store.save_labels("fr_5d", _make_valid_frame())
        with pytest.raises(FileExistsError):
            store.save_labels("fr_5d", _make_valid_frame())

    def test_overwrite_true_allows_second_save(self, tmp_path: Path) -> None:
        store = LabelStore(str(tmp_path))
        store.save_labels("fr_5d", _make_valid_frame(n=3))
        store.save_labels("fr_5d", _make_valid_frame(n=5), overwrite=True)
        loaded = store.load_labels("fr_5d")
        assert len(loaded) == 5

    @pytest.mark.parametrize("null_col", ["trade_date", "instrument"])
    def test_null_in_required_column_fails(self, tmp_path: Path, null_col: str) -> None:
        store = LabelStore(str(tmp_path))
        frame = _make_valid_frame()
        frame.loc[0, null_col] = None
        with pytest.raises(ValueError, match=f"{null_col}.*null"):
            store.save_labels("fr_5d", frame)

    def test_null_horizon_fails(self, tmp_path: Path) -> None:
        store = LabelStore(str(tmp_path))
        frame = _make_valid_frame()
        frame.loc[0, "horizon"] = None
        with pytest.raises(ValueError, match="horizon.*null"):
            store.save_labels("fr_5d", frame)

    def test_csv_file_format(self, tmp_path: Path) -> None:
        store = LabelStore(str(tmp_path))
        frame = _make_valid_frame(label_id="test_csv")
        path = store.save_labels("test_csv", frame, file_format="csv")
        assert path.suffix == ".csv"
        loaded = store.load_labels("test_csv")
        assert len(loaded) == 3


class TestLabelStoreLoad:
    def test_filter_by_date(self, tmp_path: Path) -> None:
        store = LabelStore(str(tmp_path))
        store.save_labels("fr_5d", _make_valid_frame(n=5))
        loaded = store.load_labels("fr_5d", start_date="2026-05-03")
        assert len(loaded) == 3

    def test_filter_by_instruments(self, tmp_path: Path) -> None:
        store = LabelStore(str(tmp_path))
        store.save_labels("fr_5d", _make_valid_frame(n=5))
        loaded = store.load_labels("fr_5d", instruments=["000000.SZ"])
        assert len(loaded) == 1

    def test_load_missing_raises(self, tmp_path: Path) -> None:
        store = LabelStore(str(tmp_path))
        with pytest.raises(FileNotFoundError):
            store.load_labels("nonexistent")


class TestLabelStoreList:
    def test_list_labels(self, tmp_path: Path) -> None:
        store = LabelStore(str(tmp_path))
        store.save_labels("fr_5d", _make_valid_frame(n=5))
        store.save_labels("fr_10d", _make_valid_frame(label_id="fr_10d", n=3))
        df = store.list_labels()
        assert len(df) == 2
        assert set(df["label_id"]) == {"fr_5d", "fr_10d"}

    def test_list_empty(self, tmp_path: Path) -> None:
        store = LabelStore(str(tmp_path))
        df = store.list_labels()
        assert len(df) == 0
