from __future__ import annotations

from qsys.feature.definitions.execution_state import build_execution_state_features


def build_tradability_features(df):
    return build_execution_state_features(df)
