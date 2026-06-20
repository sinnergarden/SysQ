"""FeatureSet resolver — the bridge between YAML and internal metadata.

This is the core "user-facing → internal" translation layer:

**Input:** FeatureSet YAML (``configs/features/*.yaml``)
**Process:**
  1. Discover and index all YAML files
  2. Resolve extends chains (additive-only)
  3. Map each feature name → FeatureSpec (registry_v2) or inventory fallback
  4. Validate: no missing, no broken, no circular extends
**Output:** ``ResolvedFeatureSet`` — a clean, validated list of feature names
  with per-feature metadata.

**Rules:**
- Missing feature → fail fast (ValueError).
- Broken feature → fail fast.
- Deprecated feature → warning (non-blocking).
- Silent skip is never allowed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from qsys.feature.feature_set import FeatureSetSpec, load_feature_set_yaml
from qsys.feature.registry_v2 import (
    FeatureSpec,
    check_broken_features,
    check_missing_features,
)


# ── Resolver index ──

_index: dict[str, Path] = {}
"""feature_set_id -> YAML path. Populated by ``discover_feature_sets()``."""


def discover_feature_sets(config_dir: str = "configs/features") -> dict[str, FeatureSetSpec]:
    """Scan *config_dir* for all ``*.yaml`` files and index by id.

    **Clears the global ``_index``** before populating, so every call is
    a fresh scan of the given directory.  This prevents stale indices
    when switching between different config directories.

    Returns a dict mapping ``feature_set_id`` → ``FeatureSetSpec``.
    Two files with the same ``feature_set_id`` → ValueError (fail fast).
    """
    _index.clear()
    base = Path(config_dir)
    if not base.exists():
        return {}

    specs: dict[str, FeatureSetSpec] = {}
    for p in sorted(base.rglob("*.yaml")):
        if p.name.startswith("__"):
            continue
        spec = load_feature_set_yaml(p)
        if spec.feature_set_id in specs:
            raise ValueError(
                f"Duplicate feature_set_id '{spec.feature_set_id}' in "
                f"{spec.path} and {specs[spec.feature_set_id].path}"
            )
        specs[spec.feature_set_id] = spec
        _index[spec.feature_set_id] = Path(spec.path)
    return specs


# ── Resolved output ──


@dataclass(frozen=True)
class ResolvedFeatureSet:
    """The resolved output of a FeatureSet YAML after full resolution.

    ``resolved_features`` is the flat, deduplicated, stable-ordered list
    of feature *names* that the YAML ultimately declares.
    """

    feature_set_id: str
    source_path: str
    resolved_features: tuple[str, ...]
    feature_ids: tuple[str, ...]
    raw_features: tuple[str, ...]
    derived_features: tuple[str, ...]
    required_transforms: tuple[str, ...]
    warnings: tuple[str, ...]
    spec_sources: tuple[dict[str, str], ...]
    """Per-feature source provenance: ``{"name": ..., "source": "registry_v2|inventory"}``."""


# ── Core resolution logic ──


def _builtin_index() -> dict[str, Path]:
    """Return the current ``_index`` dict (for testing)."""
    return dict(_index)


def resolve_feature_set(
    feature_set_id_or_path: str,
    *,
    config_dir: str = "configs/features",
) -> ResolvedFeatureSet:
    """Resolve a FeatureSet YAML to a ``ResolvedFeatureSet``.

    Parameters
    ----------
    feature_set_id_or_path:
        Either a ``feature_set_id`` (looked up in index) or a direct file path.
    config_dir:
        Directory to scan for YAML files.  Only used if the index is empty.
    allow_deprecated:
        If ``False``, treat deprecated features as hard errors.

    Returns
    -------
    ResolvedFeatureSet
        Fully resolved, validated feature set.

    Raises
    ------
    ValueError
        On: missing/broken features, circular extends, prohibited fields,
        empty resolved list, or unresolvable extends chain.
    """
    # 1. Ensure index is populated
    if not _index:
        discover_feature_sets(config_dir)

    # 2. Determine which spec to resolve
    path = Path(feature_set_id_or_path)
    if path.exists():
        spec = load_feature_set_yaml(path)
    else:
        yaml_path = _index.get(feature_set_id_or_path)
        if yaml_path is None:
            raise ValueError(
                f"Unknown feature_set_id '{feature_set_id_or_path}'. "
                f"Available: {sorted(_index)}"
            )
        spec = load_feature_set_yaml(yaml_path)

    # 3. Resolve features list
    resolved_names, warnings = _resolve_features(spec, _seen=set())

    # 4. Validate against registry / inventory
    specs_sources: list[dict[str, str]] = []
    for name in resolved_names:
        source_info, info_warnings = _resolve_single_feature(name)
        specs_sources.append(source_info)
        warnings.extend(info_warnings)

    # 5. Classify raw vs derived, collect transforms
    raw_names: list[str] = []
    derived_names: list[str] = []
    transforms: set[str] = set()

    for info in specs_sources:
        if info["kind"] == "raw":
            raw_names.append(info["name"])
        else:
            derived_names.append(info["name"])
        if info.get("compute_fn"):
            transforms.add(info["compute_fn"])

    # 6. Detect derived features with no compute_fn (unresolved transforms)
    for info in specs_sources:
        if info["kind"] == "derived" and not info.get("compute_fn"):
            warnings.append(
                f"Feature '{info['name']}' is derived but has no compute_fn "
                f"(unresolved transform)"
            )

    return ResolvedFeatureSet(
        feature_set_id=spec.feature_set_id,
        source_path=spec.path,
        resolved_features=tuple(resolved_names),
        feature_ids=tuple(info["feature_id"] for info in specs_sources),
        raw_features=tuple(raw_names),
        derived_features=tuple(derived_names),
        required_transforms=tuple(sorted(transforms)),
        warnings=tuple(warnings),
        spec_sources=tuple(specs_sources),
    )


def _resolve_features(
    spec: FeatureSetSpec,
    _seen: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Resolve the flat feature list for *spec*.

    Returns ``(resolved_names, warnings)``.

    *Legacy mode:* return ``features`` as-is.
    *Additive mode:* recursively resolve ``extends``, then append
    ``add_features`` with stable-order dedup.
    """
    if _seen is None:
        _seen = set()

    if spec.is_legacy():
        return list(spec.features), []

    # ── Additive mode ──
    if spec.is_additive():
        # Circular extends detection
        if spec.feature_set_id in _seen:
            raise ValueError(
                f"Circular extends detected: '{spec.feature_set_id}' "
                f"already in resolution chain {_seen}"
            )
        _seen.add(spec.feature_set_id)

        # Resolve base
        if spec.extends not in _index:
            raise ValueError(
                f"extends='{spec.extends}' in '{spec.feature_set_id}' "
                f"not found. Available: {sorted(_index)}"
            )
        base_path = _index[spec.extends]
        base_spec = load_feature_set_yaml(base_path)
        base_names, base_warnings = _resolve_features(base_spec, _seen=_seen)

        # Append add_features with dedup
        result = list(base_names)
        seen_names = set(base_names)
        for f in spec.add_features:
            if f not in seen_names:
                result.append(f)
                seen_names.add(f)
        warnings = list(base_warnings)
        return result, warnings

    # Should never reach here — FeatureSetSpec validation ensures
    # at least one mode is active.
    raise ValueError(f"FeatureSet {spec.feature_set_id} has no features or extends")


