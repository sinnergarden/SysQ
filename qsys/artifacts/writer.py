"""
ADR-007 Artifact Writer.

Writes artifact dataclasses to JSON sidecar files alongside existing artifacts.
Sidecar naming convention: {existing_name}.adr7.json

Multi-row artifacts (SignalArtifact, OrderIntentArtifact, ExecutionArtifact)
use write_artifacts() which writes a JSON array.
Single-object artifacts (PortfolioSnapshot, RunManifest, CandidateReport)
use write_artifact() which writes a single JSON object.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from qsys.artifacts.contracts import artifact_to_dict
from qsys.artifacts.validator import validate


def write_artifact(artifact: Any, output_path: str | Path) -> str:
    """Write a single artifact as a JSON object."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _check_validation(artifact)
    data = artifact_to_dict(artifact)
    output_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return str(output_path)


def write_artifacts(artifacts: list[Any], output_path: str | Path) -> str:
    """Write a list of artifacts as a JSON array sidecar.

    Use for multi-row artifacts (SignalArtifact, OrderIntentArtifact,
    ExecutionArtifact) to avoid overwriting on repeated calls.

    Args:
        artifacts: List of artifact dataclass instances.
        output_path: Full path for the JSON sidecar file.

    Returns:
        The path written to as a string.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data_list: list[dict[str, Any]] = []
    for art in artifacts:
        _check_validation(art)
        data_list.append(artifact_to_dict(art))
    output_path.write_text(json.dumps(data_list, indent=2, ensure_ascii=False))
    return str(output_path)


def sidecar_path(existing_artifact_path: str | Path) -> Path:
    """Derive the sidecar JSON path from an existing artifact path.

    Example:
        predictions_2026-05-18.csv → predictions_2026-05-18.adr7.json
    """
    p = Path(existing_artifact_path)
    return p.with_name(p.stem + ".adr7.json")


def _check_validation(artifact: Any) -> None:
    """Validate artifact and warn on errors (non-blocking)."""
    errors = validate(artifact)
    if errors:
        type_name = type(artifact).__name__
        print(f"  ⚠ {type_name} validation warnings:")
        for err in errors:
            print(f"    {err}")
