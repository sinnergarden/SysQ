"""Candidate promotion artifacts — create, load, promote to shadow (UC-10).

This module is the core of UC-10 Candidate Promotion.  It provides functions
to build, validate, persist, and promote candidates — no business logic for
daily ops, backtesting, or production trading is included.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# ── Schema helpers ───────────────────────────────────────────────────────

_REQUIRED_DOTTED_FIELDS = (
    "candidate_id",
    "signal_ref.signal_id",
    "signal_ref.signal_run_id",
    "strategy.strategy_config_id",
    "strategy.strategy_template_id",
    "backtest_ref.strategy_run_id",
    "backtest_ref.backtest_id",
    "backtest_ref.path",
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


def _candidate_path(research_root: Path, candidate_id: str) -> Path:
    return research_root / "candidates" / candidate_id / "candidate.yaml"


def _shadow_pointer_path(research_root: Path) -> Path:
    return research_root / "promotions" / "shadow.yaml"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Core functions ──────────────────────────────────────────────────────


def build_candidate_payload(
    candidate_id: str,
    signal_ref: dict[str, str],
    strategy: dict[str, Any],
    backtest_ref: dict[str, str],
    evidence: dict[str, Any] | None = None,
    source: dict[str, Any] | None = None,
    created_by: str = "manual",
) -> dict[str, Any]:
    """Construct a candidate artifact dict.

    Parameters
    ----------
    candidate_id:
        Unique candidate identifier.
    signal_ref:
        Dict with ``signal_id`` and ``signal_run_id`` keys.
    strategy:
        Dict with ``strategy_config_id``, ``strategy_template_id``,
        and optional ``top_n``, ``rebalance_freq``.
    backtest_ref:
        Dict with ``strategy_run_id``, ``backtest_id``, ``path``.
    evidence:
        Optional evidence dict (see module docstring for schema).
        Missing evidence keys default to ``None``.
    source:
        Optional dict with ``experiment_id``, ``label_id``, ``notes``.
    created_by:
        Identifier of the creator (default ``"manual"``).

    Returns
    -------
    dict
        Candidate artifact dict ready for serialisation.
    """
    now = _now_iso()

    candidate: dict[str, Any] = {
        "artifact_type": "candidate",
        "candidate_id": candidate_id,
        "status": "candidate",
        "created_at": now,
        "created_by": created_by,
        "promotion_target": None,
        "promoted_at": None,
        "promoted_by": None,
        "signal_ref": {
            "signal_id": signal_ref.get("signal_id", ""),
            "signal_run_id": signal_ref.get("signal_run_id", ""),
        },
        "strategy": {
            "strategy_config_id": strategy.get("strategy_config_id", ""),
            "strategy_template_id": strategy.get("strategy_template_id", ""),
            "top_n": strategy.get("top_n"),
            "rebalance_freq": strategy.get("rebalance_freq"),
        },
        "backtest_ref": {
            "strategy_run_id": backtest_ref.get("strategy_run_id", ""),
            "backtest_id": backtest_ref.get("backtest_id", ""),
            "path": backtest_ref.get("path", ""),
        },
        "evidence": {
            "metrics_path": None,
            "manifest_path": None,
            "ic_mean": None,
            "icir": None,
            "rank_ic_mean": None,
            "rank_icir": None,
            "total_return": None,
            "annualized_return": None,
            "sharpe": None,
            "max_drawdown": None,
        },
        "source": {
            "experiment_id": None,
            "label_id": None,
            "notes": None,
        },
    }

    if evidence:
        for k in list(candidate["evidence"]):
            if k in evidence:
                candidate["evidence"][k] = evidence[k]
    if source:
        for k in list(candidate["source"]):
            if k in source:
                candidate["source"][k] = source[k]

    return candidate


def write_candidate(
    candidate: dict[str, Any],
    *,
    research_root: str | Path = "data/research",
    overwrite: bool = False,
) -> Path:
    """Persist a candidate artifact to YAML.

    Parameters
    ----------
    candidate:
        Candidate dict (e.g. from :func:`build_candidate_payload`).
    research_root:
        Research root directory (default ``data/research``).
    overwrite:
        When ``False`` (default), raise ``FileExistsError`` if the
        candidate YAML already exists.

    Returns
    -------
    Path
        Path to the written ``candidate.yaml``.
    """
    candidate_id = str(candidate.get("candidate_id", ""))
    if not candidate_id:
        raise ValueError("candidate must have a non-empty candidate_id")

    path = _candidate_path(Path(research_root), candidate_id)
    if path.exists() and not overwrite:
        raise FileExistsError(
            f"Candidate already exists: {path} "
            f"(use overwrite=True or --overwrite to replace)"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(candidate, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def load_candidate(
    candidate_id: str,
    research_root: str | Path = "data/research",
) -> dict[str, Any]:
    """Load a candidate artifact from YAML.

    Parameters
    ----------
    candidate_id:
        Candidate identifier.
    research_root:
        Research root directory (default ``data/research``).

    Returns
    -------
    dict
        Candidate artifact dict.

    Raises
    ------
    FileNotFoundError
        If the candidate YAML does not exist.
    """
    path = _candidate_path(Path(research_root), candidate_id)
    if not path.exists():
        raise FileNotFoundError(
            f"Candidate {candidate_id!r} not found at {path}. "
            f"Create it with ``scripts/promote_candidate.py create`` first."
        )
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data


def validate_candidate(
    candidate: dict[str, Any],
) -> list[str]:
    """Validate required fields in a candidate artifact.

    Returns a list of missing-field descriptions.  An empty list means
    the candidate is valid.
    """
    missing: list[str] = []
    for dotted in _REQUIRED_DOTTED_FIELDS:
        val = _get_nested(candidate, dotted)
        if val is None or (isinstance(val, str) and not val.strip()):
            missing.append(dotted)
    return missing


def load_backtest_evidence(
    backtest_path: str | Path,
    *,
    metrics_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Extract evidence fields from backtest artifacts.

    Reads ``metrics.json`` and/or ``manifest.json`` from the backtest
    directory.  Fields that cannot be read are set to ``None``.

    Parameters
    ----------
    backtest_path:
        Path to the backtest run directory (containing ``metrics.json``
        and ``manifest.json``).
    metrics_path:
        Explicit path to ``metrics.json``.  Defaults to
        ``<backtest_path>/metrics.json``.
    manifest_path:
        Explicit path to ``manifest.json``.  Defaults to
        ``<backtest_path>/manifest.json``.

    Returns
    -------
    dict
    """
    bt_dir = Path(backtest_path)
    evidence: dict[str, Any] = {
        "metrics_path": str(metrics_path) if metrics_path else str(bt_dir / "metrics.json"),
        "manifest_path": str(manifest_path) if manifest_path else str(bt_dir / "manifest.json"),
        "ic_mean": None,
        "icir": None,
        "rank_ic_mean": None,
        "rank_icir": None,
        "total_return": None,
        "annualized_return": None,
        "sharpe": None,
        "max_drawdown": None,
    }

    _read_metrics = Path(evidence["metrics_path"])
    if _read_metrics.exists():
        try:
            raw = json.loads(_read_metrics.read_text(encoding="utf-8"))
            evidence["total_return"] = raw.get("total_return")
        except Exception:
            pass

    _read_manifest = Path(evidence["manifest_path"])
    if _read_manifest.exists():
        try:
            raw = json.loads(_read_manifest.read_text(encoding="utf-8"))
            if evidence["total_return"] is None:
                evidence["total_return"] = raw.get("total_return")
        except Exception:
            pass

    return evidence


