from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from scripts.ops import audit_inference_coverage


def _feature_frame(instruments: list[str], columns: list[str]) -> pd.DataFrame:
    index = pd.MultiIndex.from_tuples(
        [(instrument, pd.Timestamp("2026-04-17")) for instrument in instruments],
        names=["instrument", "datetime"],
    )
    data = {column: list(range(1, len(instruments) + 1)) for column in columns}
    return pd.DataFrame(data, index=index)


def test_audit_artifact_contract(tmp_path: Path) -> None:
    model_dir = tmp_path / "data" / "models" / "qlib_lgbm_extended"
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "meta.yaml").write_text("name: qlib_lgbm_extended\nfeature_config:\n  - $close\n", encoding="utf-8")
    feature_frame = _feature_frame(["000001.SZ", "000002.SZ", "000063.SZ"], ["$close"])

    class _Adapter:
        def __init__(self):
            self.qlib_dir = tmp_path / "data" / "qlib_bin"

        def init_qlib(self):
            return None

        def get_features(self, *args, **kwargs):
            return feature_frame

    inst_dir = tmp_path / "data" / "qlib_bin" / "instruments"
    inst_dir.mkdir(parents=True, exist_ok=True)
    (inst_dir / "csi300.txt").write_text(
        "000001.SZ\t2010-01-04\t2026-04-17\n000002.SZ\t2010-01-04\t2026-04-17\n000063.SZ\t2010-01-04\t2026-04-17\n",
        encoding="utf-8",
    )
    (inst_dir / "all.txt").write_text((inst_dir / "csi300.txt").read_text(encoding="utf-8"), encoding="utf-8")

    fake_scores = pd.Series([0.1, 0.2, 0.3], index=feature_frame.index)
    with patch("scripts.ops.audit_inference_coverage.resolve_daily_trade_date", return_value={"requested_date": "2026-04-26", "resolved_trade_date": "2026-04-17"}), \
         patch("scripts.ops.audit_inference_coverage.read_latest_shadow_model", return_value={"model_path": str(model_dir), "mainline_object_name": "feature_173", "bundle_id": "bundle_feature_173", "model_name": "qlib_lgbm_extended", "train_run_id": "shadow_retrain_x", "status": "success"}), \
         patch("scripts.ops.audit_inference_coverage.resolve_mainline_feature_config", return_value=["$close"]), \
         patch("scripts.ops.audit_inference_coverage.QlibAdapter", _Adapter), \
         patch("scripts.ops.audit_inference_coverage.build_model_input_frame", return_value=feature_frame.copy()), \
         patch("scripts.ops.audit_inference_coverage.SignalGenerator") as generator_cls, \
         patch("sys.argv", ["audit_inference_coverage.py", "--base-dir", str(tmp_path), "--mainline", "feature_173"]):
        generator_cls.return_value.predict.return_value = fake_scores
        audit_inference_coverage.main()

    out = tmp_path / "experiments" / "ops_diagnostics" / "inference_coverage_audit"
    assert (out / "inference_coverage_summary.json").exists()
    assert (out / "dropped_instruments.csv").exists()
    assert (out / "feature_frame_profile.json").exists()
    payload = json.loads((out / "inference_coverage_summary.json").read_text(encoding="utf-8"))
    assert payload["prediction_count"] == 3
    assert payload["feature_frame_row_count"] == 3


def test_dropped_instruments_csv_has_expected_fields(tmp_path: Path) -> None:
    path = tmp_path / "dropped_instruments.csv"
    rows = [{"instrument": "000100.SZ", "stage": "feature_frame", "reason": "universe_inactive_or_missing_feature_row"}]
    audit_inference_coverage._write_csv(path, rows, ["instrument", "stage", "reason"])
    text = path.read_text(encoding="utf-8")
    assert "instrument,stage,reason" in text
    assert "000100.SZ" in text
