"""Tests for qsys/ops/model_resolver.py

Covers:
1. pointer exists + valid → resolve success
2. pointer missing → fail-fast
3. strategy_id mismatch → fail-fast
4. mode mismatch → fail-fast
5. model_path missing on disk → fail-fast
6. model_path escapes project_root → fail-fast
7. write_model_pointer writes shadow pointer correctly
8. write_model_pointer writes prod pointer
9. legacy models/latest_shadow_model.json is ignored
10. symlinked model paths are rejected
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from qsys.ops.model_resolver import (
    ResolvedModel,
    pointer_path_for_strategy,
    read_model_pointer,
    resolve_model_for_strategy,
    write_model_pointer,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a temporary project root with a mock model directory."""
    model_dir = tmp_path / "experiments" / "alpha_v1_models" / "my_trained_model"
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "model.bin").write_bytes(b"model-artifact")
    return tmp_path


def _write_valid_pointer(
    project_root: Path,
    strategy_id: str = "alpha_v1",
    mode: str = "shadow",
    model_id: str = "alpha_v1_20260704",
    model_path: str | None = None,
) -> Path:
    if model_path is None:
        model_path = f"experiments/alpha_v1_models/my_trained_model"
    return write_model_pointer(
        project_root=project_root,
        strategy_id=strategy_id,
        mode=mode,
        model_id=model_id or strategy_id,
        model_path=model_path,
        created_at="2026-07-04T15:30:00Z",
        status="approved",
        source_run_id="weekly_retrain_20260704",
        approved_by="manual",
    )


def _write_legacy_pointer(
    project_root: Path, model_path: str | None = None
) -> str:
    """Write a legacy ``models/latest_shadow_model.json``.

    Uses an **absolute** ``model_path`` because the legacy
    ``latest_shadow_model_is_usable()`` function does ``Path(model_path).exists()``
    without resolving relative to the project root.

    Returns the absolute model path string written to the pointer.
    """
    if model_path is None:
        model_path = str(project_root / "experiments" / "alpha_v1_models" / "my_trained_model")
    else:
        # Resolve relative paths to absolute for legacy pointer tests
        mp = Path(model_path)
        if not mp.is_absolute():
            model_path = str(project_root / mp)
    legacy_dir = project_root / "models"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "model_name": "alpha_v1_20260704",
        "model_path": model_path,
        "mainline_object_name": "feature_173",
        "bundle_id": "bundle_semantic_demo",
        "train_run_id": "weekly_retrain_20260704",
        "trained_at": "2026-07-04T15:30:00Z",
        "status": "success",
    }
    (legacy_dir / "latest_shadow_model.json").write_text(
        json.dumps(payload, indent=2) + "\n"
    )
    # Create required artifact stubs so latest_shadow_model_is_usable passes
    model_dir = Path(model_path)
    model_dir.mkdir(parents=True, exist_ok=True)
    for stub in ("config_snapshot.json", "training_summary.json",
                 "decisions.json", "meta.yaml", "model.pkl"):
        (model_dir / stub).write_text("")
    return model_path


# ── Tests ───────────────────────────────────────────────────────────────────


class TestResolveModel:
    def test_resolve_success(self, tmp_project: Path) -> None:
        """1. Pointer exists and valid → resolve succeeds."""
        _write_valid_pointer(tmp_project)
        resolved = resolve_model_for_strategy(
            project_root=tmp_project,
            strategy_id="alpha_v1",
            mode="shadow",
        )
        assert isinstance(resolved, ResolvedModel)
        assert resolved.strategy_id == "alpha_v1"
        assert resolved.mode == "shadow"
        assert resolved.model_id == "alpha_v1_20260704"
        assert resolved.model_path.exists()
        assert resolved.model_path.name == "my_trained_model"

    def test_resolve_pointer_missing(self, tmp_project: Path) -> None:
        """2. Pointer missing → FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            resolve_model_for_strategy(
                project_root=tmp_project,
                strategy_id="alpha_v1",
                mode="shadow",
            )

    def test_resolve_strategy_id_mismatch(self, tmp_project: Path) -> None:
        """3. Strategy_id mismatch → FileNotFoundError."""
        _write_valid_pointer(tmp_project, strategy_id="alpha_v2")
        with pytest.raises(FileNotFoundError):
            resolve_model_for_strategy(
                project_root=tmp_project,
                strategy_id="alpha_v1",
                mode="shadow",
            )

    def test_resolve_mode_mismatch(self, tmp_project: Path) -> None:
        """4. Mode mismatch → FileNotFoundError."""
        _write_valid_pointer(tmp_project, mode="shadow")
        with pytest.raises(FileNotFoundError):
            resolve_model_for_strategy(
                project_root=tmp_project,
                strategy_id="alpha_v1",
                mode="prod",
            )

    def test_resolve_model_path_missing(self, tmp_project: Path) -> None:
        """5. Model path on disk missing → FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            _write_valid_pointer(
                tmp_project,
                model_path="experiments/alpha_v1_models/nonexistent",
            )

    def test_resolve_model_path_escape(self, tmp_project: Path) -> None:
        """6. Model path escapes project_root → ValueError."""
        # Create a model outside the project root
        outside = tmp_project.parent / "outside_model"
        outside.mkdir(parents=True, exist_ok=True)
        (outside / "model.bin").write_bytes(b"outside")
        with pytest.raises(ValueError, match="must be relative"):
            _write_valid_pointer(
                tmp_project,
                model_path=str(outside),
            )

    def test_resolve_prod_pointer_shadow_no_fallback(self, tmp_project: Path) -> None:
        """Resolve prod pointer when only shadow exists → fail-fast."""
        _write_valid_pointer(tmp_project, mode="shadow")
        with pytest.raises(FileNotFoundError):
            resolve_model_for_strategy(
                project_root=tmp_project,
                strategy_id="alpha_v1",
                mode="prod",
            )

    def test_unapproved_pointer_is_rejected(self, tmp_project: Path) -> None:
        write_model_pointer(
            project_root=tmp_project,
            strategy_id="alpha_v1",
            mode="shadow",
            model_id="pending_model",
            model_path="experiments/alpha_v1_models/my_trained_model",
            status="pending",
        )
        with pytest.raises(ValueError, match="status must be 'approved'"):
            resolve_model_for_strategy(
                project_root=tmp_project,
                strategy_id="alpha_v1",
                mode="shadow",
            )


