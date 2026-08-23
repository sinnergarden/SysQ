"""Canonical path resolution for Framework Stable 2.0 research artifacts.

Conventions
-----------
::

    data/research/
        labels/<label_id>/
            manifest.json
            labels.parquet
        signals/<signal_id>/<signal_run_id>/
            manifest.json
            predictions.parquet
        models/<model_id>/<model_version>/
            manifest.json
            model.bin
        backtests/<strategy_run_id>/<backtest_id>/
            manifest.json
            daily_summary.parquet
        experiments/<experiment_id>/
            manifest.json
"""

from __future__ import annotations

import re
from pathlib import Path

_INVALID_SEGMENTS = frozenset({"", ".", "..", "/", "\\", "~"})
_SEGMENT_RE = re.compile(r"^[a-zA-Z0-9_\-\.]+$")


def _sanitize_segment(segment: str, label: str) -> str:
    if not segment:
        raise ValueError(f"{label} must not be empty")
    if segment in _INVALID_SEGMENTS:
        raise ValueError(f"{label} invalid segment: {segment!r}")
    if not _SEGMENT_RE.match(segment):
        raise ValueError(
            f"{label} contains invalid characters: {segment!r} "
            "(allowed: letters, digits, _, -, .)"
        )
    return segment


class ResearchPaths:
    """Construct canonical artifact paths under a research root.

    No filesystem writes occur during construction (callers must create
    directories explicitly when needed).
    """

    def __init__(self, root: str | Path = "data/research") -> None:
        self.root = Path(root).resolve()

    # ── Labels ──────────────────────────────────────────────────────────

    def label_dir(self, label_id: str) -> Path:
        _sanitize_segment(label_id, "label_id")
        return self.root / "labels" / label_id

    def label_file(self, label_id: str, fmt: str = "parquet") -> Path:
        return self.label_dir(label_id) / f"labels.{fmt}"

    def label_manifest(self, label_id: str) -> Path:
        return self.label_dir(label_id) / "manifest.json"

    # ── Signals ─────────────────────────────────────────────────────────

    def signal_dir(self, signal_id: str, signal_run_id: str) -> Path:
        _sanitize_segment(signal_id, "signal_id")
        _sanitize_segment(signal_run_id, "signal_run_id")
        return self.root / "signals" / signal_id / signal_run_id

    def signal_file(self, signal_id: str, signal_run_id: str, fmt: str = "parquet") -> Path:
        return self.signal_dir(signal_id, signal_run_id) / f"predictions.{fmt}"

    def signal_manifest(self, signal_id: str, signal_run_id: str) -> Path:
        return self.signal_dir(signal_id, signal_run_id) / "manifest.json"

    def signal_eval_dir(self, signal_id: str, signal_run_id: str, label_id: str) -> Path:
        _sanitize_segment(label_id, "label_id")
        return self.signal_dir(signal_id, signal_run_id) / "eval" / label_id

    # ── Models ──────────────────────────────────────────────────────────

    def model_dir(self, model_id: str, model_version: str) -> Path:
        _sanitize_segment(model_id, "model_id")
        _sanitize_segment(model_version, "model_version")
        return self.root / "models" / model_id / model_version

    # ── Backtests ───────────────────────────────────────────────────────

    def backtest_dir(self, strategy_run_id: str, backtest_id: str) -> Path:
        _sanitize_segment(strategy_run_id, "strategy_run_id")
        _sanitize_segment(backtest_id, "backtest_id")
        return self.root / "backtests" / strategy_run_id / backtest_id

    # ── Experiments ─────────────────────────────────────────────────────

    def experiment_dir(self, experiment_id: str) -> Path:
        _sanitize_segment(experiment_id, "experiment_id")
        return self.root / "experiments" / experiment_id

    def window_checkpoint_dir(self, experiment_id: str, generator_id: str) -> Path:
        _sanitize_segment(experiment_id, "experiment_id")
        _sanitize_segment(generator_id, "generator_id")
        return (
            self.experiment_dir(experiment_id)
            / "window_checkpoints"
            / generator_id
        )

    # ── Utilities ───────────────────────────────────────────────────────

    def ensure_dir(self, path: Path) -> Path:
        """Create directory if it does not exist, return path."""
        path.mkdir(parents=True, exist_ok=True)
        return path