def _is_qlib_expression(name: str) -> bool:
    """Heuristic: qlib expressions contain ``$``, ``(``, ``)``, or ``/``."""
    return bool(name.startswith("$") or "(" in name or ")" in name or "/" in name)


def _resolve_single_feature(
    name: str,
) -> tuple[dict[str, str], list[str]]:
    """Resolve a single feature name against registry_v2, then inventory CSV.

    Qlib expressions (like ``$close`` or ``Ref($close,5)/$close``) are treated
    as pass-through raw features — they are not looked up in the registry.

    Returns
    -------
    (info_dict, warnings_list)
        info_dict has keys: ``name``, ``feature_id``, ``kind``,
        ``compute_fn``, ``source`` (``"registry_v2"``, ``"inventory"``, or
        ``"qlib_expression"``).

    Raises ``ValueError`` if the feature is missing or broken.
    """
    warnings: list[str] = []

    # Qlib expressions → pass-through raw
    if _is_qlib_expression(name):
        return {
            "name": name,
            "feature_id": name,
            "kind": "raw",
            "compute_fn": "",
            "source": "qlib_expression",
            "status": "active",
        }, warnings

    # Try registry_v2 first
    spec: FeatureSpec | None = None
    source = "registry_v2"

    from qsys.feature.registry_v2 import get_by_name

    spec = get_by_name(name)

    if spec is None:
        # Fallback: inventory CSV
        source = "inventory"
        inv_info = _lookup_inventory(name)
        if inv_info is None:
            raise ValueError(
                f"Feature '{name}' not found in registry_v2 or inventory CSV"
            )
        # Inventory fallback: also check broken/deprecated
        inv_status = inv_info.get("status", "active")
        if inv_status == "broken":
            raise ValueError(
                f"Feature '{name}' from inventory CSV is status=broken "
                f"and must not enter any active feature list"
            )
        if inv_status == "deprecated":
            warnings.append(
                f"Feature '{name}' is status=deprecated (from inventory CSV)"
            )
        return {
            "name": name,
            "feature_id": inv_info["feature_id"],
            "kind": inv_info["kind"],
            "compute_fn": inv_info["compute_fn"],
            "source": "inventory",
            "status": inv_status,
        }, warnings

    # Check broken — registry_v2
    if spec.status == "broken":
        raise ValueError(
            f"Feature '{name}' (feature_id={spec.feature_id}) is status=broken "
            f"and must not enter any active feature list"
        )

    # Check deprecated — registry_v2
    if spec.status == "deprecated":
        warnings.append(
            f"Feature '{name}' (feature_id={spec.feature_id}) is status=deprecated"
        )

    return {
        "name": spec.name,
        "feature_id": spec.feature_id,
        "kind": spec.kind,
        "compute_fn": spec.compute_fn or "",
        "source": "registry_v2",
        "status": spec.status,
    }, warnings


_INVENTORY_CACHE: list[dict[str, str]] | None = None
"""Cached contents of ``artifacts/feature_registry_audit/feature_inventory.csv``."""


def _lookup_inventory(name: str) -> dict[str, str] | None:
    """Look up a feature in the inventory CSV (fallback).

    Caches the CSV on first call.
    """
    global _INVENTORY_CACHE
    if _INVENTORY_CACHE is None:
        import csv

        inv_path = (
            Path(__file__).resolve().parents[2]
            / "artifacts"
            / "feature_registry_audit"
            / "feature_inventory.csv"
        )
        if not inv_path.exists():
            _INVENTORY_CACHE = []
            return None
        with open(inv_path, newline="") as f:
            _INVENTORY_CACHE = list(csv.DictReader(f))

    for row in _INVENTORY_CACHE:
        if row.get("feature_name") == name or row.get("feature_id") == name:
            return row
    return None
