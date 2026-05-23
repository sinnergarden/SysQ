"""Training contracts — lightweight result and manifest dataclasses.

These types are strategy-agnostic.  Every strategy's ``train()`` method
returns a ``TrainingResult``; model artifacts are accompanied by a
``ModelManifest`` for discoverability.

JSON serialisation uses the built-in ``dataclasses`` module — no MLflow
or heavy tracking framework.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class TrainingResult:
    """Outcome of a single training run.

    Every strategy's ``train()`` method should return one of these.
    """

    strategy_id: str
    model_version: str
    model_dir: str

    # Optional time bounds
    train_start: str | None = None
    train_end: str | None = None
    valid_start: str | None = None
    valid_end: str | None = None

    # Metrics (RankIC, Sharpe, MSE, …) — strategy-specific keys
    metrics: dict[str, Any] = field(default_factory=dict)

    # Discovered artifact paths relative to model_dir
    artifacts: dict[str, str] = field(default_factory=dict)

    # Status
    status: str = "success"  # "success" | "failed"
    message: str | None = None


@dataclass
class ModelManifest:
    """Metadata sidecar for a trained model directory.

    Written alongside model artifacts so that downstream consumers
    (DailyRunner, notification, replays) know what they are working with
    without inspecting model internals.
    """

    strategy_id: str
    model_version: str
    model_type: str  # e.g. "lightgbm_dual", "transformer", "dnn"
    feature_set: str
    label: dict[str, Any]
    train_window: dict[str, Any]
    created_at: str
    artifacts: dict[str, str]
    metrics: dict[str, Any]

    git_commit: str | None = None


# ── Serialisation helpers ──────────────────────────────────────────────────


def write_training_result(result: TrainingResult, path: str | Path) -> None:
    """Write *result* to *path* as JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(result), indent=2, ensure_ascii=False), encoding="utf-8")


def write_model_manifest(manifest: ModelManifest, path: str | Path) -> None:
    """Write *manifest* to *path* as JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(manifest), indent=2, ensure_ascii=False), encoding="utf-8")
