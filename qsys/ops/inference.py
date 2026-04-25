from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.data.adapter import QlibAdapter
from qsys.research.mainline import resolve_mainline_feature_config
from qsys.strategy.generator import SignalGenerator


@dataclass(frozen=True)
class InferenceArtifacts:
    trade_date: str
    model_name: str
    model_path: str
    mainline_object_name: str
    bundle_id: str
    train_run_id: str
    predictions_path: str
    inference_summary_path: str
    prediction_count: int
    score_min: float | None
    score_max: float | None
    score_mean: float | None


class InferenceInvocationError(RuntimeError):
    pass


def run_shadow_daily_inference(
    *,
    trade_date: str,
    model_payload: dict[str, Any],
    output_dir: str | Path,
    universe: str = "csi300",
) -> InferenceArtifacts:
    mainline_object_name = str(model_payload["mainline_object_name"])
    feature_config = resolve_mainline_feature_config(mainline_object_name)
    if not feature_config:
        raise InferenceInvocationError(f"No feature config found for mainline object {mainline_object_name}")

    model_path = Path(str(model_payload["model_path"]))
    if not model_path.exists() or not model_path.is_dir():
        raise InferenceInvocationError(f"Model path is not a directory: {model_path}")

    adapter = QlibAdapter()
    adapter.init_qlib()
    features = adapter.get_features(universe, feature_config, start_time=trade_date, end_time=trade_date)
    if features is None or features.empty:
        raise InferenceInvocationError(f"No inference features available for trade_date={trade_date}")

    generator = SignalGenerator(model_path)
    scores = generator.predict(features)
    prediction_frame = _build_prediction_frame(scores=scores, trade_date=trade_date, model_payload=model_payload)
    if prediction_frame.empty:
        raise InferenceInvocationError(f"Model produced no predictions for trade_date={trade_date}")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = output_dir / "predictions.csv"
    inference_summary_path = output_dir / "inference_summary.json"

    prediction_frame.to_csv(predictions_path, index=False, quoting=csv.QUOTE_MINIMAL)
    summary_payload = {
        "trade_date": trade_date,
        "model_name": str(model_payload["model_name"]),
        "model_path": str(model_payload["model_path"]),
        "mainline_object_name": mainline_object_name,
        "bundle_id": str(model_payload["bundle_id"]),
        "train_run_id": str(model_payload["train_run_id"]),
        "prediction_count": int(len(prediction_frame)),
        "score_min": _safe_float(prediction_frame["score"].min()),
        "score_max": _safe_float(prediction_frame["score"].max()),
        "score_mean": _safe_float(prediction_frame["score"].mean()),
        "status": "success",
    }
    inference_summary_path.write_text(json.dumps(summary_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return InferenceArtifacts(
        trade_date=trade_date,
        model_name=str(model_payload["model_name"]),
        model_path=str(model_payload["model_path"]),
        mainline_object_name=mainline_object_name,
        bundle_id=str(model_payload["bundle_id"]),
        train_run_id=str(model_payload["train_run_id"]),
        predictions_path=str(predictions_path),
        inference_summary_path=str(inference_summary_path),
        prediction_count=int(len(prediction_frame)),
        score_min=summary_payload["score_min"],
        score_max=summary_payload["score_max"],
        score_mean=summary_payload["score_mean"],
    )


def write_failed_inference_summary(
    *,
    trade_date: str,
    model_payload: dict[str, Any],
    output_dir: str | Path,
    error: str,
) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "inference_summary.json"
    payload = {
        "trade_date": trade_date,
        "model_name": str(model_payload.get("model_name", "")),
        "model_path": str(model_payload.get("model_path", "")),
        "mainline_object_name": str(model_payload.get("mainline_object_name", "")),
        "bundle_id": str(model_payload.get("bundle_id", "")),
        "train_run_id": str(model_payload.get("train_run_id", "")),
        "prediction_count": 0,
        "score_min": None,
        "score_max": None,
        "score_mean": None,
        "status": "failed",
        "error": error,
    }
    summary_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary_path


def _build_prediction_frame(*, scores: pd.Series, trade_date: str, model_payload: dict[str, Any]) -> pd.DataFrame:
    if not isinstance(scores, pd.Series):
        scores = pd.Series(scores)
    frame = scores.rename("score").reset_index()
    if "instrument" not in frame.columns:
        if "ts_code" in frame.columns:
            frame = frame.rename(columns={"ts_code": "instrument"})
        elif "index" in frame.columns:
            frame = frame.rename(columns={"index": "instrument"})
    if "datetime" in frame.columns:
        frame = frame.drop(columns=["datetime"])
    if "trade_date" in frame.columns:
        frame = frame.drop(columns=["trade_date"])
    if "instrument" not in frame.columns:
        raise InferenceInvocationError("Prediction output does not contain an instrument column")

    frame["trade_date"] = trade_date
    frame["model_name"] = str(model_payload["model_name"])
    frame["mainline_object_name"] = str(model_payload["mainline_object_name"])
    frame["bundle_id"] = str(model_payload["bundle_id"])
    frame["train_run_id"] = str(model_payload["train_run_id"])
    frame = frame[[
        "trade_date",
        "instrument",
        "score",
        "model_name",
        "mainline_object_name",
        "bundle_id",
        "train_run_id",
    ]].copy()
    frame["instrument"] = frame["instrument"].astype(str)
    frame["score"] = pd.to_numeric(frame["score"], errors="coerce")
    frame = frame.dropna(subset=["score"]).sort_values(["trade_date", "instrument"]).reset_index(drop=True)
    return frame


def _safe_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)