class TestWriteAndReadPointer:
    def test_write_shadow_pointer(self, tmp_project: Path) -> None:
        """7. write_model_pointer writes shadow pointer correctly."""
        path = write_model_pointer(
            project_root=tmp_project,
            strategy_id="alpha_v1",
            mode="shadow",
            model_id="alpha_v1_20260704",
            model_path="experiments/alpha_v1_models/my_trained_model",
            created_at="2026-07-04T15:30:00Z",
            status="approved",
            source_run_id="weekly_retrain_20260704",
            approved_by="test",
        )
        assert path.exists()
        assert "shadow.json" in str(path)
        assert "alpha_v1" in str(path)

        # Verify content
        payload = json.loads(path.read_text())
        assert payload["schema_version"] == 2
        assert payload["strategy_id"] == "alpha_v1"
        assert payload["mode"] == "shadow"
        assert payload["model_id"] == "alpha_v1_20260704"
        assert payload["status"] == "approved"

    def test_write_prod_pointer(self, tmp_project: Path) -> None:
        """8. write_model_pointer writes prod pointer correctly."""
        path = write_model_pointer(
            project_root=tmp_project,
            strategy_id="alpha_v1",
            mode="prod",
            model_id="alpha_v1_20260704_prod",
            model_path="experiments/alpha_v1_models/my_trained_model",
            approved_by="operator",
        )
        assert "prod.json" in str(path)
        payload = json.loads(path.read_text())
        assert payload["mode"] == "prod"
        assert payload["approved_by"] == "operator"

    def test_read_model_pointer(self, tmp_project: Path) -> None:
        """read_model_pointer returns correct payload."""
        _write_valid_pointer(tmp_project)
        payload = read_model_pointer(tmp_project, "alpha_v1", "shadow")
        assert payload["strategy_id"] == "alpha_v1"
        assert payload["model_id"] == "alpha_v1_20260704"

    def test_read_model_pointer_missing(self, tmp_project: Path) -> None:
        """read_model_pointer returns empty dict for missing pointer."""
        payload = read_model_pointer(tmp_project, "alpha_v1", "prod")
        assert payload == {}

    def test_pointer_path_convention(self, tmp_project: Path) -> None:
        """pointer_path_for_strategy returns the right path structure."""
        p = pointer_path_for_strategy(tmp_project, "alpha_v1", "shadow")
        assert str(p).endswith("artifacts/registry/models/alpha_v1/shadow.json")

        p2 = pointer_path_for_strategy(tmp_project, "alpha_v2", "prod")
        assert str(p2).endswith("artifacts/registry/models/alpha_v2/prod.json")


class TestPointerMalformed:
    def test_malformed_pointer_file_raises_value_error(self, tmp_project: Path) -> None:
        """Canonical pointer file exists but malformed → ValueError, not missing."""
        pointer = pointer_path_for_strategy(tmp_project, "alpha_v1", "shadow")
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text('{"schema_version": 999, "strategy_id": "alpha_v1", "mode": "shadow"}')
        with pytest.raises(ValueError, match="schema_version"):
            resolve_model_for_strategy(
                project_root=tmp_project,
                strategy_id="alpha_v1",
                mode="shadow",
            )

    def test_empty_pointer_file_raises_value_error(self, tmp_project: Path) -> None:
        """Canonical pointer file exists but is empty → ValueError."""
        pointer = pointer_path_for_strategy(tmp_project, "alpha_v1", "shadow")
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text("{}")
        with pytest.raises(ValueError, match="empty or invalid"):
            resolve_model_for_strategy(
                project_root=tmp_project,
                strategy_id="alpha_v1",
                mode="shadow",
            )


