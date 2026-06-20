"""FeatureSpec registry — per-feature metadata for the SysQ feature system.

This is a backward-compatible addition alongside ``qsys.feature.registry.FEATURE_GROUPS``.
All features registered here are grouped by the existing group structure; the main
addition is per-feature ``FeatureSpec`` metadata.

feature_id
    Permanent, stable identifier for the feature.  Never changes even if the
    output column name is renamed.
name
    The actual column name in the DataFrame / qlib panel.  Must match the
    existing downstream name for backward compatibility.
group
    Logical group (matches one of the ``FEATURE_GROUPS`` keys).
kind
    ``"raw"`` — directly from data source or qlib table, no business logic.
    ``"derived"`` — computed from raw features or other derived features.
source
    For raw features: the data source table (e.g. ``"daily"``, ``"fina_indicator"``,
    ``"margin_detail"``, ``"shareholder"``).  For derived features: the
    implementing module path.
dependencies
    Tuple of feature names this feature directly depends on.  Empty for raw
    features.
compute_fn
    Fully qualified function name (e.g. ``"build_microstructure_features"``)
    for derived features; ``None`` for raw.
dtype
    Expected pandas/numpy dtype, or ``None`` if unknown.
pit_type
    ``"point_in_time"`` — reported at a specific date (financial statements).
    ``"rolling_past"`` — computed over a historical lookback window.
    ``"cross_sectional"`` — ranked / normalised across stocks on the same date.
    ``"static"`` — does not change (industry code, stock code).
cache_scope
    ``"per_date"`` — value depends on date only.
    ``"per_instrument"`` — value depends on instrument only.
    ``"panel"`` — value is a panel of (date, instrument).
    ``"none"`` — not cacheable.
status
    ``"active"`` — in production or active research.
    ``"experimental"`` — under evaluation, not in production config.
    ``"deprecated"`` — still available but scheduled for removal.
    ``"broken"`` — known issues, must NOT enter active feature list.
description
    Human-readable description of what this feature measures.
owner
    Optional: the team or person who owns this feature.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


FeatureKind = Literal["raw", "derived"]
PitType = Literal["point_in_time", "rolling_past", "cross_sectional", "static"]
CacheScope = Literal["per_date", "per_instrument", "panel", "none"]
FeatureStatus = Literal["active", "experimental", "deprecated", "broken"]


@dataclass(frozen=True)
class FeatureSpec:
    """Per-feature metadata spec."""

    feature_id: str
    name: str
    group: str
    kind: FeatureKind
    source: str | None = None
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    compute_fn: str | None = None
    dtype: str | None = None
    pit_type: PitType = "rolling_past"
    cache_scope: CacheScope = "none"
    status: FeatureStatus = "active"
    description: str = ""
    owner: str | None = None


# ── Helper: build FeatureSpec from a dict (for migration from FEATURE_GROUPS) ──


def spec_from_dict(d: dict) -> FeatureSpec:
    """Construct a FeatureSpec from a plain dict."""
    return FeatureSpec(
        feature_id=d["feature_id"],
        name=d.get("name", d["feature_id"]),
        group=d.get("group", ""),
        kind=d.get("kind", "derived"),
        source=d.get("source"),
        dependencies=tuple(d.get("dependencies", []) or []),
        compute_fn=d.get("compute_fn"),
        dtype=d.get("dtype"),
        pit_type=d.get("pit_type", "rolling_past"),
        cache_scope=d.get("cache_scope", "none"),
        status=d.get("status", "active"),
        description=d.get("description", ""),
        owner=d.get("owner"),
    )


# ── Global registries ──

# _FEATURE_SPECS: dict[str, FeatureSpec] keyed by feature_id
_FEATURE_SPECS: dict[str, FeatureSpec] = {}

# _NAME_INDEX: dict[str, str] — name -> feature_id (for name-based lookup)
_NAME_INDEX: dict[str, str] = {}


def register(spec: FeatureSpec) -> None:
    """Register a single FeatureSpec.

    Raises ValueError if feature_id or name is already registered.
    """
    if spec.feature_id in _FEATURE_SPECS:
        raise ValueError(
            f"FeatureSpec feature_id '{spec.feature_id}' already registered"
        )
    if spec.name in _NAME_INDEX:
        existing_id = _NAME_INDEX[spec.name]
        if existing_id != spec.feature_id:
            raise ValueError(
                f"Feature name '{spec.name}' already registered (feature_id={existing_id})"
            )
    _FEATURE_SPECS[spec.feature_id] = spec
    _NAME_INDEX[spec.name] = spec.feature_id


def register_batch(specs: list[FeatureSpec]) -> None:
    """Register multiple FeatureSpecs."""
    for sp in specs:
        register(sp)


def get_by_id(feature_id: str) -> FeatureSpec | None:
    """Lookup a FeatureSpec by feature_id."""
    return _FEATURE_SPECS.get(feature_id)


def get_by_name(name: str) -> FeatureSpec | None:
    """Lookup a FeatureSpec by output column name."""
    fid = _NAME_INDEX.get(name)
    if fid is None:
        return None
    return _FEATURE_SPECS.get(fid)


def list_specs(
    status: FeatureStatus | None = None,
    kind: FeatureKind | None = None,
    group: str | None = None,
) -> list[FeatureSpec]:
    """List registered specs, optionally filtered."""
    results = []
    for spec in _FEATURE_SPECS.values():
        if status is not None and spec.status != status:
            continue
        if kind is not None and spec.kind != kind:
            continue
        if group is not None and spec.group != group:
            continue
        results.append(spec)
    return results


def resolve_dependencies(
    feature_id: str, *, visited: set[str] | None = None
) -> list[str]:
    """Resolve the full dependency chain for a feature (topological order).

    Returns a list of *kind='raw'* feature names that *feature_id* ultimately
    depends on.  Raises ``ValueError`` on circular dependency.
    """
    if visited is None:
        visited = set()
    if feature_id in visited:
        raise ValueError(f"Circular dependency detected: {feature_id}")
    visited.add(feature_id)
    spec = get_by_id(feature_id)
    if spec is None:
        return []
    if spec.kind == "raw":
        return [spec.name]
    deps: list[str] = []
    for dep_name in spec.dependencies:
        dep_spec = get_by_name(dep_name)
        if dep_spec is None:
            # Could be a raw feature not in the registry yet — add as-is
            deps.append(dep_name)
        else:
            deps.extend(resolve_dependencies(dep_spec.feature_id, visited=visited))
    return deps


def check_broken_features(names: list[str]) -> list[str]:
    """Check if any of the named features is status=broken.

    Returns a list of broken feature names found.
    """
    broken: list[str] = []
    for name in names:
        spec = get_by_name(name)
        if spec is not None and spec.status == "broken":
            broken.append(name)
    return broken


def check_deprecated_features(names: list[str]) -> list[str]:
    """Check if any of the named features is status=deprecated.

    Returns a list of deprecated feature names found.
    """
    deprecated: list[str] = []
    for name in names:
        spec = get_by_name(name)
        if spec is not None and spec.status == "deprecated":
            deprecated.append(name)
    return deprecated


def check_missing_features(names: list[str]) -> list[str]:
    """Check which feature names are NOT in the registry at all."""
    missing: list[str] = []
    for name in names:
        if get_by_name(name) is None:
            missing.append(name)
    return missing


def verify_feature_list(features: list[str]) -> dict[str, list[str]]:
    """Verify a feature list against the registry.

    Returns a dict with keys:
        broken: list of broken feature names
        deprecated: list of deprecated feature names
        missing: list of feature names not found in registry
    """
    return {
        "broken": check_broken_features(features),
        "deprecated": check_deprecated_features(features),
        "missing": check_missing_features(features),
    }
