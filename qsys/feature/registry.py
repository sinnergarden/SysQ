from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import yaml


REGISTRY_PATH = Path(__file__).resolve().parents[2] / "features" / "registry" / "feature_registry.yaml"
FEATURE_SET_PATH = Path(__file__).resolve().parents[2] / "features" / "registry" / "feature_sets.yaml"
NATIVE_SOURCE_TYPES = {"qlib_raw_field", "qlib_alpha158", "qlib_expression"}


@dataclass(frozen=True)
class ResolvedFeatureSelection:
    selection_name: str
    feature_ids: list[str]
    features: list[dict[str, Any]]

    @property
    def feature_names(self) -> list[str]:
        return [feature["name"] for feature in self.features]

    @property
    def qlib_column_names(self) -> list[str]:
        return [str(feature.get("qlib_column_name") or feature["name"]) for feature in self.features]

    @property
    def native_features(self) -> list[dict[str, Any]]:
        return [feature for feature in self.features if feature.get("source_type") in NATIVE_SOURCE_TYPES]

    @property
    def derived_features(self) -> list[dict[str, Any]]:
        return [feature for feature in self.features if feature.get("source_type") not in NATIVE_SOURCE_TYPES]

    @property
    def native_qlib_fields(self) -> list[str]:
        fields = []
        for feature in self.native_features:
            expr = feature.get("provider_expression") or feature.get("qlib_column_name") or feature["name"]
            fields.append(str(expr))
        return fields

    @property
    def native_feature_names(self) -> list[str]:
        return [feature["name"] for feature in self.native_features]

    @property
    def derived_columns(self) -> list[str]:
        return [str(feature.get("qlib_column_name") or feature["name"]) for feature in self.derived_features]

    @property
    def required_groups(self) -> list[str]:
        groups: list[str] = []
        for feature in self.derived_features:
            group = str(feature.get("builder_group") or feature.get("group") or "").strip()
            if group and group not in groups:
                groups.append(group)
        return groups

    def column_name_map(self) -> dict[str, str]:
        return {
            feature["id"]: str(feature.get("qlib_column_name") or feature["name"])
            for feature in self.features
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "selection_name": self.selection_name,
            "feature_ids": list(self.feature_ids),
            "feature_names": self.feature_names,
            "qlib_column_names": self.qlib_column_names,
            "native_qlib_fields": self.native_qlib_fields,
            "derived_columns": self.derived_columns,
            "required_groups": self.required_groups,
        }


def _normalize_path(path: str | Path | None, default_path: Path) -> str:
    resolved = Path(path) if path is not None else default_path
    return str(resolved.resolve())


