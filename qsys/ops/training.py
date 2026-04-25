from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from qsys.config import cfg
from qsys.research.mainline import MAINLINE_OBJECTS


@dataclass(frozen=True)
class TrainingArtifacts:
    mainline_object_name: str
    bundle_id: str
    model_name: str
    model_path: str
    config_snapshot_path: str
    training_summary_path: str
    decisions_path: str
    training_report_path: str | None
    trained_at: str
    train_run_id: str
    command: list[str]


class TrainingInvocationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        command: list[str] | None = None,
        returncode: int | None = None,
        stdout_tail: str = "",
        stderr_tail: str = "",
    ) -> None:
        super().__init__(message)
        self.command = list(command or [])
        self.returncode = returncode
        self.stdout_tail = stdout_tail
        self.stderr_tail = stderr_tail


def _tail_text(text: str, *, max_lines: int = 20, max_chars: int = 4000) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    tail = "\n".join(lines[-max_lines:])
    return tail[-max_chars:]


def run_weekly_shadow_training(
    project_root: str | Path,
    *,
    mainline_object_name: str = "feature_173",
    train_run_id: str,
    extra_args: list[str] | None = None,
) -> TrainingArtifacts:
    project_root = Path(project_root).resolve()
    spec = MAINLINE_OBJECTS.get(mainline_object_name)
    if spec is None:
        raise TrainingInvocationError(f"Unsupported mainline_object_name: {mainline_object_name}")

    reports_dir = project_root / "experiments" / "reports"
    reports_before = {path.resolve() for path in reports_dir.glob("train_*.json")} if reports_dir.exists() else set()

    command = [
        sys.executable,
        str(project_root / "scripts" / "run_train.py"),
        "--model",
        "qlib_lgbm",
        "--bundle_id",
        spec.bundle_id,
    ]
    if extra_args:
        command.extend(extra_args)

    completed = subprocess.run(
        command,
        cwd=str(project_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise TrainingInvocationError(
            detail,
            command=command,
            returncode=completed.returncode,
            stdout_tail=_tail_text(completed.stdout),
            stderr_tail=_tail_text(completed.stderr),
        )

    model_dir = cfg.get_path("root") / "models" / spec.model_name
    config_snapshot_path = model_dir / "config_snapshot.json"
    training_summary_path = model_dir / "training_summary.json"
    decisions_path = model_dir / "decisions.json"

    missing = [
        str(path)
        for path in (config_snapshot_path, training_summary_path, decisions_path)
        if not path.exists()
    ]
    if missing:
        raise TrainingInvocationError(
            f"Training completed but artifacts are missing: {missing}",
            command=command,
            returncode=completed.returncode,
            stdout_tail=_tail_text(completed.stdout),
            stderr_tail=_tail_text(completed.stderr),
        )

    report_candidates = []
    if reports_dir.exists():
        report_candidates = sorted(
            (path.resolve() for path in reports_dir.glob("train_*.json") if path.resolve() not in reports_before),
            key=lambda item: item.stat().st_mtime,
        )
    training_report_path = str(report_candidates[-1]) if report_candidates else None
    trained_at = datetime.now().replace(microsecond=0).isoformat()

    return TrainingArtifacts(
        mainline_object_name=spec.mainline_object_name,
        bundle_id=spec.bundle_id,
        model_name=spec.model_name,
        model_path=str(model_dir),
        config_snapshot_path=str(config_snapshot_path),
        training_summary_path=str(training_summary_path),
        decisions_path=str(decisions_path),
        training_report_path=training_report_path,
        trained_at=trained_at,
        train_run_id=train_run_id,
        command=command,
    )
