from __future__ import annotations

from qsys.feature.definitions.liquidity import build_liquidity_capacity_features


def build_liquidity_features(df):
    return build_liquidity_capacity_features(df)
