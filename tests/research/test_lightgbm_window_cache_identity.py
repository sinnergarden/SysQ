from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import hashlib
import json

import pandas as pd
import pytest

from qsys.research.generators.lightgbm_single_label import (
    LightGBMSingleLabelGenerator,
)
from qsys.data._merge_helpers import (
    FINANCIAL_AVAILABILITY_CONTRACT,
    FINANCIAL_AVAILABILITY_RULE,
    TUSHARE_FINA_INDICATOR_UNIT_CONTRACT,
)
from qsys.data.income_sidecar import (
    INCOME_SOURCE_MODE_AUDITED,
    INCOME_SIDECAR_SCHEMA,
    INCOME_SIDECAR_TRANSFORM,
)
from qsys.data.source_audit import stable_scope_hash
from qsys.ops.shareholder_sync import (
    AUDITED_SNAPSHOT_CONTRACT,
    AUDITED_SNAPSHOT_SCHEMA,
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
        feature_list_id="momentum_price_volume_v1",
        use_feature_cache=True,
        feature_cache_root=str(tmp_path),
        source_manifest_hash=source_hash,
        **kwargs,
    )


def _income_identity(tmp_path: Path, *, payload: bytes = b"income-v1") -> dict[str, str]:
    artifact = tmp_path / "income.parquet"
    artifact.write_bytes(payload)
    artifact_sha = hashlib.sha256(payload).hexdigest()
    symbols = ["000001.SZ"]
    immutable_identity = {
        "schema": INCOME_SIDECAR_SCHEMA,
        "transform_contract": INCOME_SIDECAR_TRANSFORM,
        "financial_availability_contract": FINANCIAL_AVAILABILITY_CONTRACT,
        "financial_availability_rule": FINANCIAL_AVAILABILITY_RULE,
        "source": "tushare",
        "endpoint": "income",
        "source_run_id": "run-income",
        "terminal_receipt_sha256": "d" * 64,
        "scope_key": "csi1800",
        "range_start": "20180101",
        "range_end": "20260821",
        "availability_cutoff": "20260821",
        "required_history_start": "20180313",
        "symbol_count": 1,
        "symbols_sha256": stable_scope_hash(symbols),
        "source_receipts": [],
    }
    identity_bytes = (
        json.dumps(immutable_identity, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n"
    ).encode("utf-8")
    manifest = {
        "schema_version": 1,
        "artifact_type": INCOME_SIDECAR_SCHEMA,
        "artifact_id": hashlib.sha256(identity_bytes).hexdigest(),
        "identity": immutable_identity,
        "artifact": {"path": artifact.name, "sha256": artifact_sha},
        "scope": {
            "scope_key": "csi1800",
            "range_start": "20180101",
            "range_end": "20260821",
            "availability_cutoff": "20260821",
            "required_history_start": "20180313",
            "symbol_count": 1,
            "symbols_sha256": stable_scope_hash(symbols),
            "symbols": symbols,
        },
        "contracts": {
            "transform": INCOME_SIDECAR_TRANSFORM,
            "financial_availability": FINANCIAL_AVAILABILITY_CONTRACT,
            "availability_rule": FINANCIAL_AVAILABILITY_RULE,
        },
        "source_evidence": {
            "run_id": "run-income",
            "terminal_receipt_sha256": "d" * 64,
        },
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return {
        "income_source_mode": INCOME_SOURCE_MODE_AUDITED,
        "income_sidecar_path": str(artifact),
        "income_sidecar_sha256": artifact_sha,
        "income_sidecar_manifest_path": str(manifest_path),
        "income_sidecar_manifest_sha256": hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest(),
        "income_sidecar_required_history_start": "20180313",
    }


def _shareholder_identity(tmp_path: Path) -> dict[str, str]:
    holder = tmp_path / "holder_num.parquet"
    top10 = tmp_path / "top10_holder_ratio.parquet"
    holder.write_bytes(b"holder-v1")
    top10.write_bytes(b"top10-v1")
    holder_sha = hashlib.sha256(holder.read_bytes()).hexdigest()
    top10_sha = hashlib.sha256(top10.read_bytes()).hexdigest()
    symbols = ["000001.SZ"]
    identity = {
        "schema": AUDITED_SNAPSHOT_SCHEMA,
        "contract": AUDITED_SNAPSHOT_CONTRACT,
        "source": "tushare",
        "source_run_id": "run-shareholder",
        "terminal_receipt_sha256": "e" * 64,
        "scope_key": "csi1800",
        "range_start": "20180101",
        "range_end": "20260821",
        "symbol_count": 1,
        "symbols_sha256": stable_scope_hash(symbols),
        "receipt_count": 2,
        "receipts_sha256": "f" * 64,
    }
    identity_bytes = (
        json.dumps(identity, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    manifest = {
        "schema_version": 2,
        "artifact_type": AUDITED_SNAPSHOT_SCHEMA,
        "artifact_id": hashlib.sha256(identity_bytes).hexdigest(),
        "identity": identity,
        "artifacts": {
            "holder_num": {"path": holder.name, "sha256": holder_sha},
            "top10_holder_ratio": {"path": top10.name, "sha256": top10_sha},
        },
        "scope": {
            "scope_key": "csi1800", "range_start": "20180101",
            "range_end": "20260821", "symbol_count": 1,
            "symbols_sha256": stable_scope_hash(symbols), "symbols": symbols,
        },
        "contracts": {"transform": AUDITED_SNAPSHOT_CONTRACT},
        "source_evidence": {
            "run_id": "run-shareholder", "terminal_receipt_sha256": "e" * 64,
        },
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return {
        "shareholder_holder_path": str(holder),
        "shareholder_holder_sha256": holder_sha,
        "shareholder_top10_path": str(top10),
        "shareholder_top10_sha256": top10_sha,
        "shareholder_manifest_path": str(manifest_path),
        "shareholder_manifest_sha256": hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest(),
    }


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


def test_cache_identity_binds_financial_processing_contracts(tmp_path: Path) -> None:
    generator = _generator(tmp_path)
    identity = generator._cache_identity("2020-01-01", "2021-01-01", ["f1"])

    assert identity["schema_version"] == 8
    assert len(identity["builder_code_sha256"]) == 64
    assert identity["builder_code_dependencies"]
    assert identity["feature_list_contract"]["feature_list_id"] == (
        "momentum_price_volume_v1"
    )
    assert identity["column_contract"] == {
        "materialized_features": ["f1"],
        "consumed_features": ["f1"],
    }
    assert identity["feature_history_contract"] == (
        "continuous_listed_history_member_only_cross_section_v1"
    )
    assert identity["canonical_financial_contracts"] == {
        "availability": FINANCIAL_AVAILABILITY_CONTRACT,
        "fina_indicator_units": TUSHARE_FINA_INDICATOR_UNIT_CONTRACT,
    }
    assert "qsys.data._merge_helpers" in generator.checkpoint_code_dependencies
    assert "qsys.feature.groups.relative_strength" in (
        generator.checkpoint_code_dependencies
    )
    assert "qsys.feature.transforms" in generator.checkpoint_code_dependencies
    assert "qsys.research.pit_universe" in generator.checkpoint_code_dependencies


def test_cache_key_uses_materialized_contract_not_consumer_contract(
    tmp_path: Path,
) -> None:
    def contract(feature_list_id: str) -> dict[str, object]:
        return {
            "feature_list_id": feature_list_id,
            "feature_count": 2 if feature_list_id == "shared_frame" else 1,
            "features_sha256": feature_list_id,
            "features": ["f1", "f2"] if feature_list_id == "shared_frame" else ["f1"],
        }

    first = LightGBMSingleLabelGenerator(
        feature_list_id="consumer_one",
        feature_cache_list_id="shared_frame",
        use_feature_cache=True,
        feature_cache_root=str(tmp_path),
        source_manifest_hash="source_v1",
    )
    second = LightGBMSingleLabelGenerator(
        feature_list_id="consumer_two",
        feature_cache_list_id="shared_frame",
        use_feature_cache=True,
        feature_cache_root=str(tmp_path),
        source_manifest_hash="source_v1",
    )
    with patch(
        "qsys.feature.registry.FeatureListRegistry.contract",
        side_effect=contract,
    ):
        first_identity = first._cache_identity(
            "2020-01-01",
            "2020-12-31",
            ["f1", "f2"],
            consumed_features=["f1"],
        )
        second_identity = second._cache_identity(
            "2020-01-01",
            "2020-12-31",
            ["f1", "f2"],
            consumed_features=["f2"],
        )
        assert first._window_key(
            "2020-01-01",
            "2020-12-31",
            ["f1", "f2"],
            consumed_features=["f1"],
        ) == second._window_key(
            "2020-01-01",
            "2020-12-31",
            ["f1", "f2"],
            consumed_features=["f2"],
        )

    assert first_identity["feature_list_id"] == "consumer_one"
    assert second_identity["feature_list_id"] == "consumer_two"
    assert first_identity["column_contract"] == {
        "materialized_features": ["f1", "f2"],
        "consumed_features": ["f1"],
    }
    assert second_identity["column_contract"]["consumed_features"] == ["f2"]


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
        feature_list_id="momentum_price_volume_v1",
        use_feature_cache=True,
        feature_cache_root=str(tmp_path),
        source_manifest_hash="",
    )
    with patch(
        "qsys.feature.registry.FeatureListRegistry.load", return_value=["f1"]
    ), pytest.raises(ValueError, match="source_manifest_hash"):
        generator._load_data("2020-01-01", "2021-01-01")


def test_pit_feature_load_uses_static_union_plus_separate_membership_mask(
    tmp_path: Path,
) -> None:
    generator = _generator(tmp_path, pit_membership=True)
    spans = pd.DataFrame(
        {
            "instrument": ["AAA", "BBB"],
            "effective_from": ["20200101", "20200102"],
            "effective_to": ["20201231", "20201231"],
        }
    )
    generator._pit_store = SimpleNamespace(
        instruments=["AAA", "BBB"],
        spans=spans,
    )
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2020-01-02"), "AAA")],
        names=["datetime", "instrument"],
    )
    raw = pd.DataFrame({"f1": [1.0], "$close": [10.0]}, index=index)

    with patch(
        "qsys.feature.registry.FeatureListRegistry.load", return_value=["f1"]
    ), patch("qsys.data.adapter.QlibAdapter") as adapter_class:
        adapter_class.return_value.get_features.return_value = raw
        loaded, features = generator._load_data("2020-01-01", "2020-01-03")

    call = adapter_class.return_value.get_features.call_args
    assert call.args[0] == ["AAA", "BBB"]
    pd.testing.assert_frame_equal(
        call.kwargs["semantic_pit_membership_spans"], spans
    )
    assert call.kwargs["semantic_pit_filter_mode"] == "member_as_of"
    assert features == ["f1"]
    assert loaded["instrument"].tolist() == ["AAA"]


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


def test_materialize_on_miss_writes_exact_window_cache(tmp_path: Path) -> None:
    generator = _generator(tmp_path, materialize_on_miss=True)
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2020-01-02"), "000001.SZ")],
        names=["datetime", "instrument"],
    )
    raw = pd.DataFrame({"f1": [1.0], "$close": [10.0]}, index=index)

    with patch(
        "qsys.feature.registry.FeatureListRegistry.load", return_value=["f1"]
    ), patch("qsys.data.adapter.QlibAdapter") as adapter_class:
        adapter_class.return_value.get_features.return_value = raw
        generator._load_data("2020-01-01", "2020-01-03")

    assert generator._window_has_cache("2020-01-01", "2020-01-03", ["f1"])


def test_fixed_window_repeat_is_identical_and_hits_materialized_cache(
    tmp_path: Path,
) -> None:
    first_generator = _generator(tmp_path, materialize_on_miss=True)
    second_generator = _generator(tmp_path, materialize_on_miss=True)
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2020-01-02"), "000001.SZ")],
        names=["datetime", "instrument"],
    )
    raw = pd.DataFrame({"f1": [1.0], "$close": [10.0]}, index=index)

    with patch(
        "qsys.feature.registry.FeatureListRegistry.load", return_value=["f1"]
    ), patch("qsys.data.adapter.QlibAdapter") as adapter_class:
        adapter_class.return_value.get_features.return_value = raw
        first, first_features = first_generator._load_data(
            "2020-01-01", "2020-01-03"
        )
        meta_path = first_generator._window_meta_path(
            "2020-01-01", "2020-01-03", ["f1"]
        )
        first_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        second, second_features = second_generator._load_data(
            "2020-01-01", "2020-01-03"
        )
        second_meta = json.loads(meta_path.read_text(encoding="utf-8"))

    assert adapter_class.return_value.get_features.call_count == 1
    assert first_features == second_features == ["f1"]
    pd.testing.assert_frame_equal(
        first[["trade_date", "instrument", "f1"]],
        second[["trade_date", "instrument", "f1"]],
    )
    assert first_meta == second_meta
    assert first_meta["data_sha256"] == second_meta["data_sha256"]


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


def test_audited_shareholder_manifest_enters_lineage_checkpoint_and_cache(
    tmp_path: Path,
) -> None:
    identity = _shareholder_identity(tmp_path)
    generator = _generator(tmp_path / "cache", **identity)
    lineage = generator.feature_source_lineage

    assert lineage["shareholder_sidecar"]["source_run_id"] == "run-shareholder"
    assert lineage["shareholder_sidecar"]["scope_key"] == "csi1800"
    assert generator.checkpoint_input_artifacts == [
        {"name": "holder_num", "sha256": identity["shareholder_holder_sha256"]},
        {
            "name": "shareholder_sidecar",
            "sha256": identity["shareholder_manifest_sha256"],
        },
        {
            "name": "top10_holder_ratio",
            "sha256": identity["shareholder_top10_sha256"],
        },
    ]
    legacy = _generator(
        tmp_path / "legacy",
        **{
            key: value for key, value in identity.items()
            if "manifest" not in key
        },
    )
    assert "shareholder_sidecar" not in legacy.feature_source_lineage
    assert generator._window_key("2020-01-01", "2021-01-01", ["f1"]) != (
        legacy._window_key("2020-01-01", "2021-01-01", ["f1"])
    )

    Path(identity["shareholder_manifest_path"]).write_text("{}")
    with pytest.raises(ValueError, match="manifest hash mismatch"):
        _generator(tmp_path / "tampered", **identity)


def test_legacy_shareholder_manifest_cannot_be_promoted_to_audited_lineage(
    tmp_path: Path,
) -> None:
    identity = _shareholder_identity(tmp_path)
    manifest_path = Path(identity["shareholder_manifest_path"])
    manifest = json.loads(manifest_path.read_text())
    manifest["schema_version"] = 1
    manifest["artifact_type"] = "legacy_shareholder_snapshot_v1"
    manifest_path.write_text(json.dumps(manifest))
    identity["shareholder_manifest_sha256"] = hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()

    with pytest.raises(ValueError, match="manifest contract/identity mismatch"):
        _generator(tmp_path / "legacy-manifest", **identity)


def test_income_sidecar_manifest_identity_enters_lineage_cache_and_checkpoint(
    tmp_path: Path,
) -> None:
    identity = _income_identity(tmp_path)
    generator = _generator(tmp_path / "cache", **identity)
    lineage = generator.feature_source_lineage["income_sidecar"]

    assert lineage["path"] == str(Path(identity["income_sidecar_path"]).absolute())
    assert lineage["manifest_sha256"] == identity[
        "income_sidecar_manifest_sha256"
    ]
    assert lineage["source_run_id"] == "run-income"
    assert lineage["symbol_count"] == 1
    assert lineage["symbols_sha256"]
    assert generator.checkpoint_input_artifacts == [
        {"name": "income_sidecar", "sha256": identity["income_sidecar_sha256"]},
        {
            "name": "income_sidecar_manifest",
            "sha256": identity["income_sidecar_manifest_sha256"],
        },
    ]
    assert "qsys.data.income_sidecar" in generator.checkpoint_code_dependencies
    base = _generator(tmp_path / "base")
    assert generator._window_key("2020-01-01", "2021-01-01", ["f1"]) != (
        base._window_key("2020-01-01", "2021-01-01", ["f1"])
    )

    with patch("qsys.data.adapter.QlibAdapter") as adapter_class:
        generator._ensure_qlib()
    adapter_kwargs = adapter_class.call_args.kwargs
    assert adapter_kwargs["income_sidecar_path"] == identity["income_sidecar_path"]
    assert adapter_kwargs["income_sidecar_sha256"] == identity[
        "income_sidecar_sha256"
    ]
    assert adapter_kwargs["income_sidecar_manifest_path"] == identity[
        "income_sidecar_manifest_path"
    ]
    assert adapter_kwargs["income_sidecar_manifest_sha256"] == identity[
        "income_sidecar_manifest_sha256"
    ]
    assert adapter_kwargs["income_source_mode"] == INCOME_SOURCE_MODE_AUDITED
    assert adapter_kwargs["income_sidecar_required_history_start"] == "20180313"


def test_income_sidecar_requires_complete_identity_and_rejects_tamper(
    tmp_path: Path,
) -> None:
    identity = _income_identity(tmp_path)
    incomplete = dict(identity)
    incomplete["income_sidecar_manifest_sha256"] = ""
    with pytest.raises(ValueError, match="requires artifact/manifest"):
        _generator(tmp_path / "incomplete", **incomplete)

    Path(identity["income_sidecar_path"]).write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="artifact sha256 mismatch"):
        _generator(tmp_path / "tampered", **identity)


