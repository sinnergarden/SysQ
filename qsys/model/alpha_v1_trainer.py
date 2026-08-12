"""AlphaV1Trainer — DEPRECATED legacy trainer for alpha_v1.

Not used by current production training path.  Kept only for historical
compatibility.

Do NOT use for new training work.  Do NOT add new feature-combination-specific
training scripts (e.g. ``run_alpha_vX_weekly_train.py``).

Future direction: generic training entrypoint driven by::

    model_id + label_id + feature_list_id + universe + train_window + output_dir + pointer_mode

The underlying script called by this class (``run_alpha_v1_weekly_train.py``)
has been moved to ``scripts/deprecated/`` and is also legacy.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from qsys.model.training import TrainingResult

# Legacy-only path — not an active production training entrypoint.
_TRAIN_SCRIPT = "scripts/deprecated/run_alpha_v1_weekly_train.py"


def _discover_artifacts(project_root: Path, model_dir_str: str) -> dict[str, str]:
    """Discover expected artifact files under *model_dir_str* (relative or absolute)."""
    model_dir = Path(model_dir_str)
    if not model_dir.is_absolute():
        model_dir = project_root / model_dir
    artifacts: dict[str, str] = {}
    expected = [
        "model_5d.txt", "model_20d.txt",
        "center_5d.json", "scale_5d.json",
        "center_20d.json", "scale_20d.json",
        "features.json",
        "meta.json",
    ]
    for name in expected:
        p = model_dir / name
        if p.exists():
            artifacts[name] = str(p.relative_to(model_dir) if p.is_relative_to(model_dir) else p)
    return artifacts


def _try_discover_metrics(project_root: Path, model_dir_str: str) -> dict[str, Any]:
    """Attempt to extract RankIC from meta.json."""
    metrics: dict[str, Any] = {}
    model_dir = Path(model_dir_str)
    if not model_dir.is_absolute():
        model_dir = project_root / model_dir
    meta_path = model_dir / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            metrics["train_start"] = meta.get("train_start", "")
            metrics["train_end"] = meta.get("train_end", "")
            metrics["feature_count"] = meta.get("feature_count", 0)
            metrics["training_rows"] = meta.get("training_rows", 0)
        except Exception:
            pass
    return metrics


def _discover_model_dir(project_root: Path) -> str:
    """Find the latest timestamped model dir under experiments/alpha_v1_models/.

    The training script creates a timestamped directory (e.g. ``20260704_153000``).
    We pick the directory with the newest name alphabetically (ISO timestamps
    sort chronologically).

    Returns
    -------
    str
        Relative path from *project_root* to the model directory.
        Empty string if nothing was found.
    """
    base = project_root / "experiments" / "alpha_v1_models"
    if not base.exists():
        return ""
    candidates = sorted(
        [
            d for d in base.iterdir()
            if d.is_dir()
            and not d.is_symlink()
            and not d.name.startswith(".")
            and d.name.lower() != "latest"
        ],
        reverse=True,
    )
    if not candidates:
        return ""
    model_dir = candidates[0]
    try:
        return str(model_dir.relative_to(project_root))
    except ValueError:
        return str(model_dir)


class AlphaV1Trainer:
    """DEPRECATED — Alpha V1 weekly training wrapper.

    .. deprecated::
        This trainer is not part of the current production training path.
        Kept for historical compatibility.
        Future direction: generic training entrypoint driven by
        ``model_id + label_id + feature_list_id + universe + train_window``.

    Usage (legacy)::

        trainer = AlphaV1Trainer(project_root=Path("."))
        result = trainer.run(ctx)
    """

    def __init__(
        self,
        project_root: Path,
        config: dict | None = None,
        model_version: str | None = None,
    ) -> None:
        self._project_root = project_root
        self._config = config or {}
        self._model_version = model_version

    def run(self, ctx: Any) -> TrainingResult:
        """Execute the weekly training script and produce a TrainingResult.

        Parameters
        ----------
        ctx : DailyRunContext
            Runtime context (used for project_root, strategy_id, etc.).
        """
        strategy_id = getattr(ctx, "strategy_id", "alpha_v1")
        model_version = self._model_version or getattr(ctx, "model_version", datetime.now().strftime("%Y%m%d"))

        train_script = str(self._project_root / _TRAIN_SCRIPT)
        if not Path(train_script).exists():
            return TrainingResult(
                strategy_id=strategy_id,
                model_version=model_version,
                model_dir="",
                status="failed",
                message=f"Training script not found: {train_script}",
            )

        # Build args
        args = [sys.executable, train_script]
        end_date = self._config.get("training", {}).get("end_date")
        if end_date:
            args.extend(["--end-date", end_date])
        no_notify = getattr(ctx, "no_notify", False)
        if no_notify:
            args.append("--no-notify")

        print(f"  🚀 Launching: {' '.join(args)}")
        print(f"  CWD: {self._project_root}")

        result = subprocess.run(
            args,
            cwd=str(self._project_root),
            capture_output=False,
        )

        if result.returncode != 0:
            return TrainingResult(
                strategy_id=strategy_id,
                model_version=model_version,
                model_dir="",
                status="failed",
                message=f"Training exited with code {result.returncode}",
            )

        # Resolve model directory by discovering the newest timestamped dir
        model_dir_str = _discover_model_dir(self._project_root)
        if not model_dir_str:
            return TrainingResult(
                strategy_id=strategy_id,
                model_version=model_version,
                model_dir="",
                status="failed",
                message="No model dir found after training",
            )

        # Training publishes an immutable artifact only.  Repointing shadow
        # here would invalidate the promotion pointer's pinned model hash and
        # implicitly promote an unreviewed model.  Model-pointer publication
        # belongs to the explicit candidate promotion workflow.
        print(
            f"  ✓ Model artifact ready for promotion review: {model_dir_str}"
        )

        artifacts = _discover_artifacts(self._project_root, model_dir_str)
        metrics = _try_discover_metrics(self._project_root, model_dir_str)

        return TrainingResult(
            strategy_id=strategy_id,
            model_version=model_version,
            model_dir=model_dir_str,
            status="success",
            artifacts=artifacts,
            metrics=metrics,
        )
