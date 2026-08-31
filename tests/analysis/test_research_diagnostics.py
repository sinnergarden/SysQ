from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from qsys.analysis.research_diagnostics import ResearchDiagnostics


def _raw_frame() -> pd.DataFrame:
    index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2020-01-01"), "AAA"),
            (pd.Timestamp("2020-01-03"), "AAA"),
            (pd.Timestamp("2020-01-01"), "BBB"),
            (pd.Timestamp("2020-01-03"), "BBB"),
        ],
        names=["datetime", "instrument"],
    )
    return pd.DataFrame({"f1": [1.0, 2.0, 3.0, 4.0], "$factor": 1.0}, index=index)


def _fake_pit_store(tmp_path: Path) -> SimpleNamespace:
    artifact_dir = tmp_path / "universe"
    artifact_dir.mkdir()
    (artifact_dir / "manifest.json").write_text("{}", encoding="utf-8")
    return SimpleNamespace(
        instruments=["AAA", "BBB"],
        spans=pd.DataFrame({
            "instrument": ["AAA", "BBB"],
            "effective_from": ["20200101", "20200102"],
            "effective_to": ["20200102", "20200103"],
        }),
        artifact_dir=artifact_dir,
        provenance=SimpleNamespace(
            membership_sha256="a" * 64,
            raw_source_hash="b" * 64,
        ),
    )


def test_diagnostics_applies_daily_pit_membership_and_binds_lineage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pit_store = _fake_pit_store(tmp_path)
    monkeypatch.setattr(
        "qsys.research.pit_universe.PitUniverseStore",
        lambda _artifact: pit_store,
    )
    config = {
        "diagnostics_id": "tiny",
        "experiment_id": "tiny-exp",
        "focus_features": ["f1"],
        "universe": "csi1800_pit_union",
        "pit_universe_artifact": "csi1800_pit_v2",
        "pit_filter_mode": "member_as_of",
        "require_pit_universe": True,
        "source_manifest_hash": "source-v1",
        "start_date": "2020-01-01",
        "end_date": "2020-01-03",
        "diagnostics": {
            "coverage": True,
            "feature_ic": False,
            "bucket_return": False,
            "correlation": False,
            "exposure_breakdown": False,
        },
        "top_candidates": {"enabled": False},
        "labels": [
            {
                "label_id": "open_5",
                "return_type": "open_to_open",
            }
        ],
        "require_executable_labels": True,
    }
    diagnostics = ResearchDiagnostics(config, root=tmp_path / "research")
    diagnostics._adapter = MagicMock()
    diagnostics._adapter.get_features.return_value = _raw_frame()
    label_manifest = tmp_path / "label-manifest.json"
    label_manifest.write_text("{}", encoding="utf-8")
    label_data = tmp_path / "labels.parquet"
    label_data.write_bytes(b"fixture")
    diagnostics._label_store = MagicMock()
    diagnostics._label_store.load_labels.return_value = pd.DataFrame(
        {
            "trade_date": ["2020-01-01", "2020-01-03"],
            "instrument": ["AAA", "BBB"],
            "label_value": [0.1, 0.2],
            "is_valid": [True, False],
            "entry_eligible": [True, False],
            "is_mature": [True, True],
            "return_type": ["open_to_open", "open_to_open"],
            "exit_execution_status": ["target_suspended", "executable"],
        }
    )
    diagnostics._label_store.paths.label_manifest.return_value = label_manifest
    diagnostics._label_store._resolve_data_path.return_value = label_data

    result = diagnostics.run()

    keys = set(zip(
        diagnostics._feature_frame["trade_date"],
        diagnostics._feature_frame["instrument"],
    ))
    assert keys == {("2020-01-01", "AAA"), ("2020-01-03", "BBB")}
    call = diagnostics._adapter.get_features.call_args
    assert call.args[0] == ["AAA", "BBB"]
    pd.testing.assert_frame_equal(
        call.kwargs["semantic_pit_membership_spans"], pit_store.spans
    )
    manifest = json.loads(Path(result["manifest"]).read_text())
    assert manifest["lineage"]["pit_universe"]["membership_sha256"] == "a" * 64
    assert manifest["lineage"]["industry_taxonomy"]["contract"] == (
        "historical_daily_industry_numeric_map_v1"
    )
    assert len(manifest["diagnostics_identity_sha256"]) == 64
    assert "manifest.json" not in manifest["outputs"]
    daily = pd.read_csv(Path(result["output_dir"]) / "coverage_daily.csv")
    assert set(daily["trade_date"]) == {"2020-01-01", "2020-01-03"}
    assert set(daily["eligible_count"]) == {1}
    consumed = diagnostics._label_data["open_5"]
    assert list(consumed["instrument"]) == ["AAA"]
    assert list(consumed["exit_execution_status"]) == ["target_suspended"]
    assert manifest["lineage"]["labels"]["open_5"]["raw_row_count"] == 2
    assert manifest["lineage"]["labels"]["open_5"]["consumed_row_count"] == 1

    repeated = diagnostics.run()
    assert repeated["diagnostics_identity_sha256"] == result[
        "diagnostics_identity_sha256"
    ]


