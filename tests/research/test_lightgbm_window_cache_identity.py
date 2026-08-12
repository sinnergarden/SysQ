from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from qsys.research.generators.lightgbm_single_label import (
    LightGBMSingleLabelGenerator,
)


def _generator(tmp_path: Path, **kwargs) -> LightGBMSingleLabelGenerator:
    source_hash = kwargs.pop("source_manifest_hash", "source_v1")
    return LightGBMSingleLabelGenerator(
        feature_list_id="features_v1",
        use_feature_cache=True,
        feature_cache_root=str(tmp_path),
        source_manifest_hash=source_hash,
        **kwargs,
    )


def test_cache_key_binds_source_universe_and_ordered_features(tmp_path: Path) -> None:
    base = _generator(tmp_path)
    key = base._window_key("2020-01-01", "2021-01-01", ["f1", "f2"])
    assert key != _generator(
        tmp_path, source_manifest_hash="source_v2"
    )._window_key("2020-01-01", "2021-01-01", ["f1", "f2"])
    assert key != _generator(
        tmp_path, universe="csi800"
    )._window_key("2020-01-01", "2021-01-01", ["f1", "f2"])
    assert key != base._window_key("2020-01-01", "2021-01-01", ["f2", "f1"])


def test_cache_requires_explicit_source_identity(tmp_path: Path) -> None:
    generator = LightGBMSingleLabelGenerator(
        feature_list_id="features_v1",
        use_feature_cache=True,
        feature_cache_root=str(tmp_path),
        source_manifest_hash="",
    )
    with patch(
        "qsys.feature.registry.FeatureListRegistry.load", return_value=["f1"]
    ), pytest.raises(ValueError, match="source_manifest_hash"):
        generator._load_data("2020-01-01", "2021-01-01")


def test_cache_requires_explicit_feature_list(tmp_path: Path) -> None:
    generator = LightGBMSingleLabelGenerator(
        feature_list_id=None,
        use_feature_cache=True,
        feature_cache_root=str(tmp_path),
        source_manifest_hash="source_v1",
    )
    with patch(
        "qsys.feature.registry.get_feature_fields", return_value=["$close"]
    ), patch(
        "qsys.strategy.alpha_v1.spec.get_clean_features", return_value=["f1"]
    ), pytest.raises(ValueError, match="explicit feature_list_id"):
        generator._load_data("2020-01-01", "2021-01-01")
