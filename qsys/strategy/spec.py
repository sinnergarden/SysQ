"""StrategySpec — static strategy identity, config, lifecycle, and research definition.

A ``StrategySpec`` is the **static definition** of a strategy: its identity,
configuration, lifecycle stage, and research context.  It is strategy-agnostic
and has no runtime behaviour.

``StrategySpec`` is the single source of truth for strategy identity.
``StrategyAdapter`` is the executable runtime implementation.
See ``docs/architecture/strategy-spec-vs-adapter.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml


# ── Lifecycle stages ───────────────────────────────────────────────────────────

SUPPORTED_STAGES = frozenset({
    "research",
    "candidate",
    "production",
    "rejected",
    "archived",
})


def validate_stage(stage: str) -> None:
    """Raise ``ValueError`` if *stage* is not a supported lifecycle stage."""
    if stage not in SUPPORTED_STAGES:
        raise ValueError(
            f"unsupported stage {stage!r}; "
            f"must be one of {sorted(SUPPORTED_STAGES)}"
        )


def is_runtime_stage(stage: str) -> bool:
    """Return ``True`` if *stage* is a runtime-ready stage (candidate, production)."""
    return stage in ("candidate", "production")


# ── StrategySpec ───────────────────────────────────────────────────────────────


@dataclass
class StrategySpec:
    """Static strategy identity, configuration, lifecycle, and research definition.

    Fields
    ------
    strategy_id : str
        Stable, lowercase snake_case identifier.  Single source of truth.
    stage : str
        Lifecycle stage: ``research``, ``candidate``, ``production``,
        ``rejected``, or ``archived``.
    family : str | None
        Optional grouping label (e.g. ``momentum``, ``value``, ``ml``).
    display_name : str
        Human-readable name for UI and notifications.
    owner : str | None
        Researcher or team responsible.
    universe : str
        Instrument universe identifier (e.g. ``csi300``).
    feature_set : str
        Feature set / config name.
    model_version : str | None
        Current model version string.
    signal_version : str | None
        Current signal version string.
    account_id : str | None
        Runtime account identifier (may be ``None`` for research-stage specs).
    hypothesis : str | None
        Research hypothesis text.
    label : dict[str, Any]
        Label configuration.
    model : dict[str, Any]
        Model configuration (training hyper-parameters, etc.).
    signal : dict[str, Any]
        Signal / scoring configuration.
    portfolio : dict[str, Any]
        Portfolio construction parameters (``top_n``, buffers, cap, etc.).
    paths : dict[str, Any]
        File-system paths (model dir, predictions dir, ledger db, etc.).
    lifecycle : dict[str, Any]
        Lifecycle metadata (creation date, history, etc.).
    evaluation : dict[str, Any]
        Evaluation metadata (latest metrics, last eval date, etc.).
    promotion_gates : dict[str, Any]
        Promotion check results or gate conditions.
    raw_config : dict[str, Any]
        The complete raw YAML config dict, unmodified.
    config_path : str | None
        Path to the YAML config file, if loaded from disk.
    """

    strategy_id: str
    stage: str = "research"
    family: str | None = None
    display_name: str = ""
    owner: str | None = None
    universe: str = ""
    feature_set: str = ""
    model_version: str | None = None
    signal_version: str | None = None
    account_id: str | None = None
    hypothesis: str | None = None
    label: dict[str, Any] = field(default_factory=dict)
    model: dict[str, Any] = field(default_factory=dict)
    signal: dict[str, Any] = field(default_factory=dict)
    portfolio: dict[str, Any] = field(default_factory=dict)
    paths: dict[str, Any] = field(default_factory=dict)
    lifecycle: dict[str, Any] = field(default_factory=dict)
    evaluation: dict[str, Any] = field(default_factory=dict)
    promotion_gates: dict[str, Any] = field(default_factory=dict)
    raw_config: dict[str, Any] = field(default_factory=dict)
    config_path: str | None = None

    def __post_init__(self) -> None:
        validate_stage(self.stage)
        if not self.strategy_id:
            raise ValueError("strategy_id is required")
        if not self.display_name:
            self.display_name = self.strategy_id

    def to_dict(self) -> dict[str, Any]:
        """Return a plain dict (omits ``raw_config`` and ``config_path``)."""
        d = asdict(self)
        d.pop("raw_config", None)
        d.pop("config_path", None)
        return d


# ── Loaders ────────────────────────────────────────────────────────────────────


def spec_from_config(
    config: dict[str, Any],
    path: str | Path | None = None,
) -> StrategySpec:
    """Build a ``StrategySpec`` from a parsed YAML config dict.

    Parameters
    ----------
    config
        Parsed YAML config dict.  Top-level keys map to ``StrategySpec`` fields.
    path
        Optional file path (stored as ``config_path``).

    Returns
    -------
    StrategySpec
    """
    known_keys = {
        "strategy_id", "stage", "family", "display_name", "owner",
        "universe", "feature_set", "model_version", "signal_version",
        "account_id", "hypothesis", "label", "model", "signal",
        "portfolio", "paths", "lifecycle", "evaluation", "promotion_gates",
    }
    kwargs: dict[str, Any] = {}
    extras: dict[str, Any] = {}

    for k, v in config.items():
        if k in known_keys:
            if k == "stage":
                kwargs[k] = v if v is not None else "research"
            elif k in ("label", "model", "signal", "portfolio", "paths",
                       "lifecycle", "evaluation", "promotion_gates"):
                kwargs[k] = v if isinstance(v, dict) else {}
            else:
                kwargs[k] = v
        else:
            extras[k] = v

    kwargs["raw_config"] = dict(config)
    kwargs["config_path"] = str(path) if path else None

    return StrategySpec(**kwargs)


def load_strategy_spec(path: str | Path) -> StrategySpec:
    """Load a single ``StrategySpec`` from a YAML file.

    Raises ``FileNotFoundError`` if *path* does not exist.
    """
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if not isinstance(config, dict):
        raise ValueError(f"expected a YAML dict in {path}, got {type(config).__name__}")
    return spec_from_config(config, path=path)


def load_strategy_specs(root: str | Path) -> list[StrategySpec]:
    """Load all ``StrategySpec``\\s from YAML files under *root*.

    Scans ``root/**/*.yaml`` and ``root/**/*.yml`` recursively.
    Skips non-dict YAML files.
    """
    root = Path(root)
    pattern = "**/*.y*ml"
    specs: list[StrategySpec] = []
    for p in sorted(root.glob(pattern)):
        if not p.is_file():
            continue
        try:
            spec = load_strategy_spec(p)
            specs.append(spec)
        except (ValueError, yaml.YAMLError) as exc:
            raise ValueError(f"failed to load {p}: {exc}") from exc
    return specs
