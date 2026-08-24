from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from qsys.evaluation.top_tail import (
    TopTailValidationError,
    _bootstrap_mean,
    _date_metrics,
    _rank_icir,
    evaluate_top_tail,
    write_top_tail_artifacts,
    sha256_file,
)


def _write_case(tmp_path: Path, *, candidate_shift: float = 0.0, score_column: str = "score_model_raw") -> dict[str, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    all_dates = pd.bdate_range("2021-01-04", periods=81)
    dates = all_dates[::20]
    instruments = [f"00000{i}.SZ" for i in range(1, 9)]
    rows = []
    for date in all_dates:
        for i, instrument in enumerate(instruments):
            rows.append({"trade_date": date, "data_date": date - pd.Timedelta(days=1), "instrument": instrument, score_column: float(i) + (candidate_shift if i == 7 else 0.0)})
    baseline = pd.DataFrame(rows)
    candidate = baseline.copy()
    candidate[score_column] = candidate[score_column] + candidate["instrument"].eq("000008.SZ") * candidate_shift
    labels = baseline[["trade_date", "instrument"]].copy()
    labels["label_value"] = labels["instrument"].str.split(".").str[0].astype(float) / 1_000_000.0
    windows = pd.DataFrame({"predict_start": dates})
    paths = {}
    for name, frame in (("labels", labels), ("baseline_windows", windows), ("candidate_windows", windows)):
        path = tmp_path / f"{name}.parquet"
        frame.to_parquet(path, index=False)
        paths[name] = path
    for name, frame in (("baseline", baseline), ("candidate", candidate)):
        directory = tmp_path / name
        directory.mkdir()
        path = directory / "predictions.parquet"
        frame.to_parquet(path, index=False)
        manifest = {
            "predictions_sha256": sha256_file(path),
            "row_count": len(frame),
            "signal_id": "test_signal",
            "source_manifest_hash": "a" * 64,
            "train_window_days": 504,
            "transform_id": "raw",
            "feature_visibility_contract": "actual_feature_date_strictly_before_trade_date_v1",
            "prediction_start": "2021-01-04",
            "prediction_end": "2021-04-26",
            "window_count": 5,
        }
        (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        paths[name] = path
    manifest = tmp_path / "label_manifest.json"
    manifest.write_text(json.dumps({"artifact_type": "label", "label_id": "x", "horizon": 180, "prediction_end": "2021-04-26", "row_count": len(labels), "labels_sha256": sha256_file(paths["labels"]), "pit_universe_artifact": "csi1800_pit_v2", "universe_manifest_sha256": "b" * 64, "universe_membership_sha256": "c" * 64, "universe": "csi1800_pit_union"}), encoding="utf-8")
    paths["manifest"] = manifest
    return paths


def test_relevance_boundaries_and_zero_idcg() -> None:
    frame = pd.DataFrame({"instrument": list("abcdefgh"), "score_model_raw": range(8), "label_value": [-.1, 0.0, .1999, .2, .4999, .5, .9999, 1.0]})
    result = _date_metrics(frame, "score_model_raw")
    assert result["ndcg_at_5"] > 0
    assert result["winner_100_count"] == 1
    zero = frame.assign(label_value=-1.0)
    zero_result = _date_metrics(zero, "score_model_raw")
    assert np.isnan(zero_result["ndcg_at_5"])


def test_selection_dates_and_maturity_filter(tmp_path: Path) -> None:
    paths = _write_case(tmp_path)
    # Make the last selection date immature; the label manifest says maturity ends earlier.
    manifest = json.loads(paths["manifest"].read_text())
    manifest["prediction_end"] = "2021-03-29"
    paths["manifest"].write_text(json.dumps(manifest))
    per_date, payload = evaluate_top_tail(paths["baseline"], paths["candidate"], paths["baseline_windows"], paths["candidate_windows"], paths["labels"], paths["manifest"], score_column="score_model_raw")
    assert per_date["trade_date"].dt.strftime("%Y-%m-%d").tolist() == ["2021-01-04", "2021-02-01", "2021-03-01", "2021-03-29"]
    assert payload["selection"]["count"] == 4
    assert payload["inputs"]["baseline_predictions"]["row_count"] == 81 * 8
    assert payload["inputs"]["baseline_predictions"]["filtered_row_count"] == 4 * 8
    assert payload["inputs"]["baseline_predictions"]["eligible_row_count"] == 4 * 8
    assert payload["contract"]["selection_cadence_trading_days"] == 20
    assert payload["contract"]["rank_icir_annualization"] == "sqrt(252/20)"


def test_pit_and_key_fail_closed(tmp_path: Path) -> None:
    paths = _write_case(tmp_path)
    bad = pd.read_parquet(paths["candidate"])
    bad.loc[0, "data_date"] = bad.loc[0, "trade_date"]
    bad.to_parquet(paths["candidate"], index=False)
    candidate_manifest_path = paths["candidate"].parent / "manifest.json"
    candidate_manifest = json.loads(candidate_manifest_path.read_text())
    candidate_manifest["predictions_sha256"] = sha256_file(paths["candidate"])
    candidate_manifest_path.write_text(json.dumps(candidate_manifest))
    with pytest.raises(TopTailValidationError, match="data_date<trade_date"):
        evaluate_top_tail(paths["baseline"], paths["candidate"], paths["baseline_windows"], paths["candidate_windows"], paths["labels"], paths["manifest"], score_column="score_model_raw")

    bad.loc[0, "data_date"] = bad.loc[0, "trade_date"] - pd.Timedelta(days=1)
    bad = bad.iloc[1:]
    bad.to_parquet(paths["candidate"], index=False)
    candidate_manifest["predictions_sha256"] = sha256_file(paths["candidate"])
    candidate_manifest["row_count"] = len(bad)
    candidate_manifest_path.write_text(json.dumps(candidate_manifest))
    with pytest.raises(TopTailValidationError, match="eligible key mismatch"):
        evaluate_top_tail(paths["baseline"], paths["candidate"], paths["baseline_windows"], paths["candidate_windows"], paths["labels"], paths["manifest"], score_column="score_model_raw")

    paths = _write_case(tmp_path / "static")
    manifest = json.loads(paths["manifest"].read_text())
    manifest.pop("pit_universe_artifact")
    manifest["universe"] = "static_current"
    paths["manifest"].write_text(json.dumps(manifest))
    with pytest.raises(TopTailValidationError, match="PIT lineage"):
        evaluate_top_tail(paths["baseline"], paths["baseline"], paths["baseline_windows"], paths["candidate_windows"], paths["labels"], paths["manifest"], score_column="score_model_raw")


def test_winner_any_ratio_and_bootstrap_determinism() -> None:
    frame = pd.DataFrame({"instrument": ["a", "b", "c", "d", "e", "f"], "score_model_raw": [6, 5, 4, 3, 2, 1], "label_value": [1.0, .2, .5, 0, 0, 0]})
    result = _date_metrics(frame, "score_model_raw")
    assert result["winner_100_any"] == 1.0
    first = _bootstrap_mean(np.arange(30, dtype=float), reps=100)
    second = _bootstrap_mean(np.arange(30, dtype=float), reps=100)
    assert first == second


def test_manifest_presence_invariants_and_lineage_fields(tmp_path: Path) -> None:
    paths = _write_case(tmp_path)
    (paths["candidate"].parent / "manifest.json").unlink()
    with pytest.raises(TopTailValidationError, match="manifest is required"):
        evaluate_top_tail(paths["baseline"], paths["candidate"], paths["baseline_windows"], paths["candidate_windows"], paths["labels"], paths["manifest"], score_column="score_model_raw")

    paths = _write_case(tmp_path / "mismatch")
    candidate_manifest_path = paths["candidate"].parent / "manifest.json"
    candidate_manifest = json.loads(candidate_manifest_path.read_text())
    candidate_manifest["transform_id"] = "wrong"
    candidate_manifest_path.write_text(json.dumps(candidate_manifest))
    with pytest.raises(TopTailValidationError, match="invariant mismatch"):
        evaluate_top_tail(paths["baseline"], paths["candidate"], paths["baseline_windows"], paths["candidate_windows"], paths["labels"], paths["manifest"], score_column="score_model_raw")

    paths = _write_case(tmp_path / "label-fields")
    label_manifest = json.loads(paths["manifest"].read_text())
    label_manifest.pop("universe_membership_sha256")
    paths["manifest"].write_text(json.dumps(label_manifest))
    with pytest.raises(TopTailValidationError, match="PIT lineage"):
        evaluate_top_tail(paths["baseline"], paths["candidate"], paths["baseline_windows"], paths["candidate_windows"], paths["labels"], paths["manifest"], score_column="score_model_raw")


def test_finite_eligible_and_top5_gate(tmp_path: Path) -> None:
    paths = _write_case(tmp_path)
    baseline = pd.read_parquet(paths["baseline"])
    candidate = pd.read_parquet(paths["candidate"])
    first_date = baseline["trade_date"].min()
    mask = baseline["trade_date"].eq(first_date) & baseline["instrument"].isin(["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"])
    baseline.loc[mask, "score_model_raw"] = np.inf
    candidate.loc[mask, "score_model_raw"] = np.inf
    baseline.to_parquet(paths["baseline"], index=False)
    candidate.to_parquet(paths["candidate"], index=False)
    for path, frame in ((paths["baseline"], baseline), (paths["candidate"], candidate)):
        manifest_path = path.parent / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["predictions_sha256"] = sha256_file(path)
        manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(TopTailValidationError, match="fewer than 5"):
        evaluate_top_tail(paths["baseline"], paths["candidate"], paths["baseline_windows"], paths["candidate_windows"], paths["labels"], paths["manifest"], score_column="score_model_raw")


def test_rank_icir_uses_selection_cadence() -> None:
    values = pd.Series([1.0, 2.0, 3.0, 4.0])
    expected = values.mean() / values.std(ddof=1) * np.sqrt(252 / 20)
    assert _rank_icir(values) == pytest.approx(expected)


def test_gate_and_atomic_output(tmp_path: Path) -> None:
    paths = _write_case(tmp_path, candidate_shift=100.0)
    per_date, payload = evaluate_top_tail(paths["baseline"], paths["candidate"], paths["baseline_windows"], paths["candidate_windows"], paths["labels"], paths["manifest"], score_column="score_model_raw")
    output = tmp_path / "out"
    write_top_tail_artifacts(per_date, payload, output)
    assert (output / "per_date.parquet").exists()
    assert (output / "comparison.json").exists()
    with pytest.raises(TopTailValidationError, match="non-empty"):
        write_top_tail_artifacts(per_date, payload, output)
