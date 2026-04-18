from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any, TypeVar


class ManifestValidationError(ValueError):
    """Raised when a factor-governance manifest is malformed."""


T = TypeVar("T")


@dataclass(frozen=True)
class FactorDefinition:
    factor_id: str
    name: str
    family: str
    kind: str
    dependencies: list[str]
    builder: str
    timing_semantics: str
    description: str

    def __post_init__(self) -> None:
        _require_non_empty_string(self.factor_id, "factor_id")
        _require_non_empty_string(self.name, "name")
        _require_non_empty_string(self.family, "family")
        _require_non_empty_string(self.kind, "kind")
        _require_string_list(self.dependencies, "dependencies")
        _require_non_empty_string(self.builder, "builder")
        _require_non_empty_string(self.timing_semantics, "timing_semantics")
        _require_non_empty_string(self.description, "description")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FactorDefinition":
        return cls(
            factor_id=_required(payload, "factor_id"),
            name=_required(payload, "name"),
            family=_required(payload, "family"),
            kind=_required(payload, "kind"),
            dependencies=_required(payload, "dependencies"),
            builder=_required(payload, "builder"),
            timing_semantics=_required(payload, "timing_semantics"),
            description=_required(payload, "description"),
        )

    @classmethod
    def from_json(cls, text: str) -> "FactorDefinition":
        return cls.from_dict(json.loads(text))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class FactorVariant:
    variant_id: str
    base_factor_id: str
    transform_chain: list[str]
    status: str
    notes: str

    def __post_init__(self) -> None:
        _require_non_empty_string(self.variant_id, "variant_id")
        _require_non_empty_string(self.base_factor_id, "base_factor_id")
        _require_string_list(self.transform_chain, "transform_chain")
        _require_non_empty_string(self.status, "status")
        _require_non_empty_string(self.notes, "notes")
        if "@" not in self.variant_id:
            raise ManifestValidationError("variant_id must contain '@', e.g. factor@raw")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FactorVariant":
        return cls(
            variant_id=_required(payload, "variant_id"),
            base_factor_id=_required(payload, "base_factor_id"),
            transform_chain=_required(payload, "transform_chain"),
            status=_required(payload, "status"),
            notes=_required(payload, "notes"),
        )

    @classmethod
    def from_json(cls, text: str) -> "FactorVariant":
        return cls.from_dict(json.loads(text))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class FactorBundle:
    bundle_id: str
    purpose: str
    factor_variants: list[str]
    intended_usage: str
    change_log: list[str]

    def __post_init__(self) -> None:
        _require_non_empty_string(self.bundle_id, "bundle_id")
        _require_non_empty_string(self.purpose, "purpose")
        _require_string_list(self.factor_variants, "factor_variants")
        _require_non_empty_string(self.intended_usage, "intended_usage")
        _require_string_list(self.change_log, "change_log")

    @property
    def factor_variant_ids(self) -> list[str]:
        return list(self.factor_variants)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FactorBundle":
        return cls(
            bundle_id=_required(payload, "bundle_id"),
            purpose=_required(payload, "purpose"),
            factor_variants=_required(payload, "factor_variants"),
            intended_usage=_required(payload, "intended_usage"),
            change_log=_required(payload, "change_log"),
        )

    @classmethod
    def from_json(cls, text: str) -> "FactorBundle":
        return cls.from_dict(json.loads(text))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


ManifestObject = FactorDefinition | FactorVariant | FactorBundle


def _required(payload: dict[str, Any], field_name: str) -> Any:
    if field_name not in payload:
        raise ManifestValidationError(f"Missing required field: {field_name}")
    return payload[field_name]


def _require_non_empty_string(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ManifestValidationError(f"{field_name} must be a non-empty string")


def _require_string_list(value: Any, field_name: str) -> None:
    if not isinstance(value, list) or not value:
        raise ManifestValidationError(f"{field_name} must be a non-empty list")
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ManifestValidationError(f"{field_name}[{index}] must be a non-empty string")
