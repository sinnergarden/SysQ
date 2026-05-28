"""Tests for qsys.research.manifest — read/write helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from qsys.research.manifest import read_manifest, write_manifest, with_standard_metadata


class TestWriteReadManifest:
    def test_write_and_read_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.json"
        data = {"label_id": "fr_5d", "kind": "forward_return"}
        write_manifest(path, data)
        assert path.exists()
        loaded = read_manifest(path)
        assert loaded["label_id"] == "fr_5d"
        assert loaded["kind"] == "forward_return"

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "sub" / "manifest.json"
        write_manifest(path, {"a": 1})
        assert path.exists()

    def test_read_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            read_manifest(tmp_path / "nonexistent.json")

    def test_stable_json_formatting(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.json"
        write_manifest(path, {"z": 1, "a": 2})
        text = path.read_text()
        # Keys should be sorted, indented
        assert '"a"' in text
        assert '"z"' in text
        assert text.strip().endswith("}")


class TestWithStandardMetadata:
    def test_sets_created_at(self) -> None:
        data = with_standard_metadata({"key": "val"})
        assert "created_at" in data
        assert "T" in data["created_at"]  # ISO-8601 format
        assert data["key"] == "val"

    def test_does_not_overwrite_when_update_false(self) -> None:
        data = with_standard_metadata({"created_at": "original"})
        assert data["created_at"] == "original"

    def test_updates_when_update_true(self) -> None:
        data = with_standard_metadata({"a": 1}, update=True)
        assert "created_at" in data
        assert "updated_at" in data