def promote_candidate_to_shadow(
    candidate_id: str,
    *,
    research_root: str | Path = "data/research",
    promoted_by: str = "manual",
    overwrite_pointer: bool = False,
) -> dict[str, Any]:
    """Promote a candidate to shadow and write the promotion pointer.

    The candidate's status is updated to ``promoted_shadow`` and the
    promotion pointer YAML is written to
    ``<research_root>/promotions/shadow.yaml``.

    Parameters
    ----------
    candidate_id:
        Candidate identifier.
    research_root:
        Research root directory (default ``data/research``).
    promoted_by:
        Identifier of the promoter (default ``"manual"``).
    overwrite_pointer:
        When ``False`` (default), raise ``FileExistsError`` if
        ``shadow.yaml`` already exists.

    Returns
    -------
    dict
        The promotion pointer dict that was written.
    """
    root = Path(research_root)
    candidate = load_candidate(candidate_id, research_root=root)

    # Validate
    missing = validate_candidate(candidate)
    if missing:
        raise ValueError(
            f"Candidate {candidate_id!r} is missing required fields: "
            f"{', '.join(missing)}"
        )

    # Check pointer overwrite
    pointer_path = _shadow_pointer_path(root)
    if pointer_path.exists() and not overwrite_pointer:
        raise FileExistsError(
            f"Shadow promotion pointer already exists at {pointer_path}. "
            f"Use overwrite_pointer=True or --overwrite-pointer to replace."
        )

    now = _now_iso()
    candidate_path = _candidate_path(root, candidate_id)

    # Build pointer payload
    pointer: dict[str, Any] = {
        "artifact_type": "shadow_promotion_pointer",
        "promotion_target": "shadow",
        "candidate_id": candidate_id,
        "candidate_path": str(candidate_path),
        "signal_ref": {
            "signal_id": _get_nested(candidate, "signal_ref.signal_id"),
            "signal_run_id": _get_nested(candidate, "signal_ref.signal_run_id"),
        },
        "strategy_config_id": _get_nested(candidate, "strategy.strategy_config_id"),
        "strategy_template_id": _get_nested(candidate, "strategy.strategy_template_id"),
        "backtest_id": _get_nested(candidate, "backtest_ref.backtest_id"),
        "strategy_run_id": _get_nested(candidate, "backtest_ref.strategy_run_id"),
        "promoted_at": now,
        "promoted_by": promoted_by,
    }

    # Update candidate status
    candidate["status"] = "promoted_shadow"
    candidate["promotion_target"] = "shadow"
    candidate["promoted_at"] = now
    candidate["promoted_by"] = promoted_by

    # Write updated candidate back
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(
        yaml.safe_dump(candidate, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    # Write pointer
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    pointer_path.write_text(
        yaml.safe_dump(pointer, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    return pointer
