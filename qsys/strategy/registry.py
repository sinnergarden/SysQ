"""Strategy registry — maps ``strategy_id`` → adapter class.

Simple static dictionary.  No plugin discovery, no YAML-driven class loading,
no dynamic imports.

Usage::

    from qsys.strategy.registry import get_strategy_class, create_strategy

    cls = get_strategy_class("alpha_v1")          # → AlphaV1StrategyAdapter
    strat = create_strategy("alpha_v1", config)    # → instance
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

# ── Built-in strategies ──────────────────────────────────────────────────────

STRATEGY_REGISTRY: dict[str, type[Any]] = {}


def register(strategy_id: str, cls: type[Any]) -> None:
    """Register a strategy adapter class under *strategy_id*."""
    STRATEGY_REGISTRY[strategy_id] = cls


def get_strategy_class(strategy_id: str) -> type[Any]:
    """Return the adapter *class* registered under *strategy_id*.

    Raises ``ValueError`` if the strategy is unknown.
    """
    try:
        return STRATEGY_REGISTRY[strategy_id]
    except KeyError:
        raise ValueError(
            f"Unknown strategy_id='{strategy_id}'. "
            f"Known: {sorted(STRATEGY_REGISTRY)}"
        )


def create_strategy(
    strategy_id: str,
    config: dict | None = None,
    project_root: Path | None = None,
) -> Any:
    """Construct an adapter *instance* for *strategy_id*.

    If the adapter class defines ``from_config``, that is called with
    ``(config, project_root=project_root)``; otherwise the adapter is
    instantiated with ``project_root=project_root``.
    """
    cls = get_strategy_class(strategy_id)
    if hasattr(cls, "from_config"):
        return cls.from_config(config, project_root=project_root)
    return cls(project_root=project_root)


# ── Register built-in strategies ─────────────────────────────────────────────

from qsys.strategy.alpha_v1.adapter import AlphaV1StrategyAdapter  # noqa: E402

register("alpha_v1", AlphaV1StrategyAdapter)
