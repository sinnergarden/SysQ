from __future__ import annotations

from qsys.feature.definitions.price_state import build_daily_price_state_features


def build_microstructure_features(df):
    return build_daily_price_state_features(df)
