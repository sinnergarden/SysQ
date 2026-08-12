"""Model resolver — single source of truth for approved model lookups.

All production paths MUST use ``resolve_model_for_strategy()`` instead of
hardcoding model paths, reading symlinks, or sorting by mtime.

Pointer file convention::

    artifacts/registry/models/{strategy_id}/{mode}.json

Resolving on-disk artifact,
not through latest symlinks or directory mtime sorting.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from qsys.ops.state import ensure_directory
from qsys.utils.json_io import atomic_write_json, load_json

_RESOLVER_REGISTRY_REL = "artifacts/registry/models"


# ── Data model ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ResolvedModel:
    """Canonical result of a successful model resolution."""

    strategy_id: str
    mode: str
    model_id: str
    model_path: Path
    pointer_path: Path
    created_at: str | None = None
    artifact_hash: str | None = None
    source_run_id: str | None = None
    approved_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "mode": self.mode,
            "model_id": self.model_id,
            "model_path": str(self.model_path),
            "pointer_path": str(self.pointer_path),
            "created_at": self.created_at or "",
            "artifact_hash": self.artifact_hash or "",
            "source_run_id": self.source_run_id or "",
            "approved_by": self.approved_by or "",
        }


# ── Public API ──────────────────────────────────────────────────────────────


def resolve_model_for_strategy(
    project_root: Path,
    strategy_id: str,
    mode: Literal["shadow", "prod"] = "shadow",
) -> ResolvedModel:
    """Resolve approved model for *strategy_id* via explicit pointer only.

    Resolution uses only
    ``artifacts/registry/models/{strategy_id}/{mode}.json``.  Legacy latest
    pointers and model-directory symlinks are intentionally not consulted.

    Returns
    -------
    ResolvedModel
        Resolved model metadata.

    Raises
    ------
    FileNotFoundError
        If no valid pointer exists.  Never falls back to symlinks or mtime.
    ValueError
        If pointer contents are structurally invalid.
    """
    pointer = pointer_path_for_strategy(project_root, strategy_id, mode)
    if pointer.is_symlink():
        raise ValueError(f"Model pointer must not be a symlink: {pointer}")
    if pointer.exists():
        payload = load_json(pointer)
        if not payload:
            raise ValueError(
                f"Pointer file exists but is empty or invalid JSON: {pointer}"
            )
        _validate_pointer_payload(payload, strategy_id, mode)
        return _build_resolved(project_root, payload, pointer)

    raise FileNotFoundError(
        f"No approved {mode} model pointer found for strategy '{strategy_id}'. "
        f"Expected at: {pointer}. "
        f"Run training or approval workflow first."
    )


def pointer_path_for_strategy(
    project_root: Path,
    strategy_id: str,
    mode: Literal["shadow", "prod"],
) -> Path:
    """Return the canonical pointer path for a strategy/mode pair."""
    return project_root / _RESOLVER_REGISTRY_REL / strategy_id / f"{mode}.json"


def write_model_pointer(
    project_root: Path,
    *,
    strategy_id: str,
    mode: Literal["shadow", "prod"],
    model_id: str,
    model_path: str,
    created_at: str | None = None,
    status: str = "approved",
    source_run_id: str | None = None,
    artifact_hash: str | None = None,
    approved_by: str | None = None,
) -> Path:
    """Write a strategy-level model pointer.

    Parameters
    ----------
    project_root
        Project root for path resolution.
    strategy_id
        Strategy identifier.
    mode
        ``"shadow"`` or ``"prod"``.
    model_id
        Human-readable model identifier.
    model_path
        Path to model directory, **relative** to *project_root*.
    created_at
        ISO-8601 timestamp.  Defaults to current UTC time.
    status
        Pointer status (``"approved"``, ``"shadow_ready"``, etc.).
    source_run_id
        Run ID that produced this model.
    artifact_hash
        Optional artifact hash for integrity verification.
    approved_by
        Who approved this model (``"manual"``, ``"system"``, etc.).

    Returns
    -------
    Path
        The written pointer file path.
    """
    created_at = created_at or _utc_now()
    if Path(model_path).is_absolute():
        raise ValueError("Model pointer model_path must be relative to project_root")
    resolved_model_path = _validate_model_path(project_root, model_path)
    actual_artifact_hash = compute_model_artifact_hash(resolved_model_path)
    if artifact_hash and artifact_hash != actual_artifact_hash:
        raise ValueError(
            "Refusing to write model pointer with a mismatched artifact hash: "
            f"declared={artifact_hash}, actual={actual_artifact_hash}"
        )
    payload: dict[str, Any] = {
        "schema_version": 2,
        "strategy_id": strategy_id,
        "mode": mode,
        "model_id": model_id,
        "model_path": model_path,
        "created_at": created_at,
        "status": status,
        "source_run_id": source_run_id or "",
        "artifact_hash": actual_artifact_hash,
        "approved_by": approved_by or "manual",
    }
    pointer = pointer_path_for_strategy(project_root, strategy_id, mode)
    ensure_directory(pointer.parent)
    return atomic_write_json(pointer, payload)


def read_model_pointer(
    project_root: Path,
    strategy_id: str,
    mode: Literal["shadow", "prod"],
) -> dict[str, Any]:
    """Read and validate a strategy model pointer.

    Returns empty dict if pointer is missing or invalid.
    """
    pointer = pointer_path_for_strategy(project_root, strategy_id, mode)
    payload = load_json(pointer)
    if not payload:
        return {}
    try:
        _validate_pointer_payload(payload, strategy_id, mode)
    except ValueError:
        return {}
    return payload


# ── Internal helpers ────────────────────────────────────────────────────────


def _validate_pointer_payload(
    payload: dict[str, Any],
    expected_strategy_id: str,
    expected_mode: str,
) -> None:
    """Validate pointer contents.  Raises ValueError on structural issues."""
    if not isinstance(payload, dict):
        raise ValueError("Pointer payload must be a dict")

    schema_ver = payload.get("schema_version")
    if schema_ver != 2:
        raise ValueError(
            f"Pointer schema_version mismatch: expected 2, got {schema_ver}"
        )

    actual_sid = payload.get("strategy_id")
    if actual_sid != expected_strategy_id:
        raise ValueError(
            f"Pointer strategy_id mismatch: expected '{expected_strategy_id}', "
            f"got '{actual_sid}'"
        )

    actual_mode = payload.get("mode")
    if actual_mode != expected_mode:
        raise ValueError(
            f"Pointer mode mismatch: expected '{expected_mode}', "
            f"got '{actual_mode}'"
        )

    if payload.get("status") != "approved":
        raise ValueError(
            "Pointer status must be 'approved' for runtime resolution, "
            f"got {payload.get('status')!r}"
        )

    if not payload.get("model_path"):
        raise ValueError("Pointer model_path is empty or missing")
    if Path(str(payload["model_path"])).is_absolute():
        raise ValueError("Pointer model_path must be relative to project_root")
    artifact_hash = payload.get("artifact_hash")
    if (
        not isinstance(artifact_hash, str)
        or len(artifact_hash) != 64
        or any(ch not in "0123456789abcdef" for ch in artifact_hash)
    ):
        raise ValueError("Pointer artifact_hash must be a SHA-256 hex digest")


def _validate_model_path(project_root: Path, model_path_str: str) -> Path:
    """Validate and resolve model path.  Raises on absence or escape."""
    model_path = Path(model_path_str)
    unresolved = model_path if model_path.is_absolute() else project_root / model_path
    project_resolved = project_root.resolve()
    if not model_path.is_absolute():
        current = project_resolved
        for part in model_path.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(
                    f"Model path must not contain symlink components: {current}"
                )
    elif unresolved.is_symlink():
        raise ValueError(f"Model path must not be a symlink: {unresolved}")
    if not model_path.is_absolute():
        model_path = unresolved.resolve()
    else:
        model_path = model_path.resolve()

    # Prevent directory traversal escape
    try:
        model_path.relative_to(project_resolved)
    except ValueError:
        raise ValueError(
            f"Model path '{model_path}' escapes project root "
            f"'{project_resolved}'"
        )

    if not model_path.exists():
        raise FileNotFoundError(f"Model path does not exist: {model_path}")

    return model_path


def compute_model_artifact_hash(model_path: Path) -> str:
    """Hash an immutable model directory by relative path and file content."""
    root = model_path.resolve()
    if not root.is_dir():
        raise ValueError(f"Model artifact path is not a directory: {root}")
    files = sorted(path for path in root.rglob("*") if path.is_file())
    if not files:
        raise ValueError(f"Model artifact directory contains no files: {root}")

    digest = hashlib.sha256()
    for path in files:
        if path.is_symlink():
            raise ValueError(f"Model artifact must not contain symlinks: {path}")
        relative = path.relative_to(root).as_posix().encode("utf-8")
        file_digest = hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(file_digest)
        digest.update(b"\n")
    return digest.hexdigest()


def _build_resolved(
    project_root: Path,
    payload: dict[str, Any],
    pointer_path: Path,
) -> ResolvedModel:
    model_path = _validate_model_path(project_root, payload["model_path"])
    actual_hash = compute_model_artifact_hash(model_path)
    declared_hash = str(payload.get("artifact_hash") or "")
    if actual_hash != declared_hash:
        raise ValueError(
            f"Model artifact hash mismatch at {model_path}: "
            f"declared={declared_hash}, actual={actual_hash}"
        )
    return ResolvedModel(
        strategy_id=payload["strategy_id"],
        mode=payload["mode"],
        model_id=payload.get("model_id", ""),
        model_path=model_path,
        pointer_path=pointer_path,
        created_at=payload.get("created_at"),
        artifact_hash=actual_hash,
        source_run_id=payload.get("source_run_id"),
        approved_by=payload.get("approved_by"),
    )


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
