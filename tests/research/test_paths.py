"""Tests for qsys.research.paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from qsys.research.paths import (
    get_research_root,
    make_run_id,
    resolve_artifact_path,
    resolve_run_dir,
    set_research_root,
)


class TestResolveRunDir:
    def test_creates_directory(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        run_dir = resolve_run_dir("test_run", project_root=project_root)
        assert run_dir.exists()
        assert run_dir.name == "test_run"
        assert run_dir.parent.name == "artifacts"

    def test_raises_on_duplicate(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        resolve_run_dir("dup_run", project_root=project_root)
        with pytest.raises(FileExistsError):
            resolve_run_dir("dup_run", project_root=project_root)

    def test_exists_ok(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        d1 = resolve_run_dir("ok_run", project_root=project_root)
        d2 = resolve_run_dir("ok_run", project_root=project_root, exists_ok=True)
        assert d1 == d2

    def test_rejects_reserved_name(self) -> None:
        with pytest.raises(ValueError, match="reserved"):
            resolve_run_dir("_hidden")

    def test_rejects_bad_name(self) -> None:
        with pytest.raises(ValueError):
            resolve_run_dir("run with spaces")

    def test_rejects_empty_name(self) -> None:
        with pytest.raises(ValueError):
            resolve_run_dir("")


class TestGetResearchRoot:
    def test_default_resolves_from_project(self, tmp_path: Path) -> None:
        root = get_research_root(tmp_path)
        assert root == tmp_path / "research" / "artifacts"

    def test_override_via_setter(self, tmp_path: Path) -> None:
        override = tmp_path / "custom" / "research"
        set_research_root(override)
        try:
            result = get_research_root()
            assert result == override
        finally:
            # Reset for other tests
            from qsys.research import paths as p
            p._RESEARCH_ROOT = None


class TestResolveArtifactPath:
    def test_manifest_returns_run_dir(self, tmp_path: Path) -> None:
        assert resolve_artifact_path(tmp_path, "manifest") == tmp_path

    def test_signals_returns_subdir(self, tmp_path: Path) -> None:
        assert resolve_artifact_path(tmp_path, "signals") == tmp_path / "signals"

    def test_labels_returns_subdir(self, tmp_path: Path) -> None:
        assert resolve_artifact_path(tmp_path, "labels") == tmp_path / "labels"


class TestMakeRunId:
    def test_returns_string_with_date_prefix(self) -> None:
        rid = make_run_id()
        assert len(rid) > 20
        assert "_" in rid

    def test_unique(self) -> None:
        ids = {make_run_id() for _ in range(100)}
        assert len(ids) == 100
