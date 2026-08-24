"""Strict, prediction-only top-tail comparison.

This module intentionally does not call the training or backtest stack.  It compares
two frozen prediction artifacts on the retraining dates in their rolling-window
artifacts and joins the realized label by ``(trade_date, instrument)``.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


TOP_K = 5
SELECTION_CADENCE_TRADING_DAYS = 20
BOOTSTRAP_BLOCK_LENGTH = 9
BOOTSTRAP_REPS = 10_000
BOOTSTRAP_SEED = 42
NDCG_DELTA_GATE = 0.02
TOP5_EXCESS_DELTA_GATE = 0.02
CAPTURE_100_MULTIPLIER = 1.10
YEAR_NONDECREASE_FRACTION = 0.8
PREDICTION_INVARIANT_FIELDS = (
    "signal_id",
    "source_manifest_hash",
    "train_window_days",
    "transform_id",
    "feature_visibility_contract",
    "prediction_start",
    "prediction_end",
    "window_count",
)


class TopTailValidationError(ValueError):
    """Raised when the evaluator cannot prove the comparison is well-formed."""


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (pd.Timestamp,)):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray, pd.Series)):
        return [_json_value(v) for v in value]
    return str(value)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_table(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise TopTailValidationError(f"input does not exist: {path}")
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() in {".csv", ".txt"}:
        return pd.read_csv(path)
    raise TopTailValidationError(f"unsupported table format: {path}")


def _path_info(
    path: str | Path,
    *,
    row_count: int | None = None,
    filtered_row_count: int | None = None,
    eligible_row_count: int | None = None,
) -> dict[str, Any]:
    absolute = Path(path).expanduser().resolve()
    try:
        relative = str(absolute.relative_to(Path.cwd().resolve()))
    except ValueError:
        relative = None
    payload: dict[str, Any] = {
        "path": str(path),
        "absolute_path": str(absolute),
        "relative_path": relative,
        "sha256": sha256_file(absolute),
    }
    if row_count is not None:
        payload["row_count"] = int(row_count)
    if filtered_row_count is not None:
        payload["filtered_row_count"] = int(filtered_row_count)
    if eligible_row_count is not None:
        payload["eligible_row_count"] = int(eligible_row_count)
    return payload


def _as_dates(frame: pd.DataFrame, column: str, *, context: str) -> pd.Series:
    if column not in frame.columns:
        raise TopTailValidationError(f"{context} missing required column: {column}")
    values = pd.to_datetime(frame[column], errors="coerce")
    if values.isna().any():
        raise TopTailValidationError(f"{context}.{column} contains invalid dates")
    return values.dt.normalize()


def _validate_prediction(path: str | Path, score_column: str) -> pd.DataFrame:
    frame = _read_table(path).copy()
    context = f"prediction[{path}]"
    frame["trade_date"] = _as_dates(frame, "trade_date", context=context)
    frame["data_date"] = _as_dates(frame, "data_date", context=context)
    for column in ("instrument", score_column):
        if column not in frame.columns:
            raise TopTailValidationError(f"{context} missing required column: {column}")
    frame["instrument"] = frame["instrument"].astype(str)
    frame[score_column] = pd.to_numeric(frame[score_column], errors="coerce")
    if frame.duplicated(["trade_date", "instrument"]).any():
        raise TopTailValidationError(f"{context} has duplicate (trade_date,instrument) rows")
    bad_pit = frame["data_date"] >= frame["trade_date"]
    if bad_pit.any():
        row = frame.loc[bad_pit].iloc[0]
        raise TopTailValidationError(
            f"{context} violates strict PIT data_date<trade_date at "
            f"{row.trade_date.date()}/{row.instrument}"
        )
    return frame


def _validate_windows(path: str | Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = _read_table(path).copy()
    context = f"rolling_windows[{path}]"
    values = _as_dates(frame, "predict_start", context=context)
    frame["predict_start"] = values
    if frame["predict_start"].duplicated().any():
        raise TopTailValidationError(f"{context} has duplicate predict_start dates")
    return frame, {
        "selection_dates": [x.strftime("%Y-%m-%d") for x in sorted(frame["predict_start"].unique())],
        "row_count": int(len(frame)),
    }


def _validate_labels(path: str | Path) -> pd.DataFrame:
    frame = _read_table(path).copy()
    context = f"labels[{path}]"
    frame["trade_date"] = _as_dates(frame, "trade_date", context=context)
    for column in ("instrument", "label_value"):
        if column not in frame.columns:
            raise TopTailValidationError(f"{context} missing required column: {column}")
    frame["instrument"] = frame["instrument"].astype(str)
    frame["label_value"] = pd.to_numeric(frame["label_value"], errors="coerce")
    if frame.duplicated(["trade_date", "instrument"]).any():
        raise TopTailValidationError(f"{context} has duplicate (trade_date,instrument) rows")
    return frame[["trade_date", "instrument", "label_value"]]


def _load_json(path: str | Path) -> dict[str, Any]:
    try:
        with Path(path).open(encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise TopTailValidationError(f"cannot read manifest {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise TopTailValidationError(f"manifest must be a JSON object: {path}")
    return value


def _validate_pit_manifest(path: str | Path) -> dict[str, Any]:
    manifest = _load_json(path)
    required = ("labels_sha256", "pit_universe_artifact", "universe_manifest_sha256", "universe_membership_sha256")
    missing = [field for field in required if not isinstance(manifest.get(field), str) or not manifest[field].strip()]
    if missing:
        raise TopTailValidationError(f"label manifest missing required PIT lineage fields {missing}: {path}")
    for field in required:
        if field.endswith("sha256") and (len(manifest[field]) != 64 or any(c not in "0123456789abcdefABCDEF" for c in manifest[field])):
            raise TopTailValidationError(f"label manifest has invalid {field}: {path}")
    return manifest


def _sibling_prediction_manifest(path: str | Path) -> Path | None:
    candidate = Path(path).resolve().parent / "manifest.json"
    return candidate if candidate.exists() else None


def _validate_prediction_manifest(path: Path | None, prediction_path: str | Path, row_count: int) -> dict[str, Any]:
    if path is None:
        raise TopTailValidationError(f"prediction manifest is required beside artifact: {prediction_path}")
    manifest = _load_json(path)
    expected_hash = manifest.get("predictions_sha256")
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        raise TopTailValidationError(f"prediction manifest missing predictions_sha256: {path}")
    if expected_hash != sha256_file(prediction_path):
        raise TopTailValidationError(f"prediction hash does not match its manifest: {prediction_path}")
    expected_rows = manifest.get("row_count")
    if expected_rows is None or int(expected_rows) != int(row_count):
        raise TopTailValidationError(f"prediction row count does not match its manifest: {prediction_path}")
    return manifest


def _validate_prediction_invariants(base: dict[str, Any], candidate: dict[str, Any]) -> None:
    missing = {
        side: [field for field in PREDICTION_INVARIANT_FIELDS if field not in manifest]
        for side, manifest in (("baseline", base), ("candidate", candidate))
    }
    if any(missing.values()):
        raise TopTailValidationError(f"prediction manifest missing invariant fields: {missing}")
    mismatches = {
        field: (base[field], candidate[field])
        for field in PREDICTION_INVARIANT_FIELDS
        if base[field] != candidate[field]
    }
    if mismatches:
        raise TopTailValidationError(f"baseline/candidate prediction invariant mismatch: {mismatches}")


def _validate_selection_cadence(selections: list[pd.Timestamp], calendar: Iterable[pd.Timestamp]) -> None:
    calendar_values = sorted(set(pd.Timestamp(value) for value in calendar))
    positions = {value: idx for idx, value in enumerate(calendar_values)}
    missing = [value.strftime("%Y-%m-%d") for value in selections if value not in positions]
    if missing:
        raise TopTailValidationError(f"selection dates missing from prediction trading calendar: {missing[:3]}")
    gaps = [positions[curr] - positions[prev] for prev, curr in zip(selections, selections[1:])]
    bad = [gap for gap in gaps if gap != SELECTION_CADENCE_TRADING_DAYS]
    if bad:
        raise TopTailValidationError(
            f"selection cadence must be {SELECTION_CADENCE_TRADING_DAYS} trading days; observed gaps {gaps[:6]}"
        )


def _relevance(label: float) -> int:
    if label < 0:
        return 0
    if label < 0.2:
        return 1
    if label < 0.5:
        return 2
    if label < 1.0:
        return 3
    return 4


def _dcg(relevances: Iterable[int]) -> float:
    return float(sum(rel / math.log2(idx + 2) for idx, rel in enumerate(relevances)))


def _date_metrics(frame: pd.DataFrame, score_column: str) -> dict[str, Any]:
    ordered = frame.sort_values([score_column, "instrument"], ascending=[False, True], kind="mergesort")
    top = ordered.head(TOP_K)
    labels = frame["label_value"].astype(float)
    top_labels = top["label_value"].astype(float)
    universe_mean = float(labels.mean())
    top5_mean = float(top_labels.mean()) if len(top_labels) else np.nan
    relevance = sorted((_relevance(v) for v in labels), reverse=True)
    idcg = _dcg(relevance[:TOP_K])
    ndcg = _dcg([_relevance(v) for v in top_labels]) / idcg if idcg > 0 else np.nan
    rank_ic = frame[score_column].corr(frame["label_value"], method="spearman") if len(frame) >= 2 else np.nan
    if pd.isna(rank_ic):
        rank_ic = np.nan
    result: dict[str, Any] = {
        "n_eligible": int(len(frame)),
        "top5_count": int(len(top)),
        "top5_mean": top5_mean,
        "universe_mean": universe_mean,
        "top5_excess": top5_mean - universe_mean if len(top_labels) else np.nan,
        "ndcg_at_5": ndcg,
        "idcg_at_5": idcg,
        "rank_ic": float(rank_ic) if not pd.isna(rank_ic) else np.nan,
    }
    winner_set = set(top["instrument"])
    for threshold, suffix in ((0.2, "20"), (0.5, "50"), (1.0, "100")):
        winners = set(frame.loc[frame["label_value"] >= threshold, "instrument"])
        if winners:
            capture = len(winner_set & winners) / len(winners)
            any_ratio = float(bool(winner_set & winners))
            result[f"winner_{suffix}_capture"] = capture
            result[f"winner_{suffix}_any"] = any_ratio
            result[f"winner_{suffix}_count"] = int(len(winners))
        else:
            result[f"winner_{suffix}_capture"] = np.nan
            result[f"winner_{suffix}_any"] = np.nan
            result[f"winner_{suffix}_count"] = 0
    return result


def _summary(series: pd.Series) -> dict[str, Any]:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return {"count": 0, "mean": None, "median": None, "positive_ratio": None}
    return {
        "count": int(len(clean)),
        "mean": float(clean.mean()),
        "median": float(clean.median()),
        "positive_ratio": float((clean > 0).mean()),
    }


def _rank_icir(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if len(clean) < 2:
        return None
    std = float(clean.std(ddof=1))
    return float(clean.mean() / std * math.sqrt(252 / SELECTION_CADENCE_TRADING_DAYS)) if std > 0 else None


def _bootstrap_mean(values: np.ndarray, *, block_length: int = BOOTSTRAP_BLOCK_LENGTH,
                    reps: int = BOOTSTRAP_REPS, seed: int = BOOTSTRAP_SEED) -> dict[str, Any]:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return {"n": 0, "mean": None, "ci95": [None, None], "block_length": block_length, "reps": reps, "seed": seed}
    rng = np.random.default_rng(seed)
    if len(values) == 1:
        mean = float(values[0])
        return {"n": 1, "mean": mean, "ci95": [mean, mean], "block_length": block_length, "reps": reps, "seed": seed}
    starts = rng.integers(0, len(values), size=(reps, math.ceil(len(values) / block_length)))
    offsets = np.arange(block_length)
    sample_indices = (starts[:, :, None] + offsets[None, None, :]) % len(values)
    sample_indices = sample_indices.reshape(reps, -1)[:, : len(values)]
    means = values[sample_indices].mean(axis=1)
    return {
        "n": int(len(values)),
        "mean": float(values.mean()),
        "ci95": [float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))],
        "block_length": block_length,
        "reps": reps,
        "seed": seed,
    }


def _yearly(frame: pd.DataFrame, metric: str) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for year, group in frame.groupby(frame["trade_date"].dt.year):
        rows[str(year)] = {
            "baseline": _summary(group[f"baseline_{metric}"]),
            "candidate": _summary(group[f"candidate_{metric}"]),
            "delta": _summary(group[f"delta_{metric}"]),
        }
    return rows


def _year_gate(yearly: dict[str, Any], metric: str) -> dict[str, Any]:
    years = sorted(yearly)
    nondecrease = [year for year in years if (
        yearly[year]["candidate"]["mean"] is not None
        and yearly[year]["baseline"]["mean"] is not None
        and yearly[year]["candidate"]["mean"] >= yearly[year]["baseline"]["mean"]
    )]
    required = int(math.ceil(YEAR_NONDECREASE_FRACTION * len(years))) if years else 0
    return {"years": years, "nondecrease_years": nondecrease, "required": required, "pass": len(nondecrease) >= required and bool(years)}


def _threshold_ok(candidate: float | None, baseline: float | None, multiplier: float) -> bool:
    if candidate is None or baseline is None:
        return False
    return candidate >= baseline * multiplier


def _gate(per_date: pd.DataFrame, yearly_ndcg: dict[str, Any], yearly_excess: dict[str, Any], boot: dict[str, Any]) -> dict[str, Any]:
    def mean(side: str, metric: str) -> float | None:
        return _summary(per_date[f"{side}_{metric}"])["mean"]
    def median(side: str, metric: str) -> float | None:
        return _summary(per_date[f"{side}_{metric}"])["median"]
    def lower(metric: str) -> float | None:
        value = boot[metric]["ci95"][0]
        return value

    checks = {
        "ndcg_mean_delta": mean("candidate", "ndcg_at_5") is not None and mean("baseline", "ndcg_at_5") is not None and mean("candidate", "ndcg_at_5") - mean("baseline", "ndcg_at_5") >= NDCG_DELTA_GATE,
        "ndcg_delta_ci_lower_positive": lower("ndcg_at_5") is not None and lower("ndcg_at_5") > 0,
        "ndcg_median_not_lower": median("candidate", "ndcg_at_5") is not None and median("baseline", "ndcg_at_5") is not None and median("candidate", "ndcg_at_5") >= median("baseline", "ndcg_at_5"),
        "ndcg_year_gate": _year_gate(yearly_ndcg, "ndcg_at_5"),
        "top5_excess_mean_delta": mean("candidate", "top5_excess") is not None and mean("baseline", "top5_excess") is not None and mean("candidate", "top5_excess") - mean("baseline", "top5_excess") >= TOP5_EXCESS_DELTA_GATE,
        "top5_excess_delta_ci_lower_positive": lower("top5_excess") is not None and lower("top5_excess") > 0,
        "top5_excess_median_not_lower": median("candidate", "top5_excess") is not None and median("baseline", "top5_excess") is not None and median("candidate", "top5_excess") >= median("baseline", "top5_excess"),
        "top5_excess_year_gate": _year_gate(yearly_excess, "top5_excess"),
        "capture100_mean_10pct": _threshold_ok(mean("candidate", "winner_100_capture"), mean("baseline", "winner_100_capture"), CAPTURE_100_MULTIPLIER),
        "capture100_delta_ci_lower_nonnegative": lower("winner_100_capture") is not None and lower("winner_100_capture") >= 0,
    }
    rank_base = mean("baseline", "rank_ic")
    rank_candidate = mean("candidate", "rank_ic")
    rank_ir_base = _rank_icir(per_date["baseline_rank_ic"])
    rank_ir_candidate = _rank_icir(per_date["candidate_rank_ic"])
    pos_base = _summary(per_date["baseline_rank_ic"])["positive_ratio"]
    pos_candidate = _summary(per_date["candidate_rank_ic"])["positive_ratio"]
    checks.update({
        "rank_ic_not_down_10pct": rank_base is not None and rank_candidate is not None and rank_candidate >= rank_base - abs(rank_base) * 0.10,
        "rank_icir_not_down_10pct": rank_ir_base is not None and rank_ir_candidate is not None and rank_ir_candidate >= rank_ir_base - abs(rank_ir_base) * 0.10,
        "rank_ic_positive_ratio_not_down_5pp": pos_base is not None and pos_candidate is not None and pos_candidate >= pos_base - 0.05,
    })
    hard = bool(per_date.attrs.get("hard_checks_pass", False))
    passed = hard and all(value is True or (isinstance(value, dict) and value.get("pass") is True) for value in checks.values())
    return {"pass": passed, "hard_checks_pass": hard, "checks": checks}


def evaluate_top_tail(
    baseline_predictions: str | Path,
    candidate_predictions: str | Path,
    baseline_windows: str | Path,
    candidate_windows: str | Path,
    labels: str | Path,
    label_manifest: str | Path,
    *,
    score_column: str,
    maturity_end: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Evaluate two frozen prediction tables and return per-date rows and JSON payload."""
    if not score_column:
        raise TopTailValidationError("score_column must be explicit")
    label_meta = _validate_pit_manifest(label_manifest)
    base = _validate_prediction(baseline_predictions, score_column)
    cand = _validate_prediction(candidate_predictions, score_column)
    base_total_rows = len(base)
    cand_total_rows = len(cand)
    base_pred_manifest_path = _sibling_prediction_manifest(baseline_predictions)
    cand_pred_manifest_path = _sibling_prediction_manifest(candidate_predictions)
    base_pred_manifest = _validate_prediction_manifest(base_pred_manifest_path, baseline_predictions, len(base))
    cand_pred_manifest = _validate_prediction_manifest(cand_pred_manifest_path, candidate_predictions, len(cand))
    _validate_prediction_invariants(base_pred_manifest, cand_pred_manifest)
    base_windows, base_window_meta = _validate_windows(baseline_windows)
    cand_windows, cand_window_meta = _validate_windows(candidate_windows)
    if set(base_windows["predict_start"]) != set(cand_windows["predict_start"]):
        raise TopTailValidationError("baseline/candidate rolling windows have different predict_start sets")
    label = _validate_labels(labels)
    label_total_rows = len(label)
    expected_label_hash = label_meta.get("labels_sha256")
    if expected_label_hash and expected_label_hash != sha256_file(labels):
        raise TopTailValidationError(f"label hash does not match its manifest: {labels}")
    if label_meta.get("row_count") is not None and int(label_meta["row_count"]) != label_total_rows:
        raise TopTailValidationError(f"label row count does not match its manifest: {labels}")
    finite_labels = np.isfinite(label["label_value"])
    label_max = label.loc[finite_labels, "trade_date"].max()
    manifest_end = pd.to_datetime(label_meta.get("prediction_end"), errors="coerce")
    if pd.isna(manifest_end):
        manifest_end = label_max
    cutoff = min(manifest_end, label_max)
    if maturity_end is not None:
        cutoff = min(cutoff, pd.to_datetime(maturity_end))
    selections = sorted(x for x in base_windows["predict_start"].unique() if x <= cutoff)
    if not selections:
        raise TopTailValidationError("no rolling predict_start date is at or before label maturity")
    _validate_selection_cadence(selections, base["trade_date"].unique())
    selected = set(selections)
    label_filtered_rows = int(label["trade_date"].isin(selected).sum())
    label = label[label["trade_date"].isin(selected)].copy()
    base_filtered_rows = int(base["trade_date"].isin(selected).sum())
    cand_filtered_rows = int(cand["trade_date"].isin(selected).sum())
    base = base[base["trade_date"].isin(selected)].copy()
    cand = cand[cand["trade_date"].isin(selected)].copy()
    label_keys = set(zip(label["trade_date"], label["instrument"]))
    rows: list[dict[str, Any]] = []
    baseline_eligible_rows = 0
    candidate_eligible_rows = 0
    for date in selections:
        b = base[base["trade_date"] == date].merge(label, on=["trade_date", "instrument"], how="inner")
        c = cand[cand["trade_date"] == date].merge(label, on=["trade_date", "instrument"], how="inner")
        b = b[np.isfinite(b[score_column]) & np.isfinite(b["label_value"])]
        c = c[np.isfinite(c[score_column]) & np.isfinite(c["label_value"])]
        bkeys = set(zip(b["trade_date"], b["instrument"]))
        ckeys = set(zip(c["trade_date"], c["instrument"]))
        if bkeys != ckeys:
            missing_b = sorted(ckeys - bkeys)[:3]
            missing_c = sorted(bkeys - ckeys)[:3]
            raise TopTailValidationError(
                f"eligible key mismatch on {date.date()}: "
                f"baseline_missing={missing_b}, candidate_missing={missing_c}"
            )
        if len(bkeys) < TOP_K:
            raise TopTailValidationError(f"fewer than {TOP_K} eligible rows on selection date {date.date()}")
        baseline_eligible_rows += len(b)
        candidate_eligible_rows += len(c)
        bm = _date_metrics(b, score_column)
        cm = _date_metrics(c, score_column)
        row: dict[str, Any] = {"trade_date": date}
        for name, value in bm.items():
            row[f"baseline_{name}"] = value
        for name, value in cm.items():
            row[f"candidate_{name}"] = value
        for metric in bm:
            bv, cv = bm[metric], cm[metric]
            row[f"delta_{metric}"] = cv - bv if np.isfinite(bv) and np.isfinite(cv) else np.nan
        rows.append(row)
    per_date = pd.DataFrame(rows).sort_values("trade_date").reset_index(drop=True)
    per_date.attrs["hard_checks_pass"] = True
    metric_names = [
        "ndcg_at_5", "top5_excess",
        "winner_20_capture", "winner_20_any", "winner_50_capture", "winner_50_any",
        "winner_100_capture", "winner_100_any", "rank_ic",
    ]
    boot = {metric: _bootstrap_mean(per_date[f"delta_{metric}"].to_numpy()) for metric in metric_names}
    yearly = {metric: _yearly(per_date, metric) for metric in metric_names}
    yearly_ndcg = yearly["ndcg_at_5"]
    yearly_excess = yearly["top5_excess"]
    summary_metrics = metric_names
    summary = {
        side: {metric: _summary(per_date[f"{side}_{metric}"]) for metric in summary_metrics}
        for side in ("baseline", "candidate")
    }
    summary["baseline"]["rank_icir"] = _rank_icir(per_date["baseline_rank_ic"])
    summary["candidate"]["rank_icir"] = _rank_icir(per_date["candidate_rank_ic"])
    summary["baseline"]["rank_ic_positive_ratio"] = summary["baseline"]["rank_ic"]["positive_ratio"]
    summary["candidate"]["rank_ic_positive_ratio"] = summary["candidate"]["rank_ic"]["positive_ratio"]
    payload = {
        "artifact_type": "top_tail_comparison",
        "contract": {
            "top_k": TOP_K,
            "selection_cadence_trading_days": SELECTION_CADENCE_TRADING_DAYS,
            "score_column": score_column,
            "selection_date_source": "rolling_windows.predict_start",
            "label_join_key": ["trade_date", "instrument"],
            "pit_rule": "data_date < trade_date",
            "rank_icir_annualization": "sqrt(252/20)",
            "ndcg_relevance": "y<0:0,[0,.2):1,[.2,.5):2,[.5,1):3,>=1:4",
            "bootstrap": {"method": "circular moving-block", "block_length": BOOTSTRAP_BLOCK_LENGTH, "reps": BOOTSTRAP_REPS, "seed": BOOTSTRAP_SEED},
            "gate_is_cagr_free": True,
        },
        "inputs": {
            "baseline_predictions": _path_info(baseline_predictions, row_count=base_total_rows, filtered_row_count=base_filtered_rows, eligible_row_count=baseline_eligible_rows),
            "candidate_predictions": _path_info(candidate_predictions, row_count=cand_total_rows, filtered_row_count=cand_filtered_rows, eligible_row_count=candidate_eligible_rows),
            "baseline_windows": _path_info(baseline_windows, row_count=base_window_meta["row_count"]),
            "candidate_windows": _path_info(candidate_windows, row_count=cand_window_meta["row_count"]),
            "labels": _path_info(labels, row_count=label_total_rows, filtered_row_count=label_filtered_rows, eligible_row_count=baseline_eligible_rows),
            "label_manifest": _path_info(label_manifest),
            "baseline_prediction_manifest": _path_info(base_pred_manifest_path) if base_pred_manifest_path else None,
            "candidate_prediction_manifest": _path_info(cand_pred_manifest_path) if cand_pred_manifest_path else None,
        },
        "selection": {"count": len(selections), "dates": [x.strftime("%Y-%m-%d") for x in selections], "maturity_end": cutoff.strftime("%Y-%m-%d"), "label_max_date": label_max.strftime("%Y-%m-%d")},
        "lineage_checks": {
            "pit_manifest": True,
            "prediction_manifests_checked": bool(base_pred_manifest_path and cand_pred_manifest_path),
            "prediction_hashes_checked": bool(base_pred_manifest and cand_pred_manifest),
            "prediction_invariants_equal": True,
            "prediction_pit_date": True,
            "duplicate_keys": True,
            "eligible_keys_equal": True,
            "label_hash_checked": bool(expected_label_hash),
            "label_maturity": True,
            "label_key_count": len(label_keys),
        },
        "baseline": summary["baseline"],
        "candidate": summary["candidate"],
        "paired_deltas": boot,
        "yearly": yearly,
    }
    payload["gate"] = _gate(per_date, yearly_ndcg, yearly_excess, boot)
    return per_date, _json_value(payload)


def write_top_tail_artifacts(per_date: pd.DataFrame, comparison: dict[str, Any], output_dir: str | Path, *, force: bool = False) -> None:
    """Write evaluator outputs, refusing accidental overwrite by default."""
    target = Path(output_dir).expanduser().resolve()
    if target.exists() and any(target.iterdir()):
        if not force:
            raise TopTailValidationError(f"output directory is non-empty; pass --force: {target}")
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=str(target.parent)))
    try:
        per_date.to_parquet(temp / "per_date.parquet", index=False)
        with (temp / "comparison.json").open("w", encoding="utf-8") as handle:
            json.dump(comparison, handle, indent=2, sort_keys=True, allow_nan=False)
        os.replace(temp, target)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