def test_matrix_factory_forwards_explicit_income_sidecar_identity(
    tmp_path: Path,
) -> None:
    from qsys.research.matrix_job import _create_generator_from_config

    identity = _income_identity(tmp_path)
    generator = _create_generator_from_config({
        "generator_id": "growth",
        "type": "single_label_lightgbm",
        "params": {
            "label_id": "fwd_ret_20d_xsz_clip3",
            **identity,
        },
    })

    assert generator.income_sidecar_path == identity["income_sidecar_path"]
    assert generator.income_sidecar_sha256 == identity["income_sidecar_sha256"]
    assert generator.income_sidecar_manifest_path == identity[
        "income_sidecar_manifest_path"
    ]
    assert generator.income_sidecar_manifest_sha256 == identity[
        "income_sidecar_manifest_sha256"
    ]


def test_matrix_factory_forwards_shareholder_manifest_identity(
    tmp_path: Path,
) -> None:
    from qsys.research.matrix_job import _create_generator_from_config

    identity = _shareholder_identity(tmp_path)
    generator = _create_generator_from_config({
        "generator_id": "shareholder",
        "type": "single_label_lightgbm",
        "params": {"label_id": "fwd_ret_20d_xsz_clip3", **identity},
    })
    assert generator.shareholder_manifest_path == identity[
        "shareholder_manifest_path"
    ]
    assert generator.feature_source_lineage["shareholder_sidecar"][
        "terminal_receipt_sha256"
    ] == "e" * 64


def test_matrix_factory_forwards_materialize_on_miss(tmp_path: Path) -> None:
    from qsys.research.matrix_job import _create_generator_from_config

    generator = _create_generator_from_config(
        {
            "generator_id": "cached",
            "type": "single_label_lightgbm",
            "params": {"label_id": "fwd_ret_5d_raw"},
        },
        feature_list_id="momentum_price_volume_v1",
        use_feature_cache=True,
        materialize_on_miss=True,
        feature_cache_root=str(tmp_path),
        source_manifest_hash="source-v1",
    )

    assert generator.materialize_on_miss is True


def test_matrix_factory_forwards_materialized_feature_list() -> None:
    from qsys.research.matrix_job import _create_generator_from_config

    generator = _create_generator_from_config({
        "generator_id": "cached",
        "type": "single_label_lightgbm",
        "params": {
            "label_id": "fwd_ret_5d_raw",
            "feature_list_id": "consumer_list",
            "feature_cache_list_id": "shared_frame",
        },
    })

    assert generator.feature_list_id == "consumer_list"
    assert generator.feature_cache_list_id == "shared_frame"
