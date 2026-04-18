from __future__ import annotations

import json
from pathlib import Path
import textwrap

import pytest

from qsys.research.manifest import (
    DEFAULT_BUNDLES_DIR,
    DEFAULT_DEFINITIONS_DIR,
    DEFAULT_VARIANTS_DIR,
    load_factor_bundle,
    load_factor_definition,
    load_factor_registry,
    load_factor_variant,
    parse_factor_bundle,
    parse_factor_definition,
    parse_factor_variant,
)
from qsys.research.schemas import FactorBundle, FactorDefinition, FactorVariant, ManifestValidationError


def _write_yaml(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")
    return path


def test_schema_parse_demo_manifests() -> None:
    definition = parse_factor_definition(DEFAULT_DEFINITIONS_DIR / "f_demo.yaml")
    variant = parse_factor_variant(DEFAULT_VARIANTS_DIR / "f_demo@raw.yaml")
    bundle = parse_factor_bundle(DEFAULT_BUNDLES_DIR / "bundle_demo.yaml")

    assert isinstance(definition, FactorDefinition)
    assert definition.factor_id == "f_demo"
    assert definition.dependencies == ["market_data.close"]

    assert isinstance(variant, FactorVariant)
    assert variant.variant_id == "f_demo@raw"
    assert variant.base_factor_id == "f_demo"
    assert variant.transform_chain == ["identity"]

    assert isinstance(bundle, FactorBundle)
    assert bundle.bundle_id == "bundle_demo"
    assert bundle.factor_variant_ids == ["f_demo@raw", "f_demo@absnorm"]


def test_registry_loads_demo_manifests_and_validates_links() -> None:
    registry = load_factor_registry()

    assert "f_demo" in registry.definitions
    assert "f_demo@raw" in registry.variants
    assert "bundle_demo" in registry.bundles
    assert registry.bundles["bundle_demo"].factor_variant_ids == ["f_demo@raw", "f_demo@absnorm"]


def test_required_field_validation_errors(tmp_path: Path) -> None:
    missing_factor_id = _write_yaml(
        tmp_path / "definitions" / "missing_factor_id.yaml",
        """
        name: Demo Factor
        family: demo
        kind: cross_sectional
        dependencies:
          - market_data.close
        builder: demo_builder_v1
        timing_semantics: t_close_for_t_plus_1
        description: Missing factor_id.
        """,
    )
    missing_variant_id = _write_yaml(
        tmp_path / "variants" / "missing_variant_id.yaml",
        """
        base_factor_id: f_demo
        transform_chain:
          - identity
        status: draft
        notes: Missing variant_id.
        """,
    )
    missing_bundle_id = _write_yaml(
        tmp_path / "bundles" / "missing_bundle_id.yaml",
        """
        purpose: demo
        factor_variants:
          - f_demo@raw
        intended_usage: demo
        change_log:
          - init
        """,
    )
    missing_base_factor_id = _write_yaml(
        tmp_path / "variants" / "missing_base_factor_id.yaml",
        """
        variant_id: f_demo@raw
        transform_chain:
          - identity
        status: draft
        notes: Missing base_factor_id.
        """,
    )
    missing_factor_variants = _write_yaml(
        tmp_path / "bundles" / "missing_factor_variants.yaml",
        """
        bundle_id: bundle_demo
        purpose: demo
        intended_usage: demo
        change_log:
          - init
        """,
    )

    with pytest.raises(ManifestValidationError, match="factor_id"):
        parse_factor_definition(missing_factor_id)
    with pytest.raises(ManifestValidationError, match="variant_id"):
        parse_factor_variant(missing_variant_id)
    with pytest.raises(ManifestValidationError, match="bundle_id"):
        parse_factor_bundle(missing_bundle_id)
    with pytest.raises(ManifestValidationError, match="base_factor_id"):
        parse_factor_variant(missing_base_factor_id)
    with pytest.raises(ManifestValidationError, match="factor_variants"):
        parse_factor_bundle(missing_factor_variants)


def test_empty_string_and_empty_list_inputs_fail(tmp_path: Path) -> None:
    empty_definition = _write_yaml(
        tmp_path / "definitions" / "empty_definition.yaml",
        """
        factor_id: ""
        name: Demo Factor
        family: demo
        kind: cross_sectional
        dependencies: []
        builder: demo_builder_v1
        timing_semantics: t_close_for_t_plus_1
        description: Invalid definition.
        """,
    )
    empty_variant = _write_yaml(
        tmp_path / "variants" / "empty_variant.yaml",
        """
        variant_id: f_demo@raw
        base_factor_id: f_demo
        transform_chain: []
        status: draft
        notes: Invalid variant.
        """,
    )
    empty_bundle = _write_yaml(
        tmp_path / "bundles" / "empty_bundle.yaml",
        """
        bundle_id: bundle_demo
        purpose: demo
        factor_variants: []
        intended_usage: ""
        change_log:
          - init
        """,
    )

    with pytest.raises(ManifestValidationError):
        parse_factor_definition(empty_definition)
    with pytest.raises(ManifestValidationError):
        parse_factor_variant(empty_variant)
    with pytest.raises(ManifestValidationError):
        parse_factor_bundle(empty_bundle)


def test_invalid_reference_validation_errors(tmp_path: Path) -> None:
    definitions_dir = tmp_path / "definitions"
    variants_dir = tmp_path / "variants"
    bundles_dir = tmp_path / "bundles"

    _write_yaml(
        definitions_dir / "f_demo.yaml",
        """
        factor_id: f_demo
        name: Demo Factor
        family: demo
        kind: cross_sectional
        dependencies:
          - market_data.close
        builder: demo_builder_v1
        timing_semantics: t_close_for_t_plus_1
        description: Demo definition.
        """,
    )
    _write_yaml(
        variants_dir / "bad_variant.yaml",
        """
        variant_id: f_demo@raw
        base_factor_id: missing_factor
        transform_chain:
          - identity
        status: draft
        notes: Broken reference.
        """,
    )
    _write_yaml(
        bundles_dir / "bundle_demo.yaml",
        """
        bundle_id: bundle_demo
        purpose: demo
        factor_variants:
          - f_demo@raw
        intended_usage: demo
        change_log:
          - init
        """,
    )

    with pytest.raises(ManifestValidationError, match="unknown base_factor_id"):
        load_factor_registry(definitions_dir, variants_dir, bundles_dir)

    _write_yaml(
        variants_dir / "good_variant.yaml",
        """
        variant_id: f_demo@raw
        base_factor_id: f_demo
        transform_chain:
          - identity
        status: draft
        notes: Fixed factor reference.
        """,
    )
    (variants_dir / "bad_variant.yaml").unlink()
    _write_yaml(
        bundles_dir / "bundle_demo.yaml",
        """
        bundle_id: bundle_demo
        purpose: demo
        factor_variants:
          - missing_variant@raw
        intended_usage: demo
        change_log:
          - init
        """,
    )

    with pytest.raises(ManifestValidationError, match="unknown variant_id"):
        load_factor_registry(definitions_dir, variants_dir, bundles_dir)


def test_duplicate_id_validation_errors(tmp_path: Path) -> None:
    definitions_dir = tmp_path / "definitions"
    variants_dir = tmp_path / "variants"
    bundles_dir = tmp_path / "bundles"

    _write_yaml(
        definitions_dir / "001_f_demo.yaml",
        """
        factor_id: f_demo
        name: Demo Factor A
        family: demo
        kind: cross_sectional
        dependencies:
          - market_data.close
        builder: demo_builder_v1
        timing_semantics: t_close_for_t_plus_1
        description: First definition.
        """,
    )
    _write_yaml(
        definitions_dir / "002_f_demo.yaml",
        """
        factor_id: f_demo
        name: Demo Factor B
        family: demo
        kind: cross_sectional
        dependencies:
          - market_data.volume
        builder: demo_builder_v2
        timing_semantics: t_close_for_t_plus_1
        description: Duplicate definition.
        """,
    )
    with pytest.raises(ManifestValidationError, match="Duplicate id 'f_demo'"):
        load_factor_registry(definitions_dir, variants_dir, bundles_dir)

    (definitions_dir / "002_f_demo.yaml").unlink()
    _write_yaml(
        variants_dir / "001_variant.yaml",
        """
        variant_id: f_demo@raw
        base_factor_id: f_demo
        transform_chain:
          - identity
        status: draft
        notes: First variant.
        """,
    )
    _write_yaml(
        variants_dir / "002_variant.yaml",
        """
        variant_id: f_demo@raw
        base_factor_id: f_demo
        transform_chain:
          - absnorm
        status: draft
        notes: Duplicate variant.
        """,
    )
    with pytest.raises(ManifestValidationError, match="Duplicate id 'f_demo@raw'"):
        load_factor_registry(definitions_dir, variants_dir, bundles_dir)

    (variants_dir / "002_variant.yaml").unlink()
    _write_yaml(
        bundles_dir / "001_bundle.yaml",
        """
        bundle_id: bundle_demo
        purpose: demo
        factor_variants:
          - f_demo@raw
        intended_usage: demo
        change_log:
          - init
        """,
    )
    _write_yaml(
        bundles_dir / "002_bundle.yaml",
        """
        bundle_id: bundle_demo
        purpose: demo duplicate
        factor_variants:
          - f_demo@raw
        intended_usage: demo
        change_log:
          - init
        """,
    )
    with pytest.raises(ManifestValidationError, match="Duplicate id 'bundle_demo'"):
        load_factor_registry(definitions_dir, variants_dir, bundles_dir)


def test_round_trip_serialization_is_stable() -> None:
    definition = load_factor_definition(DEFAULT_DEFINITIONS_DIR / "f_demo.yaml")
    variant = load_factor_variant(DEFAULT_VARIANTS_DIR / "f_demo@absnorm.yaml")
    bundle = load_factor_bundle(DEFAULT_BUNDLES_DIR / "bundle_demo.yaml")

    definition_round_trip = FactorDefinition.from_dict(definition.to_dict())
    variant_round_trip = FactorVariant.from_json(variant.to_json())
    bundle_round_trip = FactorBundle.from_dict(json.loads(bundle.to_json()))

    assert definition_round_trip == definition
    assert variant_round_trip == variant
    assert bundle_round_trip == bundle
    assert bundle_round_trip.factor_variant_ids == ["f_demo@raw", "f_demo@absnorm"]
