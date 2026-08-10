"""Deterministic, artifact-only inference for explicitly pinned model blends.

This module implements ``UC_DAILY_INFERENCE_RUN``.  It deliberately does not
import or call ``DailyRunner``: inference produces a research candidate
artifact only and must never mutate broker, trader, ledger, or account state.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import subprocess
import uuid
from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from qsys.feature.availability import (
    normalise_feature_availability,
    resolve_lagged_open_session,
)


class InferenceContractError(RuntimeError):
    """Raised when inference inputs fail a safety or provenance contract."""


@dataclass(frozen=True)
class InferenceDates:
    """Resolved aligned feature, decision, and next-session execution dates."""

    signal_date: str
    data_date: str
    decision_date: str
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


def compute_universe_hash(instruments: Iterable[Any]) -> str:
    """Hash an exact, order-independent universe membership snapshot."""

    members = sorted(str(instrument) for instrument in instruments)
    if len(members) != len(set(members)):
        raise InferenceContractError("universe snapshot contains duplicate instruments")
    return _canonical_hash({"serialization": "sorted-string-list-v1", "members": members})


def load_universe_snapshot_members(
    project_root: Path,
    universe: str,
    signal_date: str,
) -> list[str]:
    """Load the exact Qlib universe membership active on *signal_date*."""

    snapshot_path = (
        Path(project_root) / "data" / "qlib_bin" / "instruments" / f"{universe}.txt"
    )
    if not snapshot_path.is_file():
        raise InferenceContractError(
            f"universe snapshot file is missing: {snapshot_path}"
        )
    resolved_signal = _normalise_date(signal_date)
    members: list[str] = []
    for line_number, line in enumerate(
        snapshot_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 3:
            raise InferenceContractError(
                f"invalid universe snapshot row at {snapshot_path}:{line_number}"
            )
        instrument, start_date, end_date = fields
        try:
            active = (
                _normalise_date(start_date)
                <= resolved_signal
                <= _normalise_date(end_date)
            )
        except InferenceContractError as exc:
            raise InferenceContractError(
                f"invalid universe snapshot dates at {snapshot_path}:{line_number}"
            ) from exc
        if active:
            members.append(instrument.strip())
    if not members:
        raise InferenceContractError(
            f"universe snapshot has no active members for {resolved_signal}: {snapshot_path}"
        )
    compute_universe_hash(members)
    return sorted(members)


def _canonical_feature_value(value: Any) -> str | None:
    """Canonicalise one numeric feature value without lossy decimal rounding."""

    if value is None or type(value).__name__ == "NAType":
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise InferenceContractError(
            f"feature snapshot contains non-numeric value: {value!r}"
        ) from exc
    if math.isnan(numeric):
        return None
    if not math.isfinite(numeric):
        raise InferenceContractError(
            f"feature snapshot contains non-finite value: {value!r}"
        )
    if numeric == 0.0:
        numeric = 0.0
    return numeric.hex()


def compute_feature_snapshot_hash(frame: Any, features: Sequence[str]) -> str:
    """Hash exact instrument/feature/value inputs using canonical JSON.

    Values are encoded with ``float.hex`` so the digest does not depend on
    display precision, locale, dataframe row order, or JSON float rendering.
    Missing values are represented as JSON ``null``.
    """

    feature_names = [str(feature) for feature in features]
    if not feature_names or len(feature_names) != len(set(feature_names)):
        raise InferenceContractError(
            "feature snapshot hash requires a unique, non-empty feature list"
        )
    required = {"instrument", *feature_names}
    missing_columns = sorted(required - set(frame.columns))
    if missing_columns:
        raise InferenceContractError(
            f"feature snapshot hash lacks columns: {missing_columns}"
        )
    ordered = frame[["instrument", *feature_names]].sort_values(
        "instrument", kind="mergesort"
    )
    if ordered["instrument"].duplicated().any():
        raise InferenceContractError(
            "feature snapshot hash contains duplicate instruments"
        )
    rows = [
        [
            str(row[0]),
            [_canonical_feature_value(value) for value in row[1:]],
        ]
        for row in ordered.itertuples(index=False, name=None)
    ]
    return _canonical_hash(
        {
            "serialization": "instrument-feature-floathex-v1",
            "features": feature_names,
            "rows": rows,
        }
    )


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


def resolve_expected_completed_session(
    open_dates: Sequence[str],
    *,
    now: datetime | None = None,
    market_close_cutoff: str = "18:00",
) -> str:
    """Return the most recent completed open session at *now* in China time."""

    sessions = sorted({_normalise_date(value) for value in open_dates})
    if not sessions:
        raise InferenceContractError(
            "cannot resolve a completed session without an open-session calendar"
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
    anchor_text = anchor.isoformat()
    completed = [value for value in sessions if value <= anchor_text]
    if not completed:
        raise InferenceContractError(
            f"calendar has no completed session on or before {anchor_text}"
        )
    return completed[-1]


def resolve_next_open_session(signal_date: str, open_dates: Sequence[str]) -> str:
    """Return the first open session strictly after *signal_date*."""

    resolved_signal = _normalise_date(signal_date)
    sessions = sorted({_normalise_date(value) for value in open_dates})
    if resolved_signal not in sessions:
        raise InferenceContractError(
            f"signal_date is not an open trading session: {resolved_signal}"
        )
    following = [value for value in sessions if value > resolved_signal]
    if not following:
        raise InferenceContractError(
            f"calendar has no execution session after signal_date={resolved_signal}"
        )
    return following[0]


def validate_label_maturity(
    *,
    train_end: str,
    signal_date: str,
    horizon: int,
    open_dates: Sequence[str],
) -> int:
    """Validate trading-session label maturity and return observed sessions."""

    sessions = sorted({_normalise_date(value) for value in open_dates})
    resolved_train_end = _normalise_date(train_end)
    resolved_signal = _normalise_date(signal_date)
    if resolved_train_end not in sessions:
        raise InferenceContractError(
            f"train_end is not an open trading session: {resolved_train_end}"
        )
    if resolved_signal not in sessions:
        raise InferenceContractError(
            f"signal_date is not an open trading session: {resolved_signal}"
        )
    if not isinstance(horizon, int) or isinstance(horizon, bool) or horizon <= 0:
        raise InferenceContractError(f"label horizon must be positive, got {horizon!r}")
    matured_sessions = [
        day for day in sessions if resolved_train_end < day <= resolved_signal
    ]
    observed = len(matured_sessions)
    if observed < horizon:
        raise InferenceContractError(
            f"labels are not mature for {resolved_signal}: "
            f"train_end={resolved_train_end}, need={horizon} sessions, observed={observed}"
        )
    return observed


def resolve_inference_dates(
    signal_date: str | None,
    execution_date: str | None,
    open_dates: Sequence[str],
    *,
    now: datetime | None = None,
    market_close_cutoff: str = "18:00",
    universe_snapshot_semantics: str = "current_constituents_snapshot",
    feature_snapshot_lag_sessions: int = 0,
) -> InferenceDates:
    """Resolve a bounded, aligned feature snapshot for the next decision open.

    ``expected_completed`` is the decision session known at the captured run
    anchor.  ``feature_snapshot_lag_sessions`` moves *all* model inputs back by
    an exact number of open sessions.  This is intentionally distinct from a
    source-specific feature lag: the returned ``signal_date`` and ``data_date``
    always describe one aligned snapshot.
    """

    sessions = sorted({_normalise_date(value) for value in open_dates})
    if not sessions:
        raise InferenceContractError(
            "cannot resolve inference dates without an open-session calendar"
        )

    expected_completed = resolve_expected_completed_session(
        sessions,
        now=now,
        market_close_cutoff=market_close_cutoff,
    )

    if (
        not isinstance(feature_snapshot_lag_sessions, int)
        or isinstance(feature_snapshot_lag_sessions, bool)
        or feature_snapshot_lag_sessions < 0
    ):
        raise InferenceContractError(
            "feature_snapshot_lag_sessions must be a non-negative integer"
        )
    try:
        expected_signal = resolve_lagged_open_session(
            expected_completed,
            sessions,
            feature_snapshot_lag_sessions,
        )
    except ValueError as exc:
        raise InferenceContractError(str(exc)) from exc

    requested = (signal_date or "auto").strip().lower()
    resolved_signal = (
        expected_signal if requested == "auto" else _normalise_date(requested)
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
    if resolved_signal != expected_signal:
        raise InferenceContractError(
            "signal_date must equal the configured aligned feature session: "
            f"expected={expected_signal}, got={resolved_signal}, "
            f"decision_date={expected_completed}, "
            f"feature_snapshot_lag_sessions={feature_snapshot_lag_sessions}"
        )
    if universe_snapshot_semantics != "current_constituents_snapshot":
        raise InferenceContractError(
            "historical PIT inference is unavailable: no "
            "pit_constituents_snapshot provider is implemented"
        )

    expected_execution = resolve_next_open_session(expected_completed, sessions)
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
        decision_date=expected_completed,
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
    try:
        feature_availability = normalise_feature_availability(
            strategy_config.get("feature_availability")
        )
    except ValueError as exc:
        raise InferenceContractError(str(exc)) from exc

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
        "max_feature_missing_ratio": float(
            inference.get("max_feature_missing_ratio", 0.95)
        ),
        "min_model_used_feature_unique_values": int(
            inference.get("min_model_used_feature_unique_values", 2)
        ),
        "min_listed_days": int(inference.get("min_listed_days", 180)),
        "min_amount": float(inference.get("min_amount", 0.0)),
        "exclude_name_patterns": [str(item) for item in exclude_name_patterns],
        "market_close_cutoff": str(inference.get("market_close_cutoff", "18:00")),
        "feature_snapshot_lag_sessions": int(
            inference.get("feature_snapshot_lag_sessions", 0)
        ),
        "output_root": str(inference.get("output_root", "outputs")),
        "universe_snapshot_semantics": str(
            inference.get(
                "universe_snapshot_semantics", "current_constituents_snapshot"
            )
        ),
        "feature_availability": feature_availability,
    }
    if settings["top_k"] <= 0:
        raise InferenceContractError("inference.top_k must be positive")
    if settings["feature_snapshot_lag_sessions"] < 0:
        raise InferenceContractError(
            "inference.feature_snapshot_lag_sessions must be non-negative"
        )
    if settings["score_transform"] != "daily_cs_zscore_unclipped_ddof0":
        raise InferenceContractError(
            "inference.score_transform must be daily_cs_zscore_unclipped_ddof0"
        )
    if settings["output_root"] != "outputs":
        raise InferenceContractError("inference.output_root must be canonical outputs")
    if settings["universe_snapshot_semantics"] != "current_constituents_snapshot":
        raise InferenceContractError(
            "pinned_model_blend_v1 only supports current_constituents_snapshot; "
            "the PIT universe provider is not implemented"
        )
    for key in (
        "min_universe_size",
        "min_eligible_size",
        "min_model_used_feature_unique_values",
    ):
        if settings[key] <= 0:
            raise InferenceContractError(f"inference.{key} must be positive")
    for key in ("min_listed_days", "min_amount"):
        if settings[key] < 0:
            raise InferenceContractError(f"inference.{key} must be non-negative")
    for key in ("min_feature_coverage", "min_global_feature_coverage"):
        if not 0.0 <= settings[key] <= 1.0:
            raise InferenceContractError(f"inference.{key} must be between 0 and 1")
    if not 0.0 <= settings["max_feature_missing_ratio"] < 1.0:
        raise InferenceContractError(
            "inference.max_feature_missing_ratio must be between 0 (inclusive) "
            "and 1 (exclusive)"
        )
    settings["bundle_hash"] = _canonical_hash(
        {
            "bundle_id": bundle_id,
            "feature_list_id": feature_list_id,
            "feature_availability": feature_availability,
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
        try:
            model_feature_availability = normalise_feature_availability(
                meta.get("feature_availability")
            )
        except ValueError as exc:
            raise InferenceContractError(
                f"model {model['tag']} has invalid feature availability: {exc}"
            ) from exc
        if model_feature_availability != settings["feature_availability"]:
            raise InferenceContractError(
                f"model {model['tag']} feature availability differs from config"
            )
        try:
            maturity_sessions = validate_label_maturity(
                train_end=train_end,
                signal_date=signal_date,
                horizon=model["horizon"],
                open_dates=sessions,
            )
        except InferenceContractError as exc:
            raise InferenceContractError(
                f"model {model['tag']} {exc}"
            ) from exc
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
                "maturity_sessions": maturity_sessions,
                "feature_availability": model_feature_availability,
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


@contextmanager
def _project_working_directory(project_root: Path):
    """Resolve legacy relative feature resources against the project root.

    The canonical CLI may be invoked from any directory, while a few existing
    PIT sidecar loaders still accept project-relative paths.  Keep the scope
    narrow and always restore the caller's working directory.
    """

    previous = Path.cwd()
    target = Path(project_root).resolve()
    os.chdir(target)
    try:
        yield
    finally:
        os.chdir(previous)


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


def _load_runtime_models(
    model_specs: Sequence[dict[str, Any]], features: Sequence[str]
) -> tuple[dict[str, Any], dict[str, tuple[Any, Any]], set[str]]:
    """Load pinned LightGBM models and identify features used by any split."""

    import lightgbm as lgb
    import pandas as pd

    loaded: dict[str, Any] = {}
    scalers: dict[str, tuple[Any, Any]] = {}
    used_features: set[str] = set()
    for model_spec in model_specs:
        model_dir = Path(model_spec["resolved_model_dir"])
        model = lgb.Booster(model_file=str(model_dir / "model.txt"))
        if model.num_feature() != len(features):
            raise InferenceContractError(
                f"model {model_spec['tag']} expects {model.num_feature()} features, "
                f"configured list has {len(features)}"
            )
        center = pd.read_json(model_dir / "center.json", typ="series")
        scale = pd.read_json(model_dir / "scale.json", typ="series")
        validate_ordered_model_features(
            model_spec["tag"],
            features,
            center.index,
            scale.index,
        )
        split_importance = model.feature_importance(importance_type="split")
        used_features.update(
            feature
            for feature, split_count in zip(features, split_importance, strict=True)
            if int(split_count) > 0
        )
        loaded[model_spec["tag"]] = model
        scalers[model_spec["tag"]] = (center, scale)
    return loaded, scalers, used_features


def validate_ordered_model_features(
    model_tag: str,
    features: Sequence[str],
    center_index: Iterable[Any],
    scale_index: Iterable[Any],
) -> None:
    """Require pinned scaler indices to match the positional model input."""

    expected = [str(feature) for feature in features]
    for artifact_name, index in (
        ("center.json", center_index),
        ("scale.json", scale_index),
    ):
        observed = [str(feature) for feature in index]
        if observed != expected:
            mismatch = next(
                (
                    position,
                    expected[position] if position < len(expected) else None,
                    observed[position] if position < len(observed) else None,
                )
                for position in range(max(len(expected), len(observed)))
                if position >= len(expected)
                or position >= len(observed)
                or expected[position] != observed[position]
            )
            raise InferenceContractError(
                f"model {model_tag} ordered feature contract differs in "
                f"{artifact_name}: position={mismatch[0]}, "
                f"expected={mismatch[1]!r}, observed={mismatch[2]!r}"
            )


def validate_training_feature_lineage(
    model_specs: Sequence[dict[str, Any]], features: Sequence[str]
) -> None:
    """Validate schema-v2 training metadata against the live ordered list.

    Legacy schema-v1 bundles predate this provenance field and remain readable
    while explicitly pinned.  Every newly trained schema-v2 bundle must carry
    both the ordered list and its canonical hash; changing YAML order with the
    same set of names therefore fails before model prediction.
    """

    expected = [str(feature) for feature in features]
    expected_hash = _canonical_hash(expected)
    for model_spec in model_specs:
        meta_path = Path(model_spec["resolved_model_dir"]) / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            schema_version = int(meta.get("schema_version", 1))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise InferenceContractError(
                f"cannot validate training feature lineage for "
                f"{model_spec['tag']}: {exc}"
            ) from exc
        if schema_version < 2:
            continue
        observed = [str(feature) for feature in meta.get("ordered_features", [])]
        observed_hash = str(meta.get("feature_list_hash") or "")
        if observed != expected or observed_hash != expected_hash:
            raise InferenceContractError(
                f"model {model_spec['tag']} training ordered feature lineage "
                "differs from configured feature list"
            )


def profile_feature_quality(
    frame: Any,
    features: Sequence[str],
    model_used_features: Iterable[str],
    *,
    max_missing_ratio: float,
    min_model_used_unique_values: int,
) -> dict[str, Any]:
    """Profile feature health and return explicit fail-closed issue lists."""

    import numpy as np
    import pandas as pd

    missing_ratio: dict[str, float] = {}
    unique_non_null: dict[str, int] = {}
    invalid_numeric: list[str] = []
    for feature in features:
        numeric = pd.to_numeric(frame[feature], errors="coerce")
        non_numeric = frame[feature].notna() & numeric.isna()
        non_finite = numeric.notna() & ~np.isfinite(numeric)
        if bool(non_numeric.any() or non_finite.any()):
            invalid_numeric.append(str(feature))
        clean = numeric.mask(non_finite)
        missing_ratio[str(feature)] = float(clean.isna().mean())
        unique_non_null[str(feature)] = int(clean.nunique(dropna=True))

    excessive_missing = sorted(
        feature
        for feature, ratio in missing_ratio.items()
        if ratio > max_missing_ratio
    )
    constant_model_used = sorted(
        str(feature)
        for feature in model_used_features
        if unique_non_null[str(feature)] < min_model_used_unique_values
    )
    return {
        "feature_missing_ratio": missing_ratio,
        "feature_unique_non_null": unique_non_null,
        "invalid_numeric_features": sorted(invalid_numeric),
        "excessive_missing_features": excessive_missing,
        "constant_model_used_features": constant_model_used,
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

    run_anchor_time = now if now is not None else datetime.now(timezone.utc)
    if run_anchor_time.tzinfo is None or run_anchor_time.utcoffset() is None:
        run_anchor_time = run_anchor_time.replace(tzinfo=ZoneInfo("Asia/Shanghai"))

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
        now=run_anchor_time,
        market_close_cutoff=settings["market_close_cutoff"],
        universe_snapshot_semantics=settings["universe_snapshot_semantics"],
        feature_snapshot_lag_sessions=settings["feature_snapshot_lag_sessions"],
    )
    model_lineage = load_model_lineage(settings, dates.signal_date, open_dates)
    margin_lag_sessions = settings["feature_availability"]["margin"][
        "lag_sessions"
    ]
    margin_asof_date = resolve_lagged_open_session(
        dates.signal_date,
        open_dates,
        margin_lag_sessions,
    )

    adapter = QlibAdapter(
        qlib_dir=project_root / "data" / "qlib_bin",
        raw_dir=project_root / "data" / "canonical" / "daily",
    )
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
    validate_training_feature_lineage(settings["models"], features)
    market_fields = ["$close", "$volume", "$amount"]
    requested_fields = features + [
        field for field in market_fields if field not in features
    ]
    universe_members = load_universe_snapshot_members(
        project_root,
        settings["universe"],
        dates.decision_date,
    )
    universe_hash = compute_universe_hash(universe_members)
    with _project_working_directory(project_root):
        raw = adapter.get_features(
            settings["universe"],
            requested_fields,
            start_time=dates.data_date,
            end_time=dates.data_date,
            margin_lag_sessions=margin_lag_sessions,
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
    observed_members = sorted(frame["instrument"].astype(str).tolist())
    if observed_members != universe_members:
        expected = set(universe_members)
        observed = set(observed_members)
        raise InferenceContractError(
            "feature snapshot membership differs from universe snapshot: "
            f"missing={sorted(expected - observed)[:20]}, "
            f"unexpected={sorted(observed - expected)[:20]}, "
            f"expected_count={len(expected)}, observed_count={len(observed)}"
        )
    if universe_size < settings["min_universe_size"]:
        raise InferenceContractError(
            f"universe coverage too small: rows={universe_size}, "
            f"minimum={settings['min_universe_size']}"
        )

    feature_snapshot_hash = compute_feature_snapshot_hash(frame, features)
    loaded_models, loaded_scalers, model_used_features = _load_runtime_models(
        settings["models"], features
    )
    for model in model_lineage:
        model["ordered_feature_list_hash"] = feature_list_hash

    feature_quality = profile_feature_quality(
        frame,
        features,
        model_used_features,
        max_missing_ratio=settings["max_feature_missing_ratio"],
        min_model_used_unique_values=settings[
            "min_model_used_feature_unique_values"
        ],
    )
    feature_missing_ratio = feature_quality["feature_missing_ratio"]
    feature_unique_non_null = feature_quality["feature_unique_non_null"]
    invalid_numeric_features = feature_quality["invalid_numeric_features"]
    excessive_missing_features = feature_quality["excessive_missing_features"]
    constant_model_used_features = feature_quality[
        "constant_model_used_features"
    ]
    quality_violations: list[str] = []
    if invalid_numeric_features:
        quality_violations.append(
            f"non-numeric/non-finite={sorted(invalid_numeric_features)}"
        )
    if excessive_missing_features:
        quality_violations.append(
            f"missing_ratio>{settings['max_feature_missing_ratio']:.2%}="
            f"{excessive_missing_features}"
        )
    if constant_model_used_features:
        quality_violations.append(
            "model-used constant features=" f"{constant_model_used_features}"
        )
    if quality_violations:
        raise InferenceContractError(
            "feature-level readiness failed: " + "; ".join(quality_violations)
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
        model = loaded_models[model_spec["tag"]]
        center, scale = loaded_scalers[model_spec["tag"]]
        X_scaled = robust_zscore_transform(X, center, scale)
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
                "decision_date": dates.decision_date,
                "execution_date": dates.execution_date,
                "strategy_id": strategy_id,
            }
        )

    candidate_hash = compute_candidate_hash(candidates)
    created_utc = run_anchor_time.astimezone(timezone.utc).replace(microsecond=0)
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
        "decision_date": dates.decision_date,
        "execution_date": dates.execution_date,
        "date_contract": {
            "mode": "aligned_feature_snapshot_for_next_open_session",
            "run_anchor_at": created_at,
            "market_close_cutoff": settings["market_close_cutoff"],
            "expected_completed_date": dates.expected_completed_date,
            "feature_snapshot_lag_sessions": settings[
                "feature_snapshot_lag_sessions"
            ],
            "execution_rule": "next_open_session",
            "calendar_source": "data/meta.db:trade_cal",
        },
        "universe": settings["universe"],
        "universe_snapshot_semantics": settings["universe_snapshot_semantics"],
        "universe_hash": universe_hash,
        "top_k": selected_top_k,
        "candidate_count": len(candidates),
        "candidate_hash": candidate_hash,
        "config_hash": config_hash,
        "feature_list_id": settings["feature_list_id"],
        "feature_list_hash": feature_list_hash,
        "feature_snapshot_hash": feature_snapshot_hash,
        "feature_availability": {
            "margin": {
                **settings["feature_availability"]["margin"],
                "as_of_date": margin_asof_date,
            }
        },
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
            "max_feature_missing_ratio": settings["max_feature_missing_ratio"],
            "min_model_used_feature_unique_values": settings[
                "min_model_used_feature_unique_values"
            ],
            "feature_missing_ratio": {
                feature: round(ratio, 6)
                for feature, ratio in feature_missing_ratio.items()
            },
            "feature_unique_non_null": feature_unique_non_null,
            "model_used_features": sorted(model_used_features),
            "excessive_missing_features": excessive_missing_features,
            "constant_model_used_features": constant_model_used_features,
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