class TestBackwardCompat:
    def test_legacy_pointer_resolve(self, tmp_project: Path) -> None:
        """9. Legacy latest pointer is never consulted."""
        _write_legacy_pointer(tmp_project)
        with pytest.raises(FileNotFoundError):
            resolve_model_for_strategy(
                project_root=tmp_project,
                strategy_id="alpha_v1",
                mode="shadow",
            )

    def test_legacy_pointer_missing_for_non_alpha_v1(
        self, tmp_project: Path
    ) -> None:
        """10. Legacy pointer only works for alpha_v1, not other strategies."""
        # Write legacy pointer
        _write_legacy_pointer(tmp_project)
        # alpha_v2 should not find it
        with pytest.raises(FileNotFoundError):
            resolve_model_for_strategy(
                project_root=tmp_project,
                strategy_id="alpha_v2",
                mode="shadow",
            )

    def test_new_pointer_takes_precedence_over_legacy(
        self, tmp_project: Path
    ) -> None:
        """Strategy-level pointer overrides legacy singleton pointer."""
        _write_legacy_pointer(tmp_project, model_path="experiments/alpha_v1_models/legacy_model")
        dir_legacy = tmp_project / "experiments" / "alpha_v1_models" / "legacy_model"
        dir_legacy.mkdir(parents=True, exist_ok=True)

        # Also write new pointer
        _write_valid_pointer(
            tmp_project,
            model_path="experiments/alpha_v1_models/my_trained_model",
        )

        resolved = resolve_model_for_strategy(
            project_root=tmp_project,
            strategy_id="alpha_v1",
            mode="shadow",
        )
        # Should use new pointer, not legacy
        assert resolved.model_path.name == "my_trained_model"

    def test_legacy_pointer_unusable_model_fails(self, tmp_project: Path) -> None:
        """Legacy pointer exists but model directory missing → fail-fast."""
        # Write legacy pointer JSON manually — pointing to a non-existent dir
        legacy_dir = tmp_project / "models"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        bad_path = str(tmp_project / "experiments" / "alpha_v1_models" / "nonexistent")
        (legacy_dir / "latest_shadow_model.json").write_text(
            json.dumps({
                "model_name": "alpha_v1_bad",
                "model_path": bad_path,
                "mainline_object_name": "feature_173",
                "bundle_id": "bundle_semantic_demo",
                "train_run_id": "weekly_retrain_bad",
                "trained_at": "2026-07-04T15:30:00Z",
                "status": "success",
            }, indent=2) + "\n"
        )
        with pytest.raises(FileNotFoundError):
            resolve_model_for_strategy(
                project_root=tmp_project,
                strategy_id="alpha_v1",
                mode="shadow",
            )

    def test_symlink_model_directory_is_rejected(self, tmp_project: Path) -> None:
        target = tmp_project / "experiments" / "alpha_v1_models" / "real"
        target.mkdir(parents=True)
        (target / "model.bin").write_bytes(b"real")
        link = tmp_project / "experiments" / "alpha_v1_models" / "latest"
        link.symlink_to(target, target_is_directory=True)
        with pytest.raises(ValueError, match="symlink components"):
            _write_valid_pointer(
                tmp_project,
                model_path="experiments/alpha_v1_models/latest",
            )

    def test_symlink_parent_component_is_rejected(self, tmp_project: Path) -> None:
        real = tmp_project / "real_models/model_v1"
        real.mkdir(parents=True)
        (real / "model.bin").write_bytes(b"real")
        parent = tmp_project / "experiments/linked_models"
        parent.symlink_to(tmp_project / "real_models", target_is_directory=True)
        with pytest.raises(ValueError, match="symlink components"):
            _write_valid_pointer(
                tmp_project,
                model_path="experiments/linked_models/model_v1",
            )


class TestResolvedModel:
    def test_resolved_model_to_dict(self) -> None:
        """ResolvedModel.to_dict() returns correct serializable dict."""
        rm = ResolvedModel(
            strategy_id="alpha_v1",
            mode="shadow",
            model_id="model_v1",
            model_path=Path("/tmp/model"),
            pointer_path=Path("/tmp/pointer.json"),
            created_at="2026-07-04T15:30:00Z",
            source_run_id="run_123",
        )
        d = rm.to_dict()
        assert d["strategy_id"] == "alpha_v1"
        assert d["model_path"] == "/tmp/model"
        assert d["source_run_id"] == "run_123"
