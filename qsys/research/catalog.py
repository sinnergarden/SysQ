"""Strategy catalog — scans config files, builds a summary DataFrame.

The catalog uses **config files**, not the runtime registry, as its source
of truth.  This allows research-stage strategies (which may not have a
``StrategyAdapter`` or a registry entry) to appear in the catalog.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from qsys.strategy.spec import StrategySpec, load_strategy_specs


def load_strategy_configs(root: str | Path) -> list[dict[str, Any]]:
    """Load raw YAML configs from all ``*.yaml`` files under *root*.

    Returns a list of parsed config dicts.  Skips non-dict YAML files.
    """
    from qsys.strategy.spec import load_strategy_specs

    specs = load_strategy_specs(root)
    return [s.raw_config for s in specs]


def list_strategy_specs(
    root: str | Path,
    stage: str | None = None,
) -> list[StrategySpec]:
    """Load all specs from *root*, optionally filtered by *stage*.

    Returns specs sorted by ``strategy_id``.
    """
    from qsys.strategy.spec import load_strategy_specs

    specs = load_strategy_specs(root)
    if stage is not None:
        specs = [s for s in specs if s.stage == stage]
    return sorted(specs, key=lambda s: s.strategy_id)


def build_strategy_catalog(
    config_root: str | Path,
    reports_root: str | Path | None = None,
) -> pd.DataFrame:
    """Build a summary catalog DataFrame from strategy config files.

    Parameters
    ----------
    config_root
        Root directory containing per-strategy YAML configs.
    reports_root
        Optional root for evaluation reports (not yet implemented).

    Returns
    -------
    pd.DataFrame
        Columns: ``strategy_id``, ``stage``, ``family``, ``display_name``,
        ``universe``, ``feature_set``, ``model_version``, ``signal_version``,
        ``owner``, ``account_id``, ``config_path``, ``latest_eval_at``,
        ``latest_status``.
    """
    specs = load_strategy_specs(config_root)
    rows = []
    for s in specs:
        eval_info = s.evaluation or {}
        rows.append({
            "strategy_id": s.strategy_id,
            "stage": s.stage,
            "family": s.family,
            "display_name": s.display_name,
            "universe": s.universe,
            "feature_set": s.feature_set,
            "model_version": s.model_version,
            "signal_version": s.signal_version,
            "owner": s.owner,
            "account_id": s.account_id,
            "config_path": s.config_path,
            "latest_eval_at": eval_info.get("latest_eval_at"),
            "latest_status": eval_info.get("latest_status"),
        })
    return pd.DataFrame(rows)
