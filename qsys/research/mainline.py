from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qsys.feature.library import FeatureLibrary


@dataclass(frozen=True)
class MainlineObjectSpec:
    mainline_object_name: str
    bundle_id: str
    legacy_feature_set_alias: str
    model_name: str
    description: str


MAINLINE_OBJECTS: dict[str, MainlineObjectSpec] = {
    "feature_173": MainlineObjectSpec(
        mainline_object_name="feature_173",
        bundle_id="bundle_feature_173",
        legacy_feature_set_alias="extended",
        model_name="qlib_lgbm_extended",
        description="Mainline baseline research object mapped to the historical extended feature set.",
    ),
    "feature_254": MainlineObjectSpec(
        mainline_object_name="feature_254",
        bundle_id="bundle_feature_254",
        legacy_feature_set_alias="semantic_all_features",
        model_name="qlib_lgbm_semantic_all_features",
        description="Mainline expanded research object mapped to the historical semantic_all_features feature set.",
    ),
    "feature_254_absnorm": MainlineObjectSpec(
        mainline_object_name="feature_254_absnorm",
        bundle_id="bundle_feature_254_absnorm",
        legacy_feature_set_alias="semantic_all_features_absnorm",
        model_name="qlib_lgbm_semantic_all_features_absnorm",
        description="Mainline absnorm research object mapped to the historical semantic_all_features_absnorm feature set.",
    ),
    "feature_254_trimmed": MainlineObjectSpec(
        mainline_object_name="feature_254_trimmed",
        bundle_id="bundle_feature_254_trimmed",
        legacy_feature_set_alias="semantic_all_features_trimmed",
        model_name="qlib_lgbm_semantic_all_features_trimmed",
        description="Trimmed expanded research object with the shared high-missing/dead fields removed from feature_254.",
    ),
    "feature_254_absnorm_trimmed": MainlineObjectSpec(
        mainline_object_name="feature_254_absnorm_trimmed",
        bundle_id="bundle_feature_254_absnorm_trimmed",
        legacy_feature_set_alias="semantic_all_features_absnorm_trimmed",
        model_name="qlib_lgbm_semantic_all_features_absnorm_trimmed",
        description="Trimmed absnorm research object with the shared high-missing/dead fields removed from feature_254_absnorm.",
    ),
}

LEGACY_FEATURE_SET_ALIAS_TO_MAINLINE_OBJECT = {
    spec.legacy_feature_set_alias: spec.mainline_object_name
    for spec in MAINLINE_OBJECTS.values()
}

MODEL_NAME_TO_MAINLINE_OBJECT = {
    spec.model_name: spec.mainline_object_name
    for spec in MAINLINE_OBJECTS.values()
}

BUNDLE_ID_TO_MAINLINE_OBJECT = {
    spec.bundle_id: spec.mainline_object_name
    for spec in MAINLINE_OBJECTS.values()
}

MAINLINE_OBJECT_TO_COMPAT_FEATURE_SET = {
    spec.mainline_object_name: spec.legacy_feature_set_alias
    for spec in MAINLINE_OBJECTS.values()
}

MAINLINE_OBJECT_TO_FEATURE_CONFIG_LOADER = {
    "feature_173": FeatureLibrary.get_alpha158_extended_config,
    "feature_254": FeatureLibrary.get_semantic_all_features_config,
    "feature_254_absnorm": FeatureLibrary.get_semantic_all_features_absnorm_config,
    "feature_254_trimmed": FeatureLibrary.get_semantic_all_features_trimmed_config,
    "feature_254_absnorm_trimmed": FeatureLibrary.get_semantic_all_features_absnorm_trimmed_config,
}

MAINLINE_OBJECT_TO_VARIANT_IDS = {
    "feature_173": ["feature_173@raw"],
    "feature_254": ["feature_254@raw"],
    "feature_254_absnorm": ["feature_254_absnorm@raw"],
    "feature_254_trimmed": ["feature_254_trimmed@raw"],
    "feature_254_absnorm_trimmed": ["feature_254_absnorm_trimmed@raw"],
}

MAINLINE_OBJECT_TO_FEATURE_SET_NAME = {
    "feature_173": "extended",
    "feature_254": "semantic_all_features",
    "feature_254_absnorm": "semantic_all_features_absnorm",
    "feature_254_trimmed": "semantic_all_features_trimmed",
    "feature_254_absnorm_trimmed": "semantic_all_features_absnorm_trimmed",
}


def get_mainline_spec_by_bundle_id(bundle_id: str) -> MainlineObjectSpec | None:
    mainline_object_name = BUNDLE_ID_TO_MAINLINE_OBJECT.get(bundle_id)
    return MAINLINE_OBJECTS.get(mainline_object_name or "")


def get_mainline_spec_by_feature_set(feature_set: str) -> MainlineObjectSpec | None:
    mainline_object_name = LEGACY_FEATURE_SET_ALIAS_TO_MAINLINE_OBJECT.get(feature_set)
    return MAINLINE_OBJECTS.get(mainline_object_name or "")


def get_mainline_spec_by_model_name(model_name: str) -> MainlineObjectSpec | None:
    mainline_object_name = MODEL_NAME_TO_MAINLINE_OBJECT.get(model_name)
    return MAINLINE_OBJECTS.get(mainline_object_name or "")


def resolve_mainline_object_name(*, feature_set: str | None = None, bundle_id: str | None = None, model_name: str | None = None) -> str | None:
    if bundle_id and bundle_id in BUNDLE_ID_TO_MAINLINE_OBJECT:
        return BUNDLE_ID_TO_MAINLINE_OBJECT[bundle_id]
    if feature_set and feature_set in LEGACY_FEATURE_SET_ALIAS_TO_MAINLINE_OBJECT:
        return LEGACY_FEATURE_SET_ALIAS_TO_MAINLINE_OBJECT[feature_set]
    if model_name and model_name in MODEL_NAME_TO_MAINLINE_OBJECT:
        return MODEL_NAME_TO_MAINLINE_OBJECT[model_name]
    return None


def resolve_mainline_compat_feature_set(mainline_object_name: str | None) -> str | None:
    if mainline_object_name is None:
        return None
    return MAINLINE_OBJECT_TO_COMPAT_FEATURE_SET.get(mainline_object_name)


def resolve_mainline_feature_config(mainline_object_name: str | None) -> list[str] | None:
    if mainline_object_name is None:
        return None
    loader = MAINLINE_OBJECT_TO_FEATURE_CONFIG_LOADER.get(mainline_object_name)
    if loader is None:
        return None
    return loader()


def mainline_object_summary() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in MAINLINE_OBJECTS.values():
        rows.append({
            "mainline_object_name": spec.mainline_object_name,
            "bundle_id": spec.bundle_id,
            "legacy_feature_set_alias": spec.legacy_feature_set_alias,
            "model_name": spec.model_name,
            "description": spec.description,
        })
    return rows
