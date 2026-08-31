from __future__ import annotations

from pathlib import Path

from qsys.feature.registry import FeatureListRegistry
from qsys.research.matrix_job import (
    RollingResearchConfig,
    _create_generator_from_config,
)


def test_market_core_consumer_is_ordered_subset_of_materialized_frame() -> None:
    materialized = FeatureListRegistry.contract("market_core_superset_v1")
    consumed = FeatureListRegistry.contract("market_core_5d_v1")

    assert materialized["feature_count"] == 40
    assert consumed["feature_count"] == 26
    positions = {
        feature: index for index, feature in enumerate(materialized["features"])
    }
    consumed_positions = [positions[feature] for feature in consumed["features"]]
    assert consumed_positions == sorted(consumed_positions)


def test_market_core_cross_date_price_expressions_are_adjusted() -> None:
    materialized = FeatureListRegistry.load("market_core_superset_v1")
    cross_date_price = [
        expression
        for expression in materialized
        if "$close" in expression
        and any(operator in expression for operator in ("Ref(", "Mean(", "Max(", "Std("))
    ]

    assert cross_date_price
    assert all("$factor" in expression for expression in cross_date_price)


def test_market_core_preheat_config_binds_both_column_contracts() -> None:
    config_path = Path("configs/research/market_core_superset_cache_v1.yaml")
    config = RollingResearchConfig.from_file(config_path)
    generator = _create_generator_from_config(
        config.generators[0],
        feature_list_id=config.feature_list_id,
        use_feature_cache=True,
        feature_cache_root=config.feature_cache_root,
        source_manifest_hash=config.source_manifest_hash,
    )

    assert len(config.source_manifest_hash) == 64
    assert generator.feature_list_id == "market_core_5d_v1"
    assert generator.feature_cache_list_id == "market_core_superset_v1"
    assert generator.pit_filter_mode == "member_as_of"
    assert generator.pit_universe_artifact == "csi1800_pit_v2"
