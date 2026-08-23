from __future__ import annotations

import json

import pandas as pd
import pytest

from qsys.research.rolling_window import RollingWindow
from qsys.research.window_checkpoint import WindowPredictionCheckpointStore


def _window() -> RollingWindow:
    return RollingWindow(
        window_id="w0000",
        train_start="2020-01-02",
        train_end="2020-12-31",
        predict_start="2021-01-04",
        predict_end="2021-01-29",
    )


def _predictions() -> pd.DataFrame:
    return pd.DataFrame({
        "trade_date": ["2021-01-04", "2021-01-04"],
        "data_date": ["2020-12-31", "2020-12-31"],
        "instrument": ["000001.SZ", "000002.SZ"],
        "score": [0.25, -0.25],
    })


def _store(tmp_path, **identity) -> WindowPredictionCheckpointStore:
    return WindowPredictionCheckpointStore(
        tmp_path / "checkpoints",
        {"experiment_id": "exp", "source_manifest_hash": "source-v1", **identity},
    )


def test_checkpoint_round_trip_and_stable_set_hash(tmp_path):
    store = _store(tmp_path)
    first = store.save(_window(), _predictions())
    second = store.validate(_window())
    assert second == first
    pd.testing.assert_frame_equal(store.load(_window()), _predictions())
    assert store.checkpoint_set_sha256([first]) == store.checkpoint_set_sha256([second])


def test_checkpoint_fails_closed_on_parquet_tamper(tmp_path):
    store = _store(tmp_path)
    ref = store.save(_window(), _predictions())
    with ref.predictions_path.open("ab") as handle:
        handle.write(b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        store.validate(_window())


def test_checkpoint_fails_closed_on_manifest_identity_tamper(tmp_path):
    store = _store(tmp_path)
    ref = store.save(_window(), _predictions())
    manifest = json.loads(ref.manifest_path.read_text(encoding="utf-8"))
    manifest["identity"]["source_manifest_hash"] = "different"
    ref.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="identity mismatch"):
        store.validate(_window())


def test_checkpoint_fails_closed_when_commit_marker_missing(tmp_path):
    store = _store(tmp_path)
    ref = store.save(_window(), _predictions())
    ref.manifest_path.unlink()
    with pytest.raises(ValueError, match="Incomplete"):
        store.validate(_window())


def test_changed_base_identity_uses_distinct_checkpoint(tmp_path):
    first = _store(tmp_path, generator_config={"n_estimators": 100})
    second = _store(tmp_path, generator_config={"n_estimators": 200})
    first_ref = first.save(_window(), _predictions())
    assert second.validate(_window()) is None
    second_ref = second.save(_window(), _predictions())
    assert first_ref.predictions_path != second_ref.predictions_path
