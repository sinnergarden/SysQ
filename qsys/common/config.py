"""Generic configuration utilities — pure functions, no qsys imports.

* ``read_yaml`` — safe YAML file loading
* ``load_strategy_config`` — load strategy YAML from standard location
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_STRATEGY_CONFIG_DIR = "configs/strategies"


def read_yaml(path: str | Path) -> dict[str, Any]:
    """Load a YAML file and return its contents as a dict.

    Raises
    ------
    FileNotFoundError
        If *path* does not exist.
    ValueError
        If the YAML root is not a ``dict``.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {p}")
    with p.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if not isinstance(config, dict):
        raise ValueError(f"Config must be a dict at the root, got {type(config).__name__}")
    return config


def load_strategy_config(
    strategy_id: str,
    project_root: Path,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Load strategy config from YAML.

    Default location: ``{project_root}/configs/strategies/{strategy_id}.yaml``.

    Parameters
    ----------
    strategy_id : str
        Strategy identifier (e.g. ``"alpha_v1"``).
    project_root : Path
        Root of the project repository.
    config_path : Path or None
        Explicit path to a YAML config file.  If given, *strategy_id* is
        ignored for file resolution (it is still used for validation).

    Returns
    -------
    dict
        Parsed YAML content.
    """
    if config_path is not None:
        path = Path(config_path)
    else:
        path = project_root / _STRATEGY_CONFIG_DIR / f"{strategy_id}.yaml"
    return read_yaml(path)
