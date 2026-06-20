"""FeatureSetSpec — user-facing FeatureSet YAML schema.

**Rule:** FeatureSet YAML is the only user-facing layer.
FeatureSpec, TransformSpec, Resolver, Cache, Manifest are internal details.

Supported YAML modes:
1. **Legacy** — ``features`` list (compatible with existing YAML files)::

    feature_list_id: my_set
    features:
      - ret_60d
      - margin_crowding_score

2. **Additive** — ``extends`` + ``add_features`` (new, Phase 2+)::

    feature_set_id: my_set_v2
    extends: base_set
    add_features:
      - industry_ret_20d

**Prohibited in ALL modes:**

- ``exclude_features`` — must not appear
- ``exclude_groups`` — must not appear
- ``features`` and ``extends`` at the same time — ambiguous
- Duplicate features within a single YAML file
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class FeatureSetSpec:
    """Deserialised representation of a FeatureSet YAML file.

    One of two mutually exclusive modes:
    - ``features`` (legacy): a flat list of feature names.
    - ``extends + add_features`` (additive): inherit from another set
      and append.
    """

    feature_set_id: str
    path: str

    # Legacy mode
    features: tuple[str, ...] = field(default_factory=tuple)

    # Additive mode
    extends: str | None = None
    add_features: tuple[str, ...] = field(default_factory=tuple)

    description: str = ""

    # ── Validation helpers ──

    def is_legacy(self) -> bool:
        return len(self.features) > 0

    def is_additive(self) -> bool:
        return self.extends is not None


def load_feature_set_yaml(path: Path | str) -> FeatureSetSpec:
    """Load and validate a single FeatureSet YAML file.

    Raises ``ValueError`` on any of:
    - ``exclude_features`` or ``exclude_groups`` present
    - ``features`` and ``extends`` at the same time
    - Neither ``features`` nor ``extends`` populated
    - Duplicate feature names within ``features`` or ``add_features``
    - Missing ``feature_set_id`` / ``feature_list_id``
    """
    import yaml

    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"YAML root is not a dict: {path}")

    # ── Prohibited fields ──
    _check_prohibited(raw, path)

    # ── Identity ──
    feature_set_id = raw.get("feature_set_id") or raw.get("feature_list_id")
    if not feature_set_id:
        raise ValueError(f"Missing feature_set_id/feature_list_id in {path}")

    description = raw.get("description", "")
    features = raw.get("features", []) or []
    extends = raw.get("extends")
    add_features = raw.get("add_features", []) or []
    exclude_features = raw.get("exclude_features")
    exclude_groups = raw.get("exclude_groups")

    # ── Mutual exclusion: features vs extends ──
    if features and extends is not None:
        raise ValueError(
            f"Ambiguous YAML: both 'features' and 'extends' present in {path}. "
            f"Use one or the other."
        )
    if not features and extends is None:
        # If neither is set and both are empty lists (empty YAML)
        raise ValueError(
            f"YAML has neither 'features' nor 'extends': {path}"
        )

    # ── Duplicate check within file ──
    if features:
        _check_duplicates(features, f"features in {path}")
    if add_features:
        _check_duplicates(add_features, f"add_features in {path}")

    return FeatureSetSpec(
        feature_set_id=feature_set_id,
        path=str(path),
        features=tuple(features or []),
        extends=extends,
        add_features=tuple(add_features or []),
        description=description,
    )


def _check_prohibited(raw: dict, path: Path) -> None:
    """Fail fast if prohibited fields are present."""
    if "exclude_features" in raw:
        raise ValueError(
            f"Unsupported field 'exclude_features' in {path}. "
            f"Only additive YAML is supported (extends + add_features)."
        )
    if "exclude_groups" in raw:
        raise ValueError(
            f"Unsupported field 'exclude_groups' in {path}. "
            f"Only additive YAML is supported (extends + add_features)."
        )
    forbidden = {"exclude_features", "exclude_groups"}
    extra = set(raw) & forbidden
    if extra:
        raise ValueError(f"Unsupported fields {extra} in {path}")


def _check_duplicates(names: list[str], label: str) -> None:
    """Fail fast on duplicates within a list."""
    seen: set[str] = set()
    for n in names:
        if n in seen:
            raise ValueError(f"Duplicate feature '{n}' in {label}")
        seen.add(n)