@lru_cache(maxsize=None)
def _load_yaml(path_str: str) -> dict[str, Any]:
    with open(path_str, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


@lru_cache(maxsize=None)
def _build_feature_indexes(registry_path_str: str) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    registry = _load_yaml(registry_path_str)
    by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    by_column: dict[str, dict[str, Any]] = {}
    for item in registry.get("features", []) or []:
        feature = dict(item)
        if "tabular_fit" not in feature:
            feature["tabular_fit"] = bool(feature.get("tabular_ready", False))
        if "sequence_fit" not in feature:
            feature["sequence_fit"] = bool(feature.get("sequence_ready", False))
        feature_id = str(feature.get("id", "")).strip()
        name = str(feature.get("name", "")).strip()
        column_name = str(feature.get("qlib_column_name") or name).strip()
        if feature_id:
            by_id[feature_id] = feature
        if name:
            by_name[name] = feature
        if column_name:
            by_column[column_name] = feature
    return by_id, by_name, by_column


@lru_cache(maxsize=None)
def _resolve_feature_set_ids(
    feature_set_name: str,
    registry_path_str: str,
    feature_set_path_str: str,
) -> tuple[str, ...]:
    registry = _load_yaml(registry_path_str)
    feature_sets = _load_yaml(feature_set_path_str).get("feature_sets", {}) or {}
    if feature_set_name not in feature_sets:
        raise KeyError(f"Unknown feature set: {feature_set_name}")

    feature_by_id, _, _ = _build_feature_indexes(registry_path_str)
    features = registry.get("features", []) or []

    def ids_for_group(group_name: str) -> list[str]:
        return [str(feature["id"]) for feature in features if feature.get("group") == group_name]

    def dedupe(items: Iterable[str]) -> list[str]:
        ordered: list[str] = []
        for item in items:
            if item not in ordered:
                ordered.append(item)
        return ordered

    def resolve(name: str, stack: tuple[str, ...]) -> list[str]:
        if name in stack:
            cycle = " -> ".join((*stack, name))
            raise ValueError(f"Feature set include cycle detected: {cycle}")
        definition = feature_sets[name] or {}
        resolved_ids: list[str] = []
        for included in definition.get("include_sets", []) or []:
            resolved_ids.extend(resolve(str(included), (*stack, name)))
        for group_name in definition.get("include_groups", []) or []:
            resolved_ids.extend(ids_for_group(str(group_name)))
        for feature_id in definition.get("feature_ids", []) or []:
            feature_id_str = str(feature_id)
            if feature_id_str not in feature_by_id:
                raise KeyError(f"Unknown feature id in feature set {name}: {feature_id_str}")
            resolved_ids.append(feature_id_str)
        return dedupe(resolved_ids)

    return tuple(resolve(feature_set_name, tuple()))


@lru_cache(maxsize=None)
def _resolve_feature_selection_cached(
    selection_name: str,
    feature_ids: tuple[str, ...],
    registry_path_str: str,
) -> ResolvedFeatureSelection:
    by_id, _, _ = _build_feature_indexes(registry_path_str)
    features = [dict(by_id[feature_id]) for feature_id in feature_ids]
    return ResolvedFeatureSelection(
        selection_name=selection_name,
        feature_ids=list(feature_ids),
        features=features,
    )


def load_feature_registry(path: str | Path | None = None) -> dict[str, Any]:
    return _load_yaml(_normalize_path(path, REGISTRY_PATH))


def load_feature_sets(path: str | Path | None = None) -> dict[str, Any]:
    return _load_yaml(_normalize_path(path, FEATURE_SET_PATH))


def get_feature_groups(path: str | Path | None = None) -> dict[str, Any]:
    return load_feature_registry(path).get("feature_groups", {}) or {}


def get_feature_metadata(name_or_id: str, path: str | Path | None = None) -> dict[str, Any] | None:
    registry_path_str = _normalize_path(path, REGISTRY_PATH)
    by_id, by_name, by_column = _build_feature_indexes(registry_path_str)
    query = str(name_or_id).strip()
    return dict(by_id.get(query) or by_name.get(query) or by_column.get(query) or {}) or None


def get_feature_metadata_by_id(feature_id: str, path: str | Path | None = None) -> dict[str, Any] | None:
    return get_feature_metadata(feature_id, path)


def list_features_for_group(group: str, path: str | Path | None = None) -> list[str]:
    registry = load_feature_registry(path)
    return [item["name"] for item in registry.get("features", []) if item.get("group") == group]


def list_feature_ids_for_group(group: str, path: str | Path | None = None) -> list[str]:
    registry = load_feature_registry(path)
    return [item["id"] for item in registry.get("features", []) if item.get("group") == group]


def list_feature_groups(path: str | Path | None = None) -> dict[str, Any]:
    groups = get_feature_groups(path)
    return {
        name: {
            "description": meta.get("description", ""),
            "order": meta.get("order"),
            "features": list_features_for_group(name, path),
        }
        for name, meta in groups.items()
    }


def list_standardization_candidates(path: str | Path | None = None) -> list[str]:
    registry = load_feature_registry(path)
    return [
        item["name"]
        for item in registry.get("features", [])
        if item.get("need_norm") and item.get("norm_method") == "cs_winsorize_zscore_rank"
    ]


def list_feature_sets(path: str | Path | None = None) -> dict[str, Any]:
    return load_feature_sets(path).get("feature_sets", {}) or {}


def resolve_feature_selection(
    *,
    feature_set: str | None = None,
    feature_ids: Iterable[str] | None = None,
    registry_path: str | Path | None = None,
    feature_set_path: str | Path | None = None,
) -> ResolvedFeatureSelection:
    registry_path_str = _normalize_path(registry_path, REGISTRY_PATH)
    feature_set_path_str = _normalize_path(feature_set_path, FEATURE_SET_PATH)
    by_id, _, _ = _build_feature_indexes(registry_path_str)

    ordered_ids: list[str] = []
    selection_name = feature_set or "ad_hoc_feature_ids"

    if feature_set:
        ordered_ids.extend(_resolve_feature_set_ids(feature_set, registry_path_str, feature_set_path_str))

    if feature_ids is not None:
        for feature_id in feature_ids:
            feature_id_str = str(feature_id)
            if feature_id_str not in by_id:
                raise KeyError(f"Unknown feature id: {feature_id_str}")
            ordered_ids.append(feature_id_str)

    if not ordered_ids:
        raise ValueError("resolve_feature_selection requires feature_set or feature_ids")

    deduped_ids: list[str] = []
    for feature_id in ordered_ids:
        if feature_id not in deduped_ids:
            deduped_ids.append(feature_id)

    return _resolve_feature_selection_cached(selection_name, tuple(deduped_ids), registry_path_str)


def resolve_feature_set_groups(
    feature_set: str,
    *,
    registry_path: str | Path | None = None,
    feature_set_path: str | Path | None = None,
) -> list[str]:
    return resolve_feature_selection(
        feature_set=feature_set,
        registry_path=registry_path,
        feature_set_path=feature_set_path,
    ).required_groups


def resolve_feature_columns(
    *,
    feature_set: str | None = None,
    feature_ids: Iterable[str] | None = None,
    registry_path: str | Path | None = None,
    feature_set_path: str | Path | None = None,
) -> dict[str, Any]:
    selection = resolve_feature_selection(
        feature_set=feature_set,
        feature_ids=feature_ids,
        registry_path=registry_path,
        feature_set_path=feature_set_path,
    )
    return selection.to_dict()
