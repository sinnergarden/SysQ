"""Independent validation for confirmation-period rolling model artifacts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

import numpy as np
import pandas as pd

from qsys.research.matrix_job import (
    RollingResearchConfig,
    build_matrix_jobs,
    expand_multi_label_generators,
)
from qsys.research.signal_pipeline import _research_config_sha256


def _sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_hash(path: Path, expected: str, label: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is not a regular file: {path}")
    observed = _sha256(path)
    if observed != expected:
        raise ValueError(f"{label} hash mismatch: {path}")
    return observed


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _recompute_daily_metrics(
    signal: pd.DataFrame,
    labels: pd.DataFrame,
    *,
    min_count: int = 5,
) -> tuple[pd.DataFrame, int]:
    """Recompute daily Pearson/Spearman IC without evaluator helpers."""
    signal_values = signal.copy()
    label_values = labels.copy()
    if "is_valid" in signal_values:
        signal_values = signal_values[signal_values["is_valid"] != False]  # noqa: E712
    if "is_valid" in label_values:
        label_values = label_values[label_values["is_valid"] != False]  # noqa: E712
    merged = signal_values[["trade_date", "instrument", "score"]].merge(
        label_values[["trade_date", "instrument", "label_value"]],
        on=["trade_date", "instrument"],
        how="inner",
    ).dropna(subset=["score", "label_value"])
    rows: list[dict[str, Any]] = []
    for date, group in merged.groupby("trade_date", sort=True):
        count = int(len(group))
        rows.append({
            "date": str(date)[:10],
            "n": count,
            "ic": (
                float(group["score"].corr(group["label_value"]))
                if count >= min_count else np.nan
            ),
            "rank_ic": (
                float(group["score"].corr(group["label_value"], method="spearman"))
                if count >= min_count else np.nan
            ),
        })
    return pd.DataFrame(rows), int(len(merged))


def _compare_daily(
    recomputed: pd.DataFrame,
    stored: pd.DataFrame,
    value_column: str,
) -> None:
    expected = recomputed[["date", value_column, "n"]].copy()
    actual = stored[["date", value_column, "n"]].copy()
    expected["date"] = expected["date"].astype(str).str[:10]
    actual["date"] = actual["date"].astype(str).str[:10]
    expected = expected.sort_values("date").reset_index(drop=True)
    actual = actual.sort_values("date").reset_index(drop=True)
    if not expected[["date", "n"]].equals(actual[["date", "n"]]):
        raise ValueError(f"stored {value_column} daily keys/counts mismatch")
    if not np.allclose(
        expected[value_column].to_numpy(dtype=float),
        actual[value_column].to_numpy(dtype=float),
        equal_nan=True,
        rtol=1e-12,
        atol=1e-12,
    ):
        raise ValueError(f"stored {value_column} values mismatch independent recompute")


def validate_stage_b_experiment(
    *,
    config_path: Path,
    research_root: Path,
    cache_manifest_path: Path,
    cache_validation_path: Path,
    holdout_start: str,
    output_path: Path,
) -> dict[str, Any]:
    """Validate cache, SignalRuns, model diagnostics and all label evaluations."""
    config_path = config_path.resolve()
    research_root = research_root.resolve()
    config = RollingResearchConfig.from_file(config_path)
    if str(config.calendar["end_date"]) >= holdout_start:
        raise ValueError("Stage-B calendar overlaps the declared holdout")

    cache_manifest_path = cache_manifest_path.resolve()
    cache_validation_path = cache_validation_path.resolve()
    cache_manifest = json.loads(cache_manifest_path.read_text(encoding="utf-8"))
    cache_validation = json.loads(cache_validation_path.read_text(encoding="utf-8"))
    if cache_manifest.get("config_sha256") != _sha256(config_path):
        raise ValueError("cache manifest is not bound to the Stage-B config")
    if (
        cache_validation.get("status") != "pass"
        or cache_validation.get("manifest_sha256") != _sha256(cache_manifest_path)
    ):
        raise ValueError("Stage-B cache validation is not a pass for this manifest")

    experiment_dir = research_root / "experiments" / config.experiment_id
    windows_path = experiment_dir / "rolling_windows.csv"
    experiment_manifest_path = experiment_dir / "signal_research_manifest.json"
    windows = pd.read_csv(windows_path, dtype=str)
    experiment_manifest = json.loads(
        experiment_manifest_path.read_text(encoding="utf-8")
    )
    if (
        experiment_manifest.get("artifact_type") != "signal_research"
        or int(experiment_manifest.get("window_count", -1)) != len(windows)
        or str(windows["predict_start"].min()) < str(config.calendar["start_date"])
        or str(windows["predict_end"].max()) > str(config.calendar["end_date"])
        or str(windows["predict_end"].max()) >= holdout_start
    ):
        raise ValueError("Stage-B experiment/window contract mismatch")

    effective = expand_multi_label_generators(config.generators)
    jobs = build_matrix_jobs(config, effective_generators=effective)
    generator_configs = {str(value["generator_id"]): value for value in effective}
    expected_window_ids = [
        (
            f"train={row.train_start}:{row.train_end};"
            f"predict={row.predict_start}:{row.predict_end}"
        )
        for row in windows.itertuples(index=False)
    ]
    config_identity = _research_config_sha256(config)
    signal_results: list[dict[str, Any]] = []

    for job in jobs:
        signal_dir = research_root / "signals" / job.signal_id / job.signal_run_id
        signal_manifest_path = signal_dir / "manifest.json"
        signal_manifest = json.loads(signal_manifest_path.read_text(encoding="utf-8"))
        prediction_path = signal_dir / str(signal_manifest["predictions_file"])
        prediction_sha256 = _require_hash(
            prediction_path,
            str(signal_manifest["predictions_sha256"]),
            "SignalRun predictions",
        )
        signal = pd.read_parquet(prediction_path)
        if (
            len(signal) != int(signal_manifest["row_count"])
            or signal.duplicated(["trade_date", "instrument"]).any()
            or not np.isfinite(pd.to_numeric(signal["score"], errors="coerce")).all()
            or not (signal["data_date"].astype(str) < signal["trade_date"].astype(str)).all()
            or str(signal["trade_date"].min()) != str(config.calendar["start_date"])
            or str(signal["trade_date"].max()) != str(config.calendar["end_date"])
            or str(signal["trade_date"].max()) >= holdout_start
        ):
            raise ValueError(f"SignalRun frame contract mismatch: {job.signal_id}")
        if (
            signal_manifest.get("source_manifest_hash") != config.source_manifest_hash
            or signal_manifest.get("feature_list_contract", {}).get("feature_list_id")
            != config.feature_list_id
            or int(signal_manifest.get("window_count", -1)) != len(windows)
        ):
            raise ValueError(f"SignalRun lineage mismatch: {job.signal_id}")

        diagnostics = signal_manifest.get("model_diagnostics") or {}
        diagnostic_windows = diagnostics.get("windows") or []
        if (
            diagnostics.get("schema_version") != "rolling_model_diagnostics_v1"
            or [str(value.get("window_id")) for value in diagnostic_windows]
            != expected_window_ids
        ):
            raise ValueError(f"model diagnostic windows mismatch: {job.generator_id}")
        validation_values = [value.get("validation_rank_ic") for value in diagnostic_windows]
        if any(value is None or not np.isfinite(float(value)) for value in validation_values):
            raise ValueError(f"model diagnostics contain invalid validation IC: {job.generator_id}")
        generator_type = str(generator_configs[job.generator_id]["type"])
        expected_model_type = (
            "ridge_regression" if generator_type == "single_label_ridge"
            else "lightgbm_regression"
        )
        if any(value.get("model_type") != expected_model_type for value in diagnostic_windows):
            raise ValueError(f"model diagnostic type mismatch: {job.generator_id}")

        evaluation_results: list[dict[str, Any]] = []
        for label_config in config.labels:
            label_id = str(label_config["label_id"])
            eval_dir = signal_dir / "eval" / label_id
            eval_manifest_path = eval_dir / "manifest.json"
            eval_manifest = json.loads(eval_manifest_path.read_text(encoding="utf-8"))
            if eval_manifest.get("methodology", {}).get(
                "research_config_sha256"
            ) != config_identity:
                raise ValueError(f"evaluation config identity mismatch: {label_id}")
            for name, artifact in eval_manifest.get("outputs", {}).items():
                artifact_path = eval_dir / str(artifact["path"])
                _require_hash(artifact_path, str(artifact["sha256"]), f"evaluation {name}")
            input_signal = eval_manifest["inputs"]["signal"]
            if (
                input_signal.get("data_sha256") != prediction_sha256
                or input_signal.get("manifest_sha256") != _sha256(signal_manifest_path)
            ):
                raise ValueError(f"evaluation signal lineage mismatch: {label_id}")
            input_label = eval_manifest["inputs"]["label"]
            label_path = Path(str(input_label["data_path"]))
            label_manifest_path = Path(str(input_label["manifest_path"]))
            _require_hash(label_path, str(input_label["data_sha256"]), "label data")
            _require_hash(
                label_manifest_path,
                str(input_label["manifest_sha256"]),
                "label manifest",
            )
            if input_label.get("lineage_status") != "materialized":
                raise ValueError(f"evaluation label lineage is not materialized: {label_id}")
            labels = pd.read_parquet(
                label_path,
                columns=["trade_date", "instrument", "label_value", "is_valid"],
                filters=[
                    ("trade_date", ">=", str(config.calendar["start_date"])),
                    ("trade_date", "<=", str(config.calendar["end_date"])),
                ],
            )
            daily, observation_count = _recompute_daily_metrics(signal, labels)
            stored_ic = pd.read_parquet(eval_dir / "ic_daily.parquet")
            stored_rank_ic = pd.read_parquet(eval_dir / "rank_ic_daily.parquet")
            _compare_daily(daily, stored_ic, "ic")
            _compare_daily(daily, stored_rank_ic, "rank_ic")
            summary = json.loads((eval_dir / "summary.json").read_text(encoding="utf-8"))
            if (
                int(summary.get("n_obs", -1)) != observation_count
                or not np.isclose(float(summary["ic_mean"]), float(daily["ic"].mean()))
                or not np.isclose(
                    float(summary["rank_ic_mean"]), float(daily["rank_ic"].mean())
                )
            ):
                raise ValueError(f"evaluation summary mismatch: {job.generator_id}/{label_id}")
            yearly = daily.assign(year=daily["date"].str[:4]).groupby("year").agg(
                ic_mean=("ic", "mean"),
                rank_ic_mean=("rank_ic", "mean"),
                day_count=("date", "count"),
            )
            evaluation_results.append({
                "label_id": label_id,
                "observation_count": observation_count,
                "day_count": int(len(daily)),
                "ic_mean": float(daily["ic"].mean()),
                "rank_ic_mean": float(daily["rank_ic"].mean()),
                "yearly": {
                    str(year): {
                        "ic_mean": float(row.ic_mean),
                        "rank_ic_mean": float(row.rank_ic_mean),
                        "day_count": int(row.day_count),
                    }
                    for year, row in yearly.iterrows()
                },
                "evaluation_identity_sha256": eval_manifest[
                    "evaluation_identity_sha256"
                ],
            })

        model_summary: dict[str, Any] = {
            "validation_rank_ic_min": float(min(validation_values)),
            "validation_rank_ic_max": float(max(validation_values)),
            "validation_rank_ic_mean": float(np.mean(validation_values)),
        }
        if generator_type == "single_label_ridge":
            coefficients = [
                float(value["signed_coefficient"]["operating_cf_to_profit"])
                for value in diagnostic_windows
            ]
            model_summary["signed_coefficient_positive_ratio"] = float(
                np.mean(np.asarray(coefficients) > 0)
            )
            model_summary["signed_coefficient_min"] = float(min(coefficients))
            model_summary["signed_coefficient_max"] = float(max(coefficients))
        else:
            iterations = [int(value["best_iteration"]) for value in diagnostic_windows]
            model_summary["best_iteration_min"] = min(iterations)
            model_summary["best_iteration_max"] = max(iterations)

        signal_results.append({
            "generator_id": job.generator_id,
            "generator_type": generator_type,
            "signal_id": job.signal_id,
            "signal_run_id": job.signal_run_id,
            "row_count": len(signal),
            "predictions_sha256": prediction_sha256,
            "signal_manifest_sha256": _sha256(signal_manifest_path),
            "model_diagnostics": model_summary,
            "evaluations": evaluation_results,
        })

    result = {
        "schema_version": "stage_b_independent_validation_v1",
        "status": "pass",
        "experiment_id": config.experiment_id,
        "config_path": str(config_path),
        "config_sha256": _sha256(config_path),
        "research_config_sha256": config_identity,
        "cache_manifest_path": str(cache_manifest_path),
        "cache_manifest_sha256": _sha256(cache_manifest_path),
        "cache_validation_path": str(cache_validation_path),
        "cache_validation_sha256": _sha256(cache_validation_path),
        "experiment_manifest_sha256": _sha256(experiment_manifest_path),
        "window_count": len(windows),
        "holdout_start": holdout_start,
        "holdout_consumed": False,
        "checks": {
            "cache_and_config_lineage": "pass",
            "signal_hash_rows_dates_and_no_lookahead": "pass",
            "model_window_diagnostics": "pass",
            "label_and_evaluation_lineage": "pass",
            "daily_ic_independent_recompute": "pass",
            "holdout_isolation": "pass",
        },
        "signals": signal_results,
    }
    _write_json_atomic(output_path, result)
    return result
