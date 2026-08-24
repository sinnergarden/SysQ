from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
import hashlib

import pytest

from qsys.research.generators.lightgbm_single_label import (
    LightGBMSingleLabelGenerator,
)


_FRESHNESS_CONTRACT = {
    "source": "test",
    "availability_rule": "announcement_date_asof",
    "min_coverage": 0.95,
    "features": {
        "holder_num_stale_days": {"max_median_days": 200, "max_row_days": 365},
        "top10_holder_stale_days": {"max_median_days": 250, "max_row_days": 365},
    },
}


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


def test_cache_key_binds_opt_in_shareholder_freshness_contract(tmp_path: Path) -> None:
    base = _generator(tmp_path)
    gated = _generator(
        tmp_path, shareholder_freshness_contract=_FRESHNESS_CONTRACT
    )
    assert base._window_key("2020-01-01", "2021-01-01", ["f1"]) != gated._window_key(
        "2020-01-01", "2021-01-01", ["f1"]
    )


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


def test_shareholder_snapshot_requires_both_files_and_hashes(tmp_path: Path) -> None:
    holder = tmp_path / "holder.parquet"
    holder.write_bytes(b"holder")
    with pytest.raises(ValueError, match="requires path and SHA-256 for both"):
        LightGBMSingleLabelGenerator(
            shareholder_holder_path=str(holder),
            shareholder_holder_sha256=hashlib.sha256(b"holder").hexdigest(),
        )


def test_shareholder_snapshot_hash_is_verified_and_enters_identity(
    tmp_path: Path,
) -> None:
    holder = tmp_path / "holder.parquet"
    top10 = tmp_path / "top10.parquet"
    holder.write_bytes(b"holder-v1")
    top10.write_bytes(b"top10-v1")
    holder_hash = hashlib.sha256(holder.read_bytes()).hexdigest()
    top10_hash = hashlib.sha256(top10.read_bytes()).hexdigest()

    generator = LightGBMSingleLabelGenerator(
        shareholder_holder_path=str(holder),
        shareholder_holder_sha256=holder_hash,
        shareholder_top10_path=str(top10),
        shareholder_top10_sha256=top10_hash,
    )

    assert generator.checkpoint_input_artifacts == [
        {"name": "holder_num", "sha256": holder_hash},
        {"name": "top10_holder_ratio", "sha256": top10_hash},
    ]
    assert generator.feature_source_lineage["holder_num"]["path"] == str(
        holder.absolute()
    )

    with pytest.raises(ValueError, match="hash mismatch"):
        LightGBMSingleLabelGenerator(
            shareholder_holder_path=str(holder),
            shareholder_holder_sha256="0" * 64,
            shareholder_top10_path=str(top10),
            shareholder_top10_sha256=top10_hash,
        )
