from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from qsys.feature.registry import FeatureListRegistry
from qsys.research.feature_cache_validation import validate_annual_feature_cache
from qsys.research.generators import lightgbm_single_label as generator_module
from qsys.research.generators.lightgbm_single_label import (
    LightGBMSingleLabelGenerator,
)
from qsys.research.pit_universe import PitUniverseStore
from scripts.research import preheat_feature_cache as preheat_module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_independent_validator_checks_physical_and_declared_columns(
    tmp_path: Path,
) -> None:
    pit_store = PitUniverseStore("csi1800_pit_v2")
    materialized = FeatureListRegistry.load("market_core_superset_v1")
    consumed = FeatureListRegistry.load("market_core_5d_v1")
    frame = pd.DataFrame({
        "trade_date": ["2020-06-01"],
        "instrument": [pit_store.instruments[0]],
        **{feature: [1.0] for feature in materialized},
        "$close": [99.0],
    })
    generator = LightGBMSingleLabelGenerator(
        feature_list_id="market_core_5d_v1",
        feature_cache_list_id="market_core_superset_v1",
        use_feature_cache=True,
        cache_write_scope="annual_shard",
        feature_cache_root=str(tmp_path / "cache"),
        source_manifest_hash="a" * 64,
        universe="csi1800_pit_union",
        pit_filter_mode="member_as_of",
        pit_universe_artifact="csi1800_pit_v2",
    )
    path = generator._write_cache_frame(
        frame,
        "2020-01-01",
        "2020-12-31",
        materialized,
        consumed_features=consumed,
    )
    identity = generator._cache_identity(
        "2020-01-01",
        "2020-12-31",
        materialized,
        consumed_features=consumed,
    )
    config_path = tmp_path / "preheat.yaml"
    config_path.write_text("experiment_id: validation_fixture\n", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest = {
        "schema_version": 2,
        "experiment_id": "validation_fixture",
        "config_path": str(config_path),
        "config_sha256": _sha256(config_path),
        "prediction_start": "2020-06-01",
        "prediction_end": "2020-06-01",
        "cache_coverage_start": "2020-01-01",
        "cache_coverage_end": "2020-12-31",
        "cache_shard_identity_end": "2020-12-31",
        "source_manifest_hash": "a" * 64,
        "preheat_code_sha256": _sha256(Path(preheat_module.__file__)),
        "generator_code_sha256": _sha256(Path(generator_module.__file__)),
        "shards": [{
            "generator_id": "fixture",
            "start": "2020-01-01",
            "end": "2020-12-31",
            "source_coverage_start": "2020-01-01",
            "source_coverage_end": "2020-12-31",
            "path": str(path),
            "rows": 1,
            "data_sha256": _sha256(path),
            "source_manifest_hash": "a" * 64,
            "identity": identity,
        }],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    validation_path = tmp_path / "validation.json"

    result = validate_annual_feature_cache(
        manifest_path,
        project_root=tmp_path,
        preheat_code_path=Path(preheat_module.__file__),
        generator_code_path=Path(generator_module.__file__),
        output_path=validation_path,
    )

    assert result["status"] == "pass"
    assert result["summary"]["stored_column_count"] == 42
    assert result["summary"]["total_rows"] == 1
    assert pd.read_parquet(path).columns.tolist() == [
        "trade_date", "instrument", *materialized
    ]
    assert json.loads(validation_path.read_text())["status"] == "pass"

    # A second consumer may bind a different ordered subset to the same
    # immutable materialized bytes.  The shared sidecar describes the physical
    # artifact; the consumer-specific identity belongs to its manifest.
    alternate_consumed = FeatureListRegistry.load("market_core_10d_v1")
    alternate_generator = LightGBMSingleLabelGenerator(
        feature_list_id="market_core_10d_v1",
        feature_cache_list_id="market_core_superset_v1",
        use_feature_cache=True,
        cache_write_scope="annual_shard",
        feature_cache_root=str(tmp_path / "cache"),
        source_manifest_hash="a" * 64,
        universe="csi1800_pit_union",
        pit_filter_mode="member_as_of",
        pit_universe_artifact="csi1800_pit_v2",
    )
    manifest["shards"][0]["identity"] = alternate_generator._cache_identity(
        "2020-01-01",
        "2020-12-31",
        materialized,
        consumed_features=alternate_consumed,
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    alternate = validate_annual_feature_cache(
        manifest_path,
        project_root=tmp_path,
        preheat_code_path=Path(preheat_module.__file__),
        generator_code_path=Path(generator_module.__file__),
        output_path=tmp_path / "alternate_validation.json",
    )
    assert alternate["status"] == "pass"