def test_formal_diagnostics_requires_pit_artifact(tmp_path: Path) -> None:
    diagnostics = ResearchDiagnostics(
        {
            "experiment_id": "missing-pit",
            "focus_features": ["f1"],
            "require_pit_universe": True,
        },
        root=tmp_path,
    )
    diagnostics._adapter = MagicMock()
    with pytest.raises(ValueError, match="require pit_universe_artifact"):
        diagnostics._load_data()


def test_empty_correlation_result_keeps_stable_schema(tmp_path: Path) -> None:
    diagnostics = ResearchDiagnostics({}, root=tmp_path)
    diagnostics._features = ["f1", "f2"]
    diagnostics._feature_frame = pd.DataFrame(
        {
            "trade_date": ["2024-01-02", "2024-01-02"],
            "f1": [1.0, 2.0],
            "f2": [2.0, 1.0],
        }
    )
    diagnostics._cfg["correlation_threshold"] = 1.1
    result = diagnostics._run_correlation()
    assert result.empty
    assert list(result.columns) == ["feature_a", "feature_b", "corr"]


def test_diagnostics_loads_only_bound_columns_from_validated_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shard_path = tmp_path / "2024.parquet"
    pd.DataFrame({
        "trade_date": ["2024-01-02", "2024-01-03"],
        "instrument": ["AAA", "AAA"],
        "f1": [1.0, 2.0],
        "$industry": [1.0, 1.0],
        "unused": [9.0, 9.0],
    }).to_parquet(shard_path, index=False)
    data_sha256 = hashlib.sha256(shard_path.read_bytes()).hexdigest()
    identity = {
        "feature_list_id": "tiny_features",
        "feature_cache_list_id": "tiny_superset",
        "pit_universe_artifact": "csi1800_pit_v2",
        "pit_filter_mode": "member_as_of",
        "column_contract": {
            "materialized_features": ["f1", "$industry", "unused"],
            "consumed_features": ["f1"],
        },
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({
        "schema_version": 2,
        "source_manifest_hash": "source-v1",
        "cache_coverage_start": "2024-01-01",
        "cache_coverage_end": "2024-12-31",
        "shards": [{
            "path": str(shard_path),
            "data_sha256": data_sha256,
            "source_coverage_start": "2024-01-01",
            "source_coverage_end": "2024-12-31",
            "identity": identity,
        }],
    }), encoding="utf-8")
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    validation_path = tmp_path / "validation.json"
    validation_path.write_text(json.dumps({
        "status": "pass",
        "manifest_sha256": manifest_sha256,
        "shards": [{"path": str(shard_path), "data_sha256": data_sha256}],
    }), encoding="utf-8")
    validation_sha256 = hashlib.sha256(validation_path.read_bytes()).hexdigest()
    monkeypatch.setattr(
        "qsys.analysis.research_diagnostics.FeatureListRegistry.contract",
        lambda _feature_list_id: {"features": ["f1"]},
    )
    diagnostics = ResearchDiagnostics({
        "pit_universe_artifact": "csi1800_pit_v2",
        "pit_filter_mode": "member_as_of",
        "source_manifest_hash": "source-v1",
    }, root=tmp_path / "research")
    diagnostics._features = ["f1"]
    result = diagnostics._load_feature_cache(
        {
            "manifest_path": str(manifest_path),
            "manifest_sha256": manifest_sha256,
            "validation_path": str(validation_path),
            "validation_sha256": validation_sha256,
        },
        requested_features=["f1", "$industry"],
        start="2024-01-02",
        end="2024-01-03",
    )
    assert result.columns.tolist() == [
        "trade_date", "instrument", "f1", "$industry",
    ]
    assert diagnostics._lineage["feature_cache"]["consumed_feature_count"] == 1
    assert diagnostics._lineage["feature_cache"]["rows_before_daily_pit_filter"] == 2


def test_feature_dates_align_to_strictly_later_execution_session(
    tmp_path: Path,
) -> None:
    calendar_path = tmp_path / "day.txt"
    calendar_path.write_text(
        "2024-01-02\n2024-01-03\n2024-01-04\n",
        encoding="utf-8",
    )
    calendar_sha256 = hashlib.sha256(calendar_path.read_bytes()).hexdigest()
    diagnostics = ResearchDiagnostics({}, root=tmp_path / "research")
    result = diagnostics._align_feature_dates(
        pd.DataFrame({
            "trade_date": ["2024-01-02", "2024-01-03"],
            "instrument": ["AAA", "AAA"],
            "f1": [1.0, 2.0],
        }),
        {
            "contract": "previous_open_session_to_execution_date_v1",
            "calendar_path": str(calendar_path),
            "calendar_sha256": calendar_sha256,
        },
        data_root=tmp_path,
        execution_start="2024-01-02",
        execution_end="2024-01-04",
    )
    assert result[["data_date", "trade_date"]].values.tolist() == [
        ["2024-01-02", "2024-01-03"],
        ["2024-01-03", "2024-01-04"],
    ]
    assert diagnostics._lineage["feature_label_alignment"][
        "strict_prior_date_check"
    ] == "pass"
