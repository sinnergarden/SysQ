from __future__ import annotations

from qsys.feature.selection import DEFAULT_RESEARCH_PRESET


DEFAULT_RESEARCH_FEATURE_FLAGS = {
    "enable_microstructure_features": True,
    "enable_liquidity_features": True,
    "enable_tradability_features": True,
    "enable_relative_strength_features": True,
    "enable_regime_features": False,
    "enable_industry_context_features": False,
    "enable_fundamental_context_features": False,
}

# Legacy alias kept for existing scripts/tests.
RESEARCH_FEATURE_FLAGS = DEFAULT_RESEARCH_FEATURE_FLAGS
DEFAULT_RESEARCH_FEATURE_PRESET = DEFAULT_RESEARCH_PRESET
