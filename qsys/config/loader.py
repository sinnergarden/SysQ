"""
Config loader — lightweight YAML config management.

Pure functions, no classes, no global state.
Config hierarchy: spec.py defaults < YAML file < CLI overrides
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml


def load_yaml_config(path: str | Path) -> dict[str, Any]:
    """Load config dict from a YAML file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {p}")
    with p.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if not isinstance(config, dict):
        raise ValueError(f"Config must be a dict, got {type(config).__name__}")
    return config


def merge_cli_overrides(config: dict, cli_args: object) -> dict[str, Any]:
    """Return a deep copy of config with CLI values overlaid.

    Only non-None CLI attributes are applied.  Mapping:
        cli_args.universe    -> config["execution"]["universe"]
        cli_args.start       -> config["execution"]["start"]
        cli_args.end         -> config["execution"]["end"]
        cli_args.data_end    -> config["execution"]["data_end"]
        cli_args.price_mode  -> config["execution"]["price_mode"]
        cli_args.top_n       -> config["portfolio"]["top_n"]
        cli_args.output_dir  -> config["execution"]["output_dir"]
    """
    result = copy.deepcopy(config)

    _ensure_section(result, "execution")
    _ensure_section(result, "portfolio")

    overrides = {
        "universe": ("execution", "universe"),
        "start": ("execution", "start"),
        "end": ("execution", "end"),
        "data_end": ("execution", "data_end"),
        "price_mode": ("execution", "price_mode"),
        "top_n": ("portfolio", "top_n"),
        "output_dir": ("execution", "output_dir"),
    }

    for attr, (section, key) in overrides.items():
        val = getattr(cli_args, attr, None)
        if val is not None:
            result[section][key] = val

    return result


def write_resolved_config(config: dict, path: str | Path) -> None:
    """Write resolved config dict to a YAML file (human-readable)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)


def _ensure_section(config: dict, name: str) -> None:
    if name not in config:
        config[name] = {}
