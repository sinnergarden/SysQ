"""Model resolver — single source of truth for approved model lookups.

All production paths MUST use ``resolve_model_for_strategy()`` instead of
hardcoding model paths, reading symlinks, or sorting by mtime.

Pointer file convention::

    artifacts/registry/models/{strategy_id}/{mode}.json

Resolving on-disk artifact,
not through latest symlinks or directory mtime sorting.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
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

    Resolution order (first match wins):
    1. ``artifacts/registry/models/{strategy_id}/{mode}.json`` (strategy-aware)
    2. Legacy ``models/latest_shadow_model.json`` (alpha_v1/shadow only, backward compat)

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
    # 1. Strategy-aware pointer
    pointer = pointer_path_for_strategy(project_root, strategy_id, mode)
    payload = _try_load_pointer(pointer, strategy_id, mode)
    if payload is not None:
        return _build_resolved(project_root, payload, pointer)

    # 2. Backward compat: legacy singleton pointer (alpha_v1/shadow only)
    if strategy_id == "alpha_v1" and mode == "shadow":
        legacy = _try_load_legacy_pointer(project_root)
        if legacy is not None:
            return _build_resolved(project_root, legacy, pointer)

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
    payload: dict[str, Any] = {
        "schema_version": 1,
        "strategy_id": strategy_id,
        "mode": mode,
        "model_id": model_id,
        "model_path": model_path,
        "created_at": created_at,
        "status": status,
        "source_run_id": source_run_id or "",
        "artifact_hash": artifact_hash or "",
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
    if schema_ver != 1:
        raise ValueError(
            f"Pointer schema_version mismatch: expected 1, got {schema_ver}"
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

    if not payload.get("model_path"):
        raise ValueError("Pointer model_path is empty or missing")


def _validate_model_path(project_root: Path, model_path_str: str) -> Path:
    """Validate and resolve model path.  Raises on absence or escape."""
    model_path = Path(model_path_str)
    if not model_path.is_absolute():
        model_path = (project_root / model_path).resolve()
    else:
        model_path = model_path.resolve()

    # Prevent directory traversal escape
    proj_root_resolved = project_root.resolve()
    try:
        model_path.relative_to(proj_root_resolved)
    except ValueError:
        raise ValueError(
            f"Model path '{model_path}' escapes project root "
            f"'{proj_root_resolved}'"
        )

    if not model_path.exists():
        raise FileNotFoundError(f"Model path does not exist: {model_path}")

    return model_path


def _build_resolved(
    project_root: Path,
    payload: dict[str, Any],
    pointer_path: Path,
) -> ResolvedModel:
    model_path = _validate_model_path(project_root, payload["model_path"])
    return ResolvedModel(
        strategy_id=payload["strategy_id"],
        mode=payload["mode"],
        model_id=payload.get("model_id", ""),
        model_path=model_path,
        pointer_path=pointer_path,
        created_at=payload.get("created_at"),
        artifact_hash=payload.get("artifact_hash"),
        source_run_id=payload.get("source_run_id"),
        approved_by=payload.get("approved_by"),
    )


def _try_load_pointer(
    pointer: Path,
    expected_strategy_id: str,
    expected_mode: str,
) -> dict[str, Any] | None:
    """Try to load and validate a pointer file.  Returns None on failure."""
    payload = load_json(pointer)
    if not payload:
        return None
    try:
        _validate_pointer_payload(payload, expected_strategy_id, expected_mode)
    except ValueError:
        return None
    return payload


def _try_load_legacy_pointer(
    project_root: Path,
) -> dict[str, Any] | None:
    """Try to load the legacy singleton pointer ``models/latest_shadow_model.json``.

    Only used for backward compat with alpha_v1/shadow.
    """
    # Local import to avoid circular dependency (model_resolver →
    # model_registry → state, no reverse edge).
    from qsys.ops.model_registry import (  # noqa: PLC0415
        latest_shadow_model_is_usable,
        read_latest_shadow_model,
    )

    payload = read_latest_shadow_model(project_root)
    if not payload:
        return None

    model_path = payload.get("model_path", "")
    if not model_path:
        return None

    is_usable = latest_shadow_model_is_usable(project_root, payload)
    return {
        "schema_version": 1,
        "strategy_id": "alpha_v1",
        "mode": "shadow",
        "model_id": payload.get("model_name", ""),
        "model_path": model_path,
        "created_at": payload.get("trained_at", ""),
        "status": "approved" if is_usable else "unknown",
        "source_run_id": payload.get("train_run_id", ""),
        "artifact_hash": "",
        "approved_by": "system",
    }


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
