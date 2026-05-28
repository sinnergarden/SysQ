from __future__ import annotations

from dataclasses import dataclass, field
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


# ── Generic manifest read/write helpers ──────────────────────────────


def read_manifest(path: Path) -> dict[str, Any]:
    """Load a manifest JSON file and return its content as a dict."""
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_manifest(path: Path, data: dict[str, Any]) -> None:
    """Write *data* as a pretty-printed JSON manifest file.

    The directory containing *path* is created if it does not exist.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")


_GIT_REF: str | None = None


def _get_git_commit() -> str | None:
    global _GIT_REF
    if _GIT_REF is not None:
        return _GIT_REF if _GIT_REF else None

    try:
        import subprocess

        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            _GIT_REF = result.stdout.strip()
            return _GIT_REF
    except Exception:
        pass
    _GIT_REF = ""
    return None


def with_standard_metadata(data: dict[str, Any], *, update: bool = False) -> dict[str, Any]:
    """Return *data* augmented with standard metadata fields.

    Standard fields
    ---------------
    - ``created_at`` / ``updated_at``: ISO-8601 timestamps
    - ``git_commit``: short commit SHA when available

    Parameters
    ----------
    data:
        Manifest payload dict.
    update:
        When ``True``, overwrite existing ``created_at`` (default) or
        ``updated_at`` (when ``created_at`` already exists).  When
        ``False``, only set fields that are absent.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    commit = _get_git_commit()

    if update:
        data.setdefault("created_at", now)
        data["updated_at"] = now
    else:
        data.setdefault("created_at", now)

    if commit and "git_commit" not in data:
        data["git_commit"] = commit

    return data
