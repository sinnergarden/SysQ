"""Shadow promotion pointer resolution for UC-8 daily ops.

Resolves ``data/research/promotions/shadow.yaml`` into a standard dict
that populates ``DailyRunContext`` lineage fields and provides hard-block
validation for missing or incomplete promotion pointers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_SHADOW_REQUIRED_FIELDS = (
    "candidate_id",
    "candidate_path",
    "signal_ref.signal_id",
    "signal_ref.signal_run_id",
    "strategy_config_id",
    "strategy_template_id",
    "backtest_id",
    "strategy_run_id",
    "promoted_at",
    "promoted_by",
)


def _get_nested(data: dict[str, Any], dotted: str) -> Any:
    """Resolve a dotted path like ``signal_ref.signal_id``."""
    parts = dotted.split(".")
    current: Any = data
    for p in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(p)
    return current


def validate_shadow_promotion_payload(payload: dict[str, Any]) -> list[str]:
    """Validate required fields in a shadow promotion pointer.

    Returns a list of missing-field descriptions.  An empty list means
    the payload is valid.
    """
    missing: list[str] = []
    for dotted in _SHADOW_REQUIRED_FIELDS:
        val = _get_nested(payload, dotted)
        if val is None or (isinstance(val, str) and not val.strip()):
            missing.append(dotted)
    return missing


def resolve_shadow_promotion(
    pointer_path: str | Path,
    *,
    candidate_path_required: bool = True,
) -> dict[str, Any]:
    """Read and validate a shadow promotion pointer, returning lineage fields.

    Parameters
    ----------
    pointer_path:
        Path to the shadow promotion pointer YAML (typically
        ``data/research/promotions/shadow.yaml``).
    candidate_path_required:
        When ``True`` (default), also verify that the ``candidate_path``
        file exists on disk.

    Returns
    -------
    dict
        Flat dict with keys:
        ``candidate_id``, ``candidate_path``, ``signal_id``,
        ``signal_run_id``, ``strategy_config_id``, ``strategy_template_id``,
        ``backtest_id``, ``strategy_run_id``, ``promotion_pointer_path``,
        ``promoted_at``, ``promoted_by``.

    Raises
    ------
    FileNotFoundError
        If *pointer_path* does not exist.
    ValueError
        If the pointer payload fails validation, the artifact type is
        wrong, or the promotion target is not ``shadow``.
    """
    path = Path(pointer_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Shadow promotion pointer not found at {path}. "
            f"Create one with:\n"
            f"  python scripts/promote_candidate.py create --promote-to shadow ...\n"
            f"or verify the path argument."
        )

    payload: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    # Validate artifact type
    at = payload.get("artifact_type")
    if at != "shadow_promotion_pointer":
        raise ValueError(
            f"Expected artifact_type='shadow_promotion_pointer' "
            f"in {path}, got {at!r}"
        )

    # Validate promotion target
    pt = payload.get("promotion_target")
    if pt != "shadow":
        raise ValueError(
            f"Expected promotion_target='shadow' in {path}, "
            f"got {pt!r}. Production promotion is not implemented."
        )

    # Validate required fields
    missing = validate_shadow_promotion_payload(payload)
    if missing:
        raise ValueError(
            f"Shadow promotion pointer {path} is missing required fields: "
            f"{', '.join(missing)}"
        )

    # Verify candidate path exists on disk
    candidate_path_str: str | None = _get_nested(payload, "candidate_path")
    if candidate_path_required and candidate_path_str:
        candidate_path = Path(candidate_path_str)
        tried: list[str] = []
        if candidate_path.is_absolute():
            tried.append(str(candidate_path))
        else:
            # Try (a) relative to CWD
            tried.append(str(candidate_path))
            if not candidate_path.exists():
                # (b) relative to pointer's research root
                research_root = path.parent.parent
                alt = research_root / candidate_path_str
                tried.append(str(alt))
                if alt.exists():
                    candidate_path = alt
            if not candidate_path.exists():
                # (c) relative to project root (research_root.parent.parent)
                project_root = path.parent.parent.parent.parent
                alt = project_root / candidate_path_str
                tried.append(str(alt))
                if alt.exists():
                    candidate_path = alt
        if not candidate_path.exists():
            raise FileNotFoundError(
                f"Candidate file referenced by shadow pointer does not exist. "
                f"Attempted paths: {tried}. "
                f"The shadow pointer at {path} refers to "
                f"candidate_id={_get_nested(payload, 'candidate_id')!r} but "
                f"the candidate.yaml file is missing."
            )

    return {
        "candidate_id": _get_nested(payload, "candidate_id"),
        "candidate_path": candidate_path_str,
        "signal_id": _get_nested(payload, "signal_ref.signal_id"),
        "signal_run_id": _get_nested(payload, "signal_ref.signal_run_id"),
        "strategy_config_id": _get_nested(payload, "strategy_config_id"),
        "strategy_template_id": _get_nested(payload, "strategy_template_id"),
        "backtest_id": _get_nested(payload, "backtest_id"),
        "strategy_run_id": _get_nested(payload, "strategy_run_id"),
        "promotion_pointer_path": str(path.resolve()),
        "promoted_at": _get_nested(payload, "promoted_at"),
        "promoted_by": _get_nested(payload, "promoted_by"),
    }
