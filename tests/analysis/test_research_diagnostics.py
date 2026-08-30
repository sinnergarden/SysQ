from __future__ import annotations

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
