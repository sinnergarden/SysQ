from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
import hashlib
import json

import pytest

from qsys.research.generators.lightgbm_single_label import (
    LightGBMSingleLabelGenerator,
)
from qsys.data._merge_helpers import (
    FINANCIAL_AVAILABILITY_CONTRACT,
    FINANCIAL_AVAILABILITY_RULE,
)
from qsys.data.income_sidecar import (
    INCOME_SIDECAR_SCHEMA,
    INCOME_SIDECAR_TRANSFORM,
)
from qsys.data.source_audit import stable_scope_hash


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
        "income_sidecar_path": str(artifact),
        "income_sidecar_sha256": artifact_sha,
        "income_sidecar_manifest_path": str(manifest_path),
        "income_sidecar_manifest_sha256": hashlib.sha256(
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
