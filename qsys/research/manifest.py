from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
import json
from pathlib import Path
from typing import Any

import yaml

from qsys.research.schemas import (
    FactorBundle,
    FactorDefinition,
    FactorVariant,
    ManifestObject,
    ManifestValidationError,
)


# ── Research Run Manifest ──────────────────────────────────────────────


@dataclass(frozen=True)
class RunManifest:
    """Immutable lineage manifest for a research run.

    Tracks all provenance metadata needed to reproduce a run from stored
    artifacts.  Written as ``manifest.json`` at the run root.
    """

    run_id: str
    run_name: str
    created_at: str  # ISO-8601

    # Lineage
    model_id: str | None = None
    feature_set_id: str | None = None
    label_id: str | None = None

    # Optional metadata
    description: str | None = None
    tags: dict[str, str] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty_string(self.run_id, "run_id")
        _require_non_empty_string(self.run_name, "run_name")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RunManifest:
        return cls(**{k: v for k, v in payload.items() if k in _RUN_MANIFEST_FIELDS})

    @classmethod
    def from_json(cls, text: str) -> RunManifest:
        return cls.from_dict(json.loads(text))

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


_RUN_MANIFEST_FIELDS = frozenset(RunManifest.__dataclass_fields__)


def write_run_manifest(manifest: RunManifest, run_dir: Path) -> Path:
    """Write *manifest* as ``manifest.json`` into *run_dir*.

    Returns the path to the written file.
    """
    path = run_dir / "manifest.json"
    path.write_text(manifest.to_json(), encoding="utf-8")
    return path


def load_run_manifest(run_dir: Path) -> RunManifest:
    """Load ``manifest.json`` from *run_dir*."""
    path = run_dir / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")
    return RunManifest.from_json(path.read_text(encoding="utf-8"))


def _require_non_empty_string(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string, got {value!r}")


@dataclass
class FactorManifestRegistry:
    definitions: dict[str, FactorDefinition] = field(default_factory=dict)
    variants: dict[str, FactorVariant] = field(default_factory=dict)
    bundles: dict[str, FactorBundle] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "definitions": {key: value.to_dict() for key, value in self.definitions.items()},
            "variants": {key: value.to_dict() for key, value in self.variants.items()},
            "bundles": {key: value.to_dict() for key, value in self.bundles.items()},
        }

    def validate_references(self) -> None:
        for variant in self.variants.values():
            if variant.base_factor_id not in self.definitions:
                raise ManifestValidationError(
                    f"Variant '{variant.variant_id}' references unknown base_factor_id '{variant.base_factor_id}'"
                )
        for bundle in self.bundles.values():
            for variant_id in bundle.factor_variant_ids:
                if variant_id not in self.variants:
                    raise ManifestValidationError(
                        f"Bundle '{bundle.bundle_id}' references unknown variant_id '{variant_id}'"
                    )


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DEFINITIONS_DIR = REPO_ROOT / "research" / "factors" / "definitions"
DEFAULT_VARIANTS_DIR = REPO_ROOT / "research" / "factors" / "variants"
DEFAULT_BUNDLES_DIR = REPO_ROOT / "research" / "factors" / "bundles"


def parse_factor_definition(path: str | Path) -> FactorDefinition:
    return FactorDefinition.from_dict(_load_manifest_payload(path))


def parse_factor_variant(path: str | Path) -> FactorVariant:
    return FactorVariant.from_dict(_load_manifest_payload(path))


def parse_factor_bundle(path: str | Path) -> FactorBundle:
    return FactorBundle.from_dict(_load_manifest_payload(path))


def load_factor_definition(path: str | Path) -> FactorDefinition:
    return parse_factor_definition(path)


def load_factor_variant(path: str | Path) -> FactorVariant:
    return parse_factor_variant(path)


def load_factor_bundle(path: str | Path) -> FactorBundle:
    return parse_factor_bundle(path)


def load_factor_registry(
    definitions_dir: str | Path = DEFAULT_DEFINITIONS_DIR,
    variants_dir: str | Path = DEFAULT_VARIANTS_DIR,
    bundles_dir: str | Path = DEFAULT_BUNDLES_DIR,
) -> FactorManifestRegistry:
    registry = FactorManifestRegistry()
    for path in _iter_manifest_files(definitions_dir):
        definition = parse_factor_definition(path)
        _register_unique(registry.definitions, definition.factor_id, definition, path)
    for path in _iter_manifest_files(variants_dir):
        variant = parse_factor_variant(path)
        _register_unique(registry.variants, variant.variant_id, variant, path)
    for path in _iter_manifest_files(bundles_dir):
        bundle = parse_factor_bundle(path)
        _register_unique(registry.bundles, bundle.bundle_id, bundle, path)
    registry.validate_references()
    return registry


def parse_manifest_object(path: str | Path) -> ManifestObject:
    payload = _load_manifest_payload(path)
    if "factor_id" in payload:
        return FactorDefinition.from_dict(payload)
    if "variant_id" in payload:
        return FactorVariant.from_dict(payload)
    if "bundle_id" in payload:
        return FactorBundle.from_dict(payload)
    raise ManifestValidationError(f"Cannot infer manifest object type from {path}")


def _load_manifest_payload(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path)
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest file not found: {manifest_path}")
    raw_text = manifest_path.read_text(encoding="utf-8")
    suffix = manifest_path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(raw_text)
    elif suffix in {".yaml", ".yml"}:
        payload = yaml.safe_load(raw_text)
    else:
        raise ManifestValidationError(f"Unsupported manifest format: {manifest_path}")
    if not isinstance(payload, dict):
        raise ManifestValidationError(f"Manifest must decode to a mapping: {manifest_path}")
    return payload


def _iter_manifest_files(directory: str | Path) -> list[Path]:
    base_dir = Path(directory)
    if not base_dir.exists():
        return []
    return sorted(path for path in base_dir.iterdir() if path.is_file() and path.suffix.lower() in {".yaml", ".yml", ".json"})


def _register_unique(store: dict[str, Any], object_id: str, obj: Any, source_path: Path) -> None:
    if object_id in store:
        raise ManifestValidationError(f"Duplicate id '{object_id}' found in {source_path}")
    store[object_id] = obj
