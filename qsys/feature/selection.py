from __future__ import annotations

FEATURE_GROUP_ORDER = [
    "daily_price_state",
    "liquidity_capacity",
    "execution_state",
    "industry_context",
    "relative_strength",
    "market_regime",
    "fundamental_context",
]

LEGACY_FLAG_TO_GROUP = {
    "enable_microstructure_features": "daily_price_state",
    "enable_liquidity_features": "liquidity_capacity",
    "enable_tradability_features": "execution_state",
    "enable_relative_strength_features": "relative_strength",
    "enable_industry_context_features": "industry_context",
    "enable_regime_features": "market_regime",
    "enable_fundamental_context_features": "fundamental_context",
}

RESEARCH_FEATURE_PRESETS = {
    "phase1_core": [
        "daily_price_state",
        "liquidity_capacity",
        "execution_state",
        "relative_strength",
    ],
    "phase2_context": [
        "industry_context",
        "market_regime",
    ],
    "phase3_slow_context": [
        "fundamental_context",
    ],
    "research_default": [
        "daily_price_state",
        "liquidity_capacity",
        "execution_state",
        "relative_strength",
        "industry_context",
        "market_regime",
        "fundamental_context",
    ],
    "tabular_default": [
        "daily_price_state",
        "liquidity_capacity",
        "execution_state",
        "relative_strength",
        "industry_context",
        "market_regime",
        "fundamental_context",
    ],
    "sequence_context": [
        "daily_price_state",
        "execution_state",
        "industry_context",
        "market_regime",
        "fundamental_context",
    ],
}

DEFAULT_RESEARCH_PRESET = "phase1_core"


def resolve_feature_groups(
    *,
    groups: list[str] | None = None,
    flags: dict | None = None,
    preset: str = DEFAULT_RESEARCH_PRESET,
) -> list[str]:
    if groups is not None:
        enabled = list(groups)
    else:
        enabled = list(RESEARCH_FEATURE_PRESETS.get(preset, RESEARCH_FEATURE_PRESETS[DEFAULT_RESEARCH_PRESET]))

    for key, value in (flags or {}).items():
        group = LEGACY_FLAG_TO_GROUP.get(key, key if key in FEATURE_GROUP_ORDER else None)
        if group is None:
            continue
        if bool(value):
            if group not in enabled:
                enabled.append(group)
        elif group in enabled:
            enabled.remove(group)

    return [group for group in FEATURE_GROUP_ORDER if group in enabled]
