"""
ADR-007 Artifact Writer.

Writes artifact dataclasses to JSON sidecar files alongside existing artifacts.
Sidecar naming convention: {existing_name}.adr7.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from qsys.artifacts.contracts import artifact_to_dict


def write_artifact(artifact: Any, output_path: str | Path) -> str:
    """Write a single artifact to a JSON sidecar file.

    Args:
        artifact: An artifact dataclass instance.
        output_path: Full path for the JSON file.

    Returns:
        The path written to as a string.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data = artifact_to_dict(artifact)
    output_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return str(output_path)


def sidecar_path(existing_artifact_path: str | Path) -> Path:
    """Derive the sidecar JSON path from an existing artifact path.

    Example:
        predictions_2026-05-18.csv → predictions_2026-05-18.adr7.json
    """
    p = Path(existing_artifact_path)
    return p.with_name(p.stem + ".adr7.json")
