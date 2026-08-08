"""Deterministic, artifact-only inference for explicitly pinned model blends.

This module implements ``UC_DAILY_INFERENCE_RUN``.  It deliberately does not
import or call ``DailyRunner``: inference produces a research candidate
artifact only and must never mutate broker, trader, ledger, or account state.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


class InferenceContractError(RuntimeError):
    """Raised when inference inputs fail a safety or provenance contract."""


@dataclass(frozen=True)
class InferenceDates:
    """Resolved dates for post-close inference and next-session use."""

    signal_date: str
    data_date: str
    execution_date: str
    expected_completed_date: str


@dataclass(frozen=True)
class InferenceRunResult:
    """Location and in-memory payload produced by one inference attempt."""

    artifact_path: Path
    payload: dict[str, Any]


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_candidate_hash(candidates: list[dict[str, Any]]) -> str:
    """Return a stable hash of material candidate values.

    Attempt-specific fields are intentionally excluded so two runs over the
    same inputs can be compared even though their ``run_id`` differs.
    """

    material = []
    for row in candidates:
        material.append(
            {
                key: value
                for key, value in row.items()
                if key not in {"run_id", "created_at"}
            }
        )
    return _canonical_hash(material)


def _normalise_date(value: Any) -> str:
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError as exc:
        raise InferenceContractError(
            f"invalid date {value!r}; expected YYYY-MM-DD"
        ) from exc


def _normalise_optional_date(value: Any) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    return _normalise_date(value)


def load_open_dates(project_root: Path) -> list[str]:
    """Load the authoritative local Tushare open-session calendar read-only."""

    db_path = project_root / "data" / "meta.db"
    if not db_path.exists():
        raise InferenceContractError(f"trading calendar database is missing: {db_path}")

    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            rows = conn.execute(
                "SELECT cal_date FROM trade_cal WHERE is_open = 1 ORDER BY cal_date"
            ).fetchall()
    except sqlite3.Error as exc:
        raise InferenceContractError(
            f"cannot read trading calendar from {db_path}: {exc}"
        ) from exc

    dates = sorted({_normalise_date(row[0]) for row in rows if row and row[0]})
    if not dates:
        raise InferenceContractError(
            f"trading calendar has no open sessions: {db_path}"
        )
    return dates


def _parse_cutoff(value: str) -> time:
    try:
        parsed = time.fromisoformat(value)
        if parsed.tzinfo is not None:
            raise ValueError("timezone is not allowed")
        return parsed
    except ValueError as exc:
        raise InferenceContractError(
            f"invalid inference.market_close_cutoff={value!r}; expected HH:MM"
        ) from exc


def resolve_inference_dates(
    signal_date: str | None,
    execution_date: str | None,
    open_dates: Sequence[str],
    *,
    now: datetime | None = None,
    market_close_cutoff: str = "18:00",
) -> InferenceDates:
    """Resolve safe post-close signal and next-open execution dates.

    ``auto`` never assumes an unfinished same-day close.  Before the configured
    cutoff it resolves to the previous open session; after the cutoff it may
    use today only when today is an open session.  Explicit dates are held to
    the same completed-close boundary.
    """

    sessions = sorted({_normalise_date(value) for value in open_dates})
    if not sessions:
        raise InferenceContractError(
            "cannot resolve inference dates without an open-session calendar"
        )

    now_cn = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    if now_cn.tzinfo is None:
        now_cn = now_cn.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    else:
        now_cn = now_cn.astimezone(ZoneInfo("Asia/Shanghai"))

    cutoff = _parse_cutoff(market_close_cutoff)
    anchor = now_cn.date()
    if now_cn.time().replace(tzinfo=None) < cutoff:
        anchor -= timedelta(days=1)
    anchor_text = anchor.strftime("%Y-%m-%d")
    completed = [value for value in sessions if value <= anchor_text]
    if not completed:
        raise InferenceContractError(
            f"calendar has no completed session on or before {anchor_text}"
        )
    expected_completed = completed[-1]

    requested = (signal_date or "auto").strip().lower()
    resolved_signal = (
        expected_completed if requested == "auto" else _normalise_date(requested)
    )
    if resolved_signal not in sessions:
        raise InferenceContractError(
            f"signal_date is not an open trading session: {resolved_signal}"
        )
    if resolved_signal > expected_completed:
        raise InferenceContractError(
            "signal_date is not a completed close: "
            f"signal_date={resolved_signal}, latest_completed={expected_completed}"
        )

    following = [value for value in sessions if value > resolved_signal]
    if not following:
        raise InferenceContractError(
            f"calendar has no execution session after signal_date={resolved_signal}"
        )
    expected_execution = following[0]
    if execution_date:
        supplied_execution = _normalise_date(execution_date)
        if supplied_execution != expected_execution:
            raise InferenceContractError(
                "execution_date must be the next open session: "
                f"expected={expected_execution}, got={supplied_execution}"
            )
    else:
        supplied_execution = expected_execution

    return InferenceDates(
        signal_date=resolved_signal,
        data_date=resolved_signal,
        execution_date=supplied_execution,
        expected_completed_date=expected_completed,
    )


def _require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InferenceContractError(f"{name} must be a mapping")
    return value


def validate_inference_config(
    strategy_id: str,
    strategy_config: dict[str, Any],
    project_root: Path,
    *,
    require_model_files: bool = True,
) -> dict[str, Any]:
    """Validate and normalise an explicit model-blend inference config."""

    configured_id = str(strategy_config.get("strategy_id") or "")
    if configured_id != strategy_id:
        raise InferenceContractError(
            f"strategy config id mismatch: requested={strategy_id}, configured={configured_id!r}"
        )

    inference = _require_mapping(strategy_config.get("inference"), "inference")
    if inference.get("engine") != "pinned_model_blend_v1":
        raise InferenceContractError(
            "inference.engine must be 'pinned_model_blend_v1' for canonical inference"
        )
    bundle = _require_mapping(inference.get("model_bundle"), "inference.model_bundle")
    bundle_id = str(bundle.get("bundle_id") or "").strip()
    if not bundle_id or "latest" in bundle_id.lower():
        raise InferenceContractError(
            "model bundle requires an explicit non-latest bundle_id"
        )

    feature_list_id = str(
        inference.get("feature_list_id") or strategy_config.get("feature_set") or ""
    ).strip()
    universe = str(
        inference.get("universe") or strategy_config.get("universe") or ""
    ).strip()
    if not feature_list_id or not universe:
        raise InferenceContractError("inference requires feature_list_id and universe")

    raw_models = bundle.get("models")
    if not isinstance(raw_models, list) or len(raw_models) < 2:
        raise InferenceContractError(
            "inference.model_bundle.models must contain at least two models"
        )

    models: list[dict[str, Any]] = []
    tags: set[str] = set()
    for index, raw in enumerate(raw_models):
        model = _require_mapping(raw, f"inference.model_bundle.models[{index}]")
        tag = str(model.get("tag") or "").strip()
        model_hash = str(model.get("model_hash") or "").strip()
        relative_dir = str(model.get("model_dir") or "").strip()
        label_id = str(model.get("label_id") or "").strip()
        artifact_sha256 = _require_mapping(
            model.get("artifact_sha256"),
            f"inference.model_bundle.models[{index}].artifact_sha256",
        )
        try:
            weight = float(model.get("weight"))
            horizon = int(model.get("horizon"))
        except (TypeError, ValueError) as exc:
            raise InferenceContractError(
                f"model {tag or index} has invalid weight or horizon"
            ) from exc
        if not tag or tag in tags:
            raise InferenceContractError(
                f"model tags must be non-empty and unique: {tag!r}"
            )
        if not model_hash or not relative_dir or not label_id:
            raise InferenceContractError(
                f"model {tag} lacks model_hash, model_dir, or label_id"
            )
        if "latest" in model_hash.lower() or "latest" in relative_dir.lower():
            raise InferenceContractError(
                f"model {tag} uses forbidden latest resolution"
            )
        if weight <= 0 or horizon <= 0:
            raise InferenceContractError(
                f"model {tag} requires positive weight and horizon"
            )

        relative_model_dir = Path(relative_dir)
        if relative_model_dir.is_absolute() or ".." in relative_model_dir.parts:
            raise InferenceContractError(
                f"model {tag} directory must be a project-relative canonical path"
            )
        unresolved_model_dir = project_root / relative_model_dir
        current_path = project_root
        for part in relative_model_dir.parts:
            current_path = current_path / part
            if current_path.is_symlink():
                raise InferenceContractError(
                    f"model {tag} path must not contain symlinks: {current_path}"
                )

        model_dir = unresolved_model_dir.resolve()
        allowed_root = (project_root / "data" / "research" / "models").resolve()
        if model_dir != allowed_root and allowed_root not in model_dir.parents:
            raise InferenceContractError(
                f"model {tag} resolves outside {allowed_root}: {model_dir}"
            )
        if model_dir.name != model_hash:
            raise InferenceContractError(
                f"model {tag} path/hash mismatch: dir={model_dir.name}, hash={model_hash}"
            )
        required_files = ["model.txt", "center.json", "scale.json", "meta.json"]
        if set(artifact_sha256) != set(required_files):
            raise InferenceContractError(
                f"model {tag} artifact_sha256 must contain exactly {required_files}"
            )
        normalised_artifact_sha256: dict[str, str] = {}
        for filename in required_files:
            expected_digest = str(artifact_sha256.get(filename) or "").lower()
            if len(expected_digest) != 64 or any(
                char not in "0123456789abcdef" for char in expected_digest
            ):
                raise InferenceContractError(
                    f"model {tag} has invalid SHA-256 for {filename}"
                )
            normalised_artifact_sha256[filename] = expected_digest
        if require_model_files:
            missing = [
                name for name in required_files if not (model_dir / name).is_file()
            ]
            if missing:
                raise InferenceContractError(
                    f"model {tag} is incomplete at {model_dir}: missing {missing}"
                )
            if any((model_dir / name).is_symlink() for name in required_files):
                raise InferenceContractError(
                    f"model {tag} contains forbidden symlinked artifacts"
                )
            for filename, expected_digest in normalised_artifact_sha256.items():
                actual_digest = _file_sha256(model_dir / filename)
                if actual_digest != expected_digest:
                    raise InferenceContractError(
                        f"model {tag} artifact digest mismatch for {filename}: "
                        f"expected={expected_digest}, actual={actual_digest}"
                    )

        tags.add(tag)
        models.append(
            {
                "tag": tag,
                "weight": weight,
                "horizon": horizon,
                "label_id": label_id,
                "model_hash": model_hash,
                "model_dir": relative_dir,
                "artifact_sha256": normalised_artifact_sha256,
                "resolved_model_dir": model_dir,
            }
        )

    total_weight = sum(model["weight"] for model in models)
    if abs(total_weight - 1.0) > 1e-9:
        raise InferenceContractError(
            f"model weights must sum to 1.0, got {total_weight}"
        )

    exclude_name_patterns = inference.get("exclude_name_patterns", ["ST", "退"])
    if (
        not isinstance(exclude_name_patterns, list)
        or not exclude_name_patterns
        or any(not str(pattern).strip() for pattern in exclude_name_patterns)
    ):
        raise InferenceContractError(
            "inference.exclude_name_patterns must be a non-empty list"
        )

    settings = {
        "engine": "pinned_model_blend_v1",
        "bundle_id": bundle_id,
        "feature_list_id": feature_list_id,
        "universe": universe,
        "models": models,
        "top_k": int(inference.get("top_k", 200)),
        "score_transform": str(
            inference.get("score_transform", "daily_cs_zscore_unclipped_ddof0")
        ),
        "min_universe_size": int(inference.get("min_universe_size", 500)),
        "min_eligible_size": int(inference.get("min_eligible_size", 450)),
        "min_feature_coverage": float(inference.get("min_feature_coverage", 0.75)),
        "min_global_feature_coverage": float(
            inference.get("min_global_feature_coverage", 0.75)
        ),
        "min_listed_days": int(inference.get("min_listed_days", 180)),
        "min_amount": float(inference.get("min_amount", 0.0)),
        "exclude_name_patterns": [str(item) for item in exclude_name_patterns],
        "market_close_cutoff": str(inference.get("market_close_cutoff", "18:00")),
        "output_root": str(inference.get("output_root", "outputs")),
        "universe_snapshot_semantics": str(
            inference.get(
                "universe_snapshot_semantics", "current_constituents_snapshot"
            )
        ),
    }
    if settings["top_k"] <= 0:
        raise InferenceContractError("inference.top_k must be positive")
    if settings["score_transform"] != "daily_cs_zscore_unclipped_ddof0":
        raise InferenceContractError(
            "inference.score_transform must be daily_cs_zscore_unclipped_ddof0"
        )
    if settings["output_root"] != "outputs":
        raise InferenceContractError("inference.output_root must be canonical outputs")
    if settings["universe_snapshot_semantics"] != "current_constituents_snapshot":
        raise InferenceContractError(
            "pinned_model_blend_v1 requires current_constituents_snapshot semantics"
        )
    for key in ("min_universe_size", "min_eligible_size"):
        if settings[key] <= 0:
            raise InferenceContractError(f"inference.{key} must be positive")
    for key in ("min_listed_days", "min_amount"):
        if settings[key] < 0:
            raise InferenceContractError(f"inference.{key} must be non-negative")
    for key in ("min_feature_coverage", "min_global_feature_coverage"):
        if not 0.0 <= settings[key] <= 1.0:
            raise InferenceContractError(f"inference.{key} must be between 0 and 1")
    settings["bundle_hash"] = _canonical_hash(
        {
            "bundle_id": bundle_id,
            "feature_list_id": feature_list_id,
            "models": [
                {
                    key: value
                    for key, value in model.items()
                    if key != "resolved_model_dir"
                }
                for model in models
            ],
        }
    )
    return settings


def load_model_lineage(
    settings: dict[str, Any],
    signal_date: str,
    open_dates: Sequence[str],
) -> list[dict[str, Any]]:
    lineage: list[dict[str, Any]] = []
    sessions = sorted({_normalise_date(value) for value in open_dates})
    for model in settings["models"]:
        model_dir = Path(model["resolved_model_dir"])
        try:
            meta = json.loads((model_dir / "meta.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InferenceContractError(
                f"cannot read model meta for {model['tag']}: {exc}"
            ) from exc

        train_start = _normalise_optional_date(meta.get("train_start"))
        train_end = _normalise_optional_date(meta.get("train_end"))
        if not train_start or not train_end:
            raise InferenceContractError(
                f"model {model['tag']} meta lacks train_start/train_end"
            )
        if train_start not in sessions or train_end not in sessions:
            raise InferenceContractError(
                f"model {model['tag']} train window must use open trading sessions"
            )
        expected = {
            "model_hash": model["model_hash"],
            "feature_list_id": settings["feature_list_id"],
            "label_id": model["label_id"],
            "universe": settings["universe"],
        }
        for field, expected_value in expected.items():
            meta_value = str(meta.get(field) or "")
            if meta_value != expected_value:
                raise InferenceContractError(
                    f"model {model['tag']} meta mismatch for {field}: "
                    f"expected={expected_value}, got={meta_value!r}"
                )
        try:
            meta_horizon = int(meta.get("horizon"))
        except (TypeError, ValueError) as exc:
            raise InferenceContractError(
                f"model {model['tag']} meta lacks a valid horizon"
            ) from exc
        if meta_horizon != model["horizon"]:
            raise InferenceContractError(f"model {model['tag']} meta horizon mismatch")
        matured_sessions = [day for day in sessions if train_end < day <= signal_date]
        if len(matured_sessions) < model["horizon"]:
            raise InferenceContractError(
                f"model {model['tag']} labels are not mature for {signal_date}: "
                f"need={model['horizon']} sessions, observed={len(matured_sessions)}"
            )
        lineage.append(
            {
                "tag": model["tag"],
                "weight": model["weight"],
                "horizon": model["horizon"],
                "model_hash": model["model_hash"],
                "model_dir": model["model_dir"],
                "artifact_sha256": model["artifact_sha256"],
                "label_id": model["label_id"],
                "feature_list_id": settings["feature_list_id"],
                "train_start": train_start,
                "train_end": train_end,
                "maturity_sessions": len(matured_sessions),
            }
        )
    return lineage


def _load_stock_metadata(project_root: Path) -> dict[str, dict[str, Any]]:
    db_path = project_root / "data" / "meta.db"
    if not db_path.exists():
        return {}
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            rows = conn.execute(
                "SELECT ts_code, name, industry, list_date FROM stock_basic"
            ).fetchall()
    except sqlite3.Error:
        return {}

    result: dict[str, dict[str, Any]] = {}
    for ts_code, name, industry, list_date in rows:
        digits = "".join(char for char in str(ts_code) if char.isdigit())
        if digits:
            try:
                normalised_list_date = _normalise_optional_date(list_date)
            except InferenceContractError:
                normalised_list_date = None
            result[digits] = {
                "name": str(name or ""),
                "industry": str(industry or ""),
                "list_date": normalised_list_date,
            }
    return result


def _git_state(project_root: Path) -> dict[str, Any]:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=project_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        return {"commit": sha, "dirty": dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": "unavailable", "dirty": None}


def _data_latest(project_root: Path) -> str | None:
    db_path = project_root / "data" / "meta.db"
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            row = conn.execute("SELECT MAX(latest_date) FROM data_latest").fetchone()
        return _normalise_optional_date(row[0] if row else None)
    except sqlite3.Error:
        return None


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"inference artifact already exists: {path}")
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        try:
            os.link(temp_path, path)
        except FileExistsError as exc:
            raise FileExistsError(f"inference artifact already exists: {path}") from exc
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _score_stats(values: Iterable[float]) -> dict[str, float]:
    import numpy as np

    array = np.asarray(list(values), dtype=float)
    return {
        "min": round(float(array.min()), 6),
        "max": round(float(array.max()), 6),
        "mean": round(float(array.mean()), 6),
        "p10": round(float(np.percentile(array, 10)), 6),
        "p50": round(float(np.percentile(array, 50)), 6),
        "p90": round(float(np.percentile(array, 90)), 6),
    }


def run_candidate_inference(
    *,
    strategy_id: str,
    strategy_config: dict[str, Any],
    project_root: Path,
    signal_date: str | None = "auto",
    execution_date: str | None = None,
    top_k: int | None = None,
    output_root: Path | None = None,
    now: datetime | None = None,
) -> InferenceRunResult:
    """Run a pinned model blend and write one immutable CandidateRun JSON."""

    import lightgbm as lgb
    import numpy as np
    import pandas as pd

    from qsys.data.adapter import QlibAdapter
    from qsys.feature.registry import FeatureListRegistry
    from qsys.signal.alpha_v1.labels import robust_zscore_transform

    project_root = Path(project_root).resolve()
    settings = validate_inference_config(strategy_id, strategy_config, project_root)
    open_dates = load_open_dates(project_root)
    dates = resolve_inference_dates(
        signal_date,
        execution_date,
        open_dates,
        now=now,
        market_close_cutoff=settings["market_close_cutoff"],
    )
    model_lineage = load_model_lineage(settings, dates.signal_date, open_dates)

    adapter = QlibAdapter()
    adapter.init_qlib()
    qlib_latest_raw = adapter.get_last_qlib_date()
    qlib_latest = _normalise_optional_date(qlib_latest_raw)
    raw_latest = _data_latest(project_root)
    for layer, latest in (("canonical", raw_latest), ("qlib", qlib_latest)):
        if not latest or latest < dates.signal_date:
            raise InferenceContractError(
                f"{layer} data is stale for signal_date={dates.signal_date}: latest={latest}"
            )

    features = list(FeatureListRegistry.load(settings["feature_list_id"]))
    if not features or len(features) != len(set(features)):
        raise InferenceContractError(
            "feature list must be non-empty and contain no duplicates"
        )
    feature_list_hash = _canonical_hash(features)
    market_fields = ["$close", "$volume", "$amount"]
    requested_fields = features + [
        field for field in market_fields if field not in features
    ]
    raw = adapter.get_features(
        settings["universe"],
        requested_fields,
        start_time=dates.data_date,
        end_time=dates.data_date,
    )
    if raw is None or raw.empty:
        raise InferenceContractError(
            f"no features for universe={settings['universe']} data_date={dates.data_date}"
        )

    frame = raw.reset_index()
    if "datetime" in frame.columns:
        frame = frame.rename(columns={"datetime": "data_date"})
    if "instrument" not in frame.columns or "data_date" not in frame.columns:
        raise InferenceContractError(
            "qlib feature frame lacks instrument/datetime index columns"
        )
    frame = frame.loc[:, ~frame.columns.duplicated()].copy()
    frame["data_date"] = frame["data_date"].map(_normalise_date)
    observed_dates = sorted(frame["data_date"].dropna().unique().tolist())
    if observed_dates != [dates.data_date]:
        raise InferenceContractError(
            f"feature snapshot date mismatch: expected={dates.data_date}, observed={observed_dates}"
        )
    if frame["instrument"].duplicated().any():
        raise InferenceContractError(
            "feature snapshot contains duplicate instrument rows"
        )
    missing_columns = [
        field for field in requested_fields if field not in frame.columns
    ]
    if missing_columns:
        raise InferenceContractError(
            f"feature snapshot lacks required columns: {missing_columns}"
        )

    frame["ts_code"] = frame["instrument"].astype(str)
    frame = frame.sort_values("ts_code").reset_index(drop=True)
    universe_size = len(frame)
    if universe_size < settings["min_universe_size"]:
        raise InferenceContractError(
            f"universe coverage too small: rows={universe_size}, "
            f"minimum={settings['min_universe_size']}"
        )

    feature_coverage = frame[features].notna().mean(axis=1)
    global_feature_coverage = float(frame[features].notna().mean().mean())
    if global_feature_coverage < settings["min_global_feature_coverage"]:
        raise InferenceContractError(
            f"global feature coverage {global_feature_coverage:.2%} is below "
            f"{settings['min_global_feature_coverage']:.2%}"
        )

    metadata = _load_stock_metadata(project_root)
    drop_reasons: dict[str, int] = {}
    eligibility: list[bool] = []
    row_reasons: list[list[str]] = []
    names: list[str] = []
    industries: list[str] = []
    listed_days_values: list[int | None] = []
    signal_dt = date.fromisoformat(dates.signal_date)

    for index, row in frame.iterrows():
        reasons: list[str] = []
        coverage = float(feature_coverage.iloc[index])
        if coverage < settings["min_feature_coverage"]:
            reasons.append("insufficient_feature_coverage")
        if pd.isna(row["$close"]) or float(row["$close"]) <= 0:
            reasons.append("missing_or_nonpositive_close")
        if pd.isna(row["$volume"]) or float(row["$volume"]) <= 0:
            reasons.append("suspended_or_zero_volume")
        if settings["min_amount"] > 0 and (
            pd.isna(row["$amount"]) or float(row["$amount"]) < settings["min_amount"]
        ):
            reasons.append("below_minimum_amount")

        digits = "".join(char for char in str(row["ts_code"]) if char.isdigit())
        stock_meta = metadata.get(digits, {})
        name = str(stock_meta.get("name") or "")
        industry = str(stock_meta.get("industry") or "")
        list_date = stock_meta.get("list_date")
        listed_days: int | None = None
        if not name or not list_date:
            reasons.append("missing_stock_metadata")
        else:
            listed_days = (signal_dt - date.fromisoformat(list_date)).days
            if listed_days < settings["min_listed_days"]:
                reasons.append("listed_too_recently")
        if name and any(
            str(pattern).upper() in name.upper()
            for pattern in settings["exclude_name_patterns"]
        ):
            reasons.append("risk_designation")

        for reason in set(reasons):
            drop_reasons[reason] = drop_reasons.get(reason, 0) + 1
        eligibility.append(not reasons)
        row_reasons.append(reasons)
        names.append(name)
        industries.append(industry)
        listed_days_values.append(listed_days)

    frame["feature_coverage"] = feature_coverage
    frame["eligibility_reasons"] = row_reasons
    frame["name"] = names
    frame["industry"] = industries
    frame["listed_days"] = listed_days_values
    eligible = frame.loc[eligibility].copy().reset_index(drop=True)
    if len(eligible) < settings["min_eligible_size"]:
        raise InferenceContractError(
            f"eligible universe too small: rows={len(eligible)}, "
            f"minimum={settings['min_eligible_size']}, drops={drop_reasons}"
        )

    scores: dict[str, pd.Series] = {}
    ranks: dict[str, pd.Series] = {}
    # Match the training contract in lightgbm_single_label.py: missing raw
    # features are zero-filled before applying the persisted robust scaler.
    # Coverage gates above prevent this compatibility rule from hiding broadly
    # incomplete rows.
    X = eligible[features].fillna(0.0).astype(np.float32)
    for model_spec in settings["models"]:
        model_dir = Path(model_spec["resolved_model_dir"])
        model = lgb.Booster(model_file=str(model_dir / "model.txt"))
        center = pd.read_json(model_dir / "center.json", typ="series")
        scale = pd.read_json(model_dir / "scale.json", typ="series")
        if set(center.index) != set(features) or set(scale.index) != set(features):
            raise InferenceContractError(
                f"model {model_spec['tag']} scaler feature set differs from configured feature list"
            )
        X_scaled = robust_zscore_transform(
            X, center.reindex(features), scale.reindex(features)
        )
        prediction = np.asarray(model.predict(X_scaled[features].values), dtype=float)
        if len(prediction) != len(eligible) or not np.isfinite(prediction).all():
            raise InferenceContractError(
                f"model {model_spec['tag']} produced invalid predictions"
            )
        std = float(prediction.std())
        if std < 1e-12:
            raise InferenceContractError(
                f"model {model_spec['tag']} predictions have zero variance"
            )
        normalised = (prediction - float(prediction.mean())) / std
        series = pd.Series(normalised, index=eligible["ts_code"], dtype=float)
        scores[model_spec["tag"]] = series
        ranks[model_spec["tag"]] = series.rank(method="first", ascending=False).astype(
            int
        )

    blended = pd.Series(0.0, index=eligible["ts_code"], dtype=float)
    for model_spec in settings["models"]:
        blended = blended.add(
            scores[model_spec["tag"]] * model_spec["weight"], fill_value=0.0
        )
    ranking = pd.DataFrame(
        {"ts_code": eligible["ts_code"], "ranking_score": blended.values}
    )
    ranking = ranking.sort_values(
        ["ranking_score", "ts_code"], ascending=[False, True], kind="mergesort"
    ).reset_index(drop=True)
    ranking["rank"] = ranking.index + 1
    selected_top_k = int(top_k if top_k is not None else settings["top_k"])
    if selected_top_k <= 0 or selected_top_k > len(ranking):
        raise InferenceContractError(
            f"top_k must be between 1 and eligible universe size {len(ranking)}"
        )
    top = ranking.head(selected_top_k)
    eligible_by_code = eligible.set_index("ts_code", drop=False)

    candidates: list[dict[str, Any]] = []
    model_tags = [model["tag"] for model in settings["models"]]
    for row in top.itertuples(index=False):
        stock = eligible_by_code.loc[row.ts_code]
        model_rows = []
        model_rank_values = []
        for model_spec in settings["models"]:
            tag = model_spec["tag"]
            model_rank = int(ranks[tag].loc[row.ts_code])
            model_rank_values.append(model_rank)
            model_rows.append(
                {
                    "tag": tag,
                    "weight": model_spec["weight"],
                    "score": round(float(scores[tag].loc[row.ts_code]), 6),
                    "rank": model_rank,
                }
            )
        candidates.append(
            {
                "ts_code": row.ts_code,
                "name": stock["name"],
                "industry": stock["industry"],
                "rank": int(row.rank),
                "ranking_score": round(float(row.ranking_score), 6),
                "model_rank_gap": max(model_rank_values) - min(model_rank_values),
                "feature_coverage": round(float(stock["feature_coverage"]), 6),
                "listed_days": (
                    int(stock["listed_days"])
                    if pd.notna(stock["listed_days"])
                    else None
                ),
                "eligibility": {"passed": True, "reasons": []},
                "models": model_rows,
                "data_date": dates.data_date,
                "signal_date": dates.signal_date,
                "execution_date": dates.execution_date,
                "strategy_id": strategy_id,
            }
        )

    candidate_hash = compute_candidate_hash(candidates)
    created = now or datetime.now(timezone.utc)
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    created_utc = created.astimezone(timezone.utc).replace(microsecond=0)
    created_at = created_utc.isoformat().replace("+00:00", "Z")
    run_id = (
        f"infer_{strategy_id}_{dates.signal_date.replace('-', '')}_"
        f"{created_utc.strftime('%Y%m%dT%H%M%SZ')}_{candidate_hash[:8]}"
    )
    for candidate in candidates:
        candidate["run_id"] = run_id

    config_hash = _canonical_hash(strategy_config)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "candidate_run",
        "run_id": run_id,
        "strategy_id": strategy_id,
        "lifecycle_stage": str(strategy_config.get("stage") or "research"),
        "usage": "human_research_only",
        "created_at": created_at,
        "signal_date": dates.signal_date,
        "data_date": dates.data_date,
        "execution_date": dates.execution_date,
        "date_contract": {
            "mode": "postclose_for_next_open_session",
            "market_close_cutoff": settings["market_close_cutoff"],
            "expected_completed_date": dates.expected_completed_date,
            "execution_rule": "next_open_session",
            "calendar_source": "data/meta.db:trade_cal",
        },
        "universe": settings["universe"],
        "universe_snapshot_semantics": settings["universe_snapshot_semantics"],
        "top_k": selected_top_k,
        "candidate_count": len(candidates),
        "candidate_hash": candidate_hash,
        "config_hash": config_hash,
        "feature_list_id": settings["feature_list_id"],
        "feature_list_hash": feature_list_hash,
        "source": {
            "engine": settings["engine"],
            "model_bundle_id": settings["bundle_id"],
            "model_bundle_hash": settings["bundle_hash"],
            "feature_list_id": settings["feature_list_id"],
            "models": model_lineage,
            "git": _git_state(project_root),
        },
        "blend": {
            "score_transform": settings["score_transform"],
            "weights": {model["tag"]: model["weight"] for model in settings["models"]},
            "model_tags": model_tags,
            "ranking_score_stats": _score_stats(ranking["ranking_score"]),
        },
        "data_quality": {
            "status": "pass",
            "raw_latest": raw_latest,
            "qlib_latest": qlib_latest,
            "feature_snapshot_date": dates.data_date,
            "universe_rows": universe_size,
            "eligible_rows": len(eligible),
            "dropped_rows": universe_size - len(eligible),
            "drop_reasons": dict(sorted(drop_reasons.items())),
            "global_feature_coverage": round(global_feature_coverage, 6),
            "min_feature_coverage": settings["min_feature_coverage"],
            "min_global_feature_coverage": settings["min_global_feature_coverage"],
        },
        "candidates": candidates,
    }

    base_output = output_root or (project_root / settings["output_root"])
    artifact_path = (
        Path(base_output)
        / dates.signal_date
        / strategy_id
        / run_id
        / "candidate_run.json"
    )
    _atomic_write_json(artifact_path, payload)
    return InferenceRunResult(artifact_path=artifact_path, payload=payload)
