"""Canonical orchestration for UC_TOP10_SIGNAL_RUN.

This module composes the existing model-training and artifact-only inference
contracts.  It deliberately has no dependency on financial-report, promotion,
trading, broker, or ledger code.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import math
import os
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from qsys.common.config import read_yaml
from qsys.model.registry import create_model_trainer
from qsys.model.training import write_training_result
from qsys.ops.run_context import DailyRunContext
from qsys.signal.model_blend_inference import (
    InferenceRunResult,
    load_open_dates,
    resolve_inference_dates,
    run_candidate_inference,
)


class Top10RunError(RuntimeError):
    """Raised when the Top10 workflow cannot safely publish a result."""


@dataclass(frozen=True)
class Top10RunResult:
    artifact_path: Path
    payload: dict[str, Any]
    reused: bool


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _resolve_project_path(
    project_root: Path,
    value: str,
    *,
    field: str,
    must_exist: bool = False,
) -> Path:
    raw = str(value or "").strip()
    if not raw or "latest" in raw.lower():
        raise Top10RunError(f"{field} requires an explicit non-latest path")
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    current = candidate.anchor and Path(candidate.anchor) or Path(".")
    parts = candidate.parts[1:] if candidate.is_absolute() else candidate.parts
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise Top10RunError(f"{field} path must not contain symlinks: {current}")
    resolved = candidate.resolve(strict=False)
    if resolved != project_root and project_root not in resolved.parents:
        raise Top10RunError(f"{field} resolves outside project root: {resolved}")
    if must_exist and not resolved.is_file():
        raise Top10RunError(f"{field} is missing: {resolved}")
    return resolved


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Top10RunError(f"another Top10 run holds lock: {path}") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load_registry(path: Path, strategy_id: str) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "strategy_id": strategy_id, "entries": []}
    if path.is_symlink() or not path.is_file():
        raise Top10RunError(f"model registry must be a regular file: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Top10RunError(f"cannot read model registry {path}: {exc}") from exc
    if payload.get("schema_version") != 1 or payload.get("strategy_id") != strategy_id:
        raise Top10RunError("model registry schema/strategy mismatch")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise Top10RunError("model registry entries must be a list")
    hashes: set[str] = set()
    revisions: set[tuple[str, int]] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise Top10RunError("model registry entry must be a mapping")
        as_of = str(entry.get("as_of_date") or "")
        bundle_hash = str(entry.get("bundle_hash") or "")
        try:
            revision = int(entry.get("revision", 1))
        except (TypeError, ValueError) as exc:
            raise Top10RunError("model registry revision must be a positive integer") from exc
        if (
            not as_of
            or len(bundle_hash) != 64
            or revision <= 0
            or (as_of, revision) in revisions
            or bundle_hash in hashes
        ):
            raise Top10RunError("model registry contains invalid or duplicate entries")
        revisions.add((as_of, revision))
        hashes.add(bundle_hash)
    return payload


def _verify_registry_entry(project_root: Path, entry: dict[str, Any]) -> dict[str, Any]:
    bundle_path = _resolve_project_path(
        project_root,
        str(entry.get("bundle_path") or ""),
        field="registry.bundle_path",
        must_exist=True,
    )
    expected_file_hash = str(entry.get("bundle_file_sha256") or "")
    if _file_sha256(bundle_path) != expected_file_hash:
        raise Top10RunError(f"bundle file hash mismatch: {bundle_path}")
    try:
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Top10RunError(f"cannot read bundle {bundle_path}: {exc}") from exc
    if str(bundle.get("bundle_hash") or "") != str(entry.get("bundle_hash") or ""):
        raise Top10RunError("registry entry bundle_hash differs from bundle payload")
    hashed_payload = dict(bundle)
    declared_bundle_hash = str(hashed_payload.pop("bundle_hash", ""))
    if _canonical_hash(hashed_payload) != declared_bundle_hash:
        raise Top10RunError("bundle payload canonical hash mismatch")
    models = bundle.get("models")
    if not isinstance(models, list) or len(models) != 1:
        raise Top10RunError("S180 Top10 requires exactly one model in its bundle")
    return bundle


def _select_model_entry(
    registry: dict[str, Any],
    *,
    decision_date: str,
) -> dict[str, Any] | None:
    eligible = [
        entry
        for entry in registry["entries"]
        if str(entry.get("as_of_date") or "") <= decision_date
    ]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda entry: (
            entry["as_of_date"],
            int(entry.get("revision", 1)),
            entry["bundle_hash"],
        ),
    )


def _sessions_since(open_dates: list[str], earlier: str, later: str) -> int:
    try:
        return open_dates.index(later) - open_dates.index(earlier)
    except ValueError as exc:
        raise Top10RunError(
            f"model/decision date is not in authoritative calendar: {earlier}, {later}"
        ) from exc


def _publish_bundle(
    project_root: Path,
    bundle_source: Path,
    bundle_root: Path,
) -> tuple[Path, dict[str, Any]]:
    try:
        bundle = json.loads(bundle_source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Top10RunError(f"cannot read trained bundle {bundle_source}: {exc}") from exc
    bundle_hash = str(bundle.get("bundle_hash") or "")
    if len(bundle_hash) != 64:
        raise Top10RunError("trained bundle lacks a valid bundle_hash")
    target = bundle_root / f"{bundle_hash}.json"
    if target.exists():
        if target.is_symlink() or _file_sha256(target) != _file_sha256(bundle_source):
            raise Top10RunError(f"content-addressed bundle collision: {target}")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        shutil.copyfile(bundle_source, tmp)
        os.replace(tmp, target)
    return target, bundle


def _append_registry_entry(
    registry_path: Path,
    registry: dict[str, Any],
    entry: dict[str, Any],
) -> dict[str, Any]:
    existing = [
        item
        for item in registry["entries"]
        if item["bundle_hash"] == entry["bundle_hash"]
    ]
    if existing:
        comparable = {
            key: value for key, value in existing[0].items() if key != "revision"
        }
        if comparable != entry:
            raise Top10RunError("same bundle hash has conflicting registry metadata")
        return existing[0]
    revisions = [
        int(item.get("revision", 1))
        for item in registry["entries"]
        if item["as_of_date"] == entry["as_of_date"]
    ]
    entry = {**entry, "revision": max(revisions, default=0) + 1}
    registry["entries"].append(entry)
    registry["entries"].sort(
        key=lambda item: (
            item["as_of_date"],
            int(item.get("revision", 1)),
            item["bundle_hash"],
        )
    )
    _atomic_write_json(registry_path, registry)
    return entry


def _refresh_label(
    project_root: Path,
    label_config_path: Path,
    *,
    as_of_date: str,
) -> Path:
    from qsys.label.store import LabelStore

    config = read_yaml(label_config_path)
    config = copy.deepcopy(config)
    config.setdefault("date_range", {})["end_date"] = as_of_date
    cwd = Path.cwd()
    try:
        os.chdir(project_root)
        return LabelStore(project_root / "data" / "research").compute_and_save_from_config(
            config, overwrite=True
        )
    finally:
        os.chdir(cwd)


def _training_context(
    project_root: Path,
    strategy_config: dict[str, Any],
    *,
    decision_date: str,
    run_root: Path,
    triggered_by: str,
) -> DailyRunContext:
    return DailyRunContext(
        trade_date=decision_date,
        mode="train",
        run_root=run_root,
        project_root=project_root,
        strategy_id=str(strategy_config["strategy_id"]),
        account_id=str(strategy_config.get("account_id") or "research_s180_top10"),
        run_mode="shadow",
        no_notify=True,
        triggered_by=triggered_by,
    )


def _train_and_register(
    project_root: Path,
    strategy_config: dict[str, Any],
    *,
    decision_date: str,
    snapshot_path: Path,
    registry_path: Path,
    registry: dict[str, Any],
    bundle_root: Path,
    state_root: Path,
    triggered_by: str,
    reuse_checkpoint: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    config = copy.deepcopy(strategy_config)
    config.setdefault("training", {})["end_date"] = decision_date
    config["training"]["prediction_membership_path"] = str(snapshot_path)
    forced_revision = None
    if not reuse_checkpoint:
        forced_revision = 1 + max(
            (
                int(item.get("revision", 1))
                for item in registry["entries"]
                if item["as_of_date"] == decision_date
            ),
            default=0,
        )
    train_fingerprint = _canonical_hash(
        {
            "strategy_config": config,
            "decision_date": decision_date,
            "snapshot_sha256": _file_sha256(snapshot_path),
            # A forced same-day repair gets a fresh checkpoint namespace while
            # retries of that same unpublished revision remain resumable.
            "forced_revision": forced_revision,
        }
    )
    train_root = state_root / decision_date / f"train_{train_fingerprint[:16]}"
    result_path = train_root / "training_result.json"
    bundle_source = train_root / "model_bundle.json"
    if result_path.is_file() and bundle_source.is_file():
        try:
            result_payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise Top10RunError(f"invalid training checkpoint: {result_path}") from exc
        if result_payload.get("status") != "success":
            raise Top10RunError(f"training checkpoint is not successful: {result_path}")
    else:
        trainer = create_model_trainer(
            str(config["strategy_id"]), config, project_root=project_root
        )
        ctx = _training_context(
            project_root,
            config,
            decision_date=decision_date,
            run_root=train_root,
            triggered_by=triggered_by,
        )
        result = trainer.train(ctx)
        if result is None or result.status != "success":
            raise Top10RunError("S180 trainer did not return a successful result")
        write_training_result(result, result_path)
        bundle_value = result.artifacts.get("bundle_manifest")
        if bundle_value:
            bundle_source = Path(bundle_value)
            if not bundle_source.is_absolute():
                bundle_source = project_root / bundle_source
        if not bundle_source.is_file():
            raise Top10RunError(f"trainer did not publish model_bundle.json: {bundle_source}")

    bundle_path, bundle = _publish_bundle(project_root, bundle_source, bundle_root)
    relative_bundle = str(bundle_path.relative_to(project_root))
    entry = {
        "as_of_date": decision_date,
        "bundle_hash": bundle["bundle_hash"],
        "bundle_path": relative_bundle,
        "bundle_file_sha256": _file_sha256(bundle_path),
        "train_start": min(str(model["metrics"].get("train_start") or "9999") for model in bundle["models"])
        if all(isinstance(model.get("metrics"), dict) for model in bundle["models"])
        else None,
        "train_end": max(str(model.get("metrics", {}).get("train_end") or decision_date) for model in bundle["models"]),
    }
    # Bundle model meta is authoritative for dates; registry keeps only a fast
    # index and the immutable bundle hash.
    model_dir = _resolve_project_path(
        project_root,
        str(bundle["models"][0]["model_dir"]),
        field="bundle.model_dir",
    )
    meta = json.loads((model_dir / "meta.json").read_text(encoding="utf-8"))
    entry["train_start"] = str(meta["train_start"])
    entry["train_end"] = str(meta["train_end"])
    published = _append_registry_entry(registry_path, registry, entry)
    return published, bundle


def _inference_config(
    strategy_config: dict[str, Any],
    bundle: dict[str, Any],
    *,
    snapshot_path: Path,
) -> dict[str, Any]:
    config = copy.deepcopy(strategy_config)
    models: list[dict[str, Any]] = []
    for model in bundle["models"]:
        item = {
            key: value
            for key, value in model.items()
            if key
            in {
                "tag",
                "model_hash",
                "artifact_id",
                "model_dir",
                "label_id",
                "horizon",
                "artifact_sha256",
            }
        }
        item["weight"] = 1.0
        models.append(item)
    config["inference"]["model_bundle"] = {
        "bundle_id": bundle["bundle_hash"],
        "models": models,
    }
    config["inference"]["universe_snapshot_path"] = str(snapshot_path)
    return config


def validate_top10_run_artifact(path: str | Path) -> dict[str, Any]:
    artifact_path = Path(path)
    if artifact_path.is_symlink() or not artifact_path.is_file():
        raise Top10RunError(f"Top10 artifact must be a regular file: {artifact_path}")
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Top10RunError(f"cannot read Top10 artifact: {exc}") from exc
    required = {
        "schema_version",
        "artifact_type",
        "status",
        "strategy_id",
        "run_identity",
        "signal_date",
        "data_date",
        "decision_date",
        "execution_date",
        "candidate_artifact",
        "candidate_artifact_sha256",
        "model",
        "quality_gate",
        "top10",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise Top10RunError(f"Top10 artifact lacks fields: {missing}")
    if (
        payload["schema_version"] != 1
        or payload["artifact_type"] != "s180_top10_signal_run"
        or payload["status"] != "complete"
        or payload["strategy_id"] != "s180_top10"
    ):
        raise Top10RunError("Top10 artifact schema/status/strategy mismatch")
    rows = payload["top10"]
    if not isinstance(rows, list) or len(rows) != 10:
        raise Top10RunError("Top10 artifact must contain exactly 10 rows")
    codes = [str(row.get("ts_code") or "") for row in rows]
    scores = [row.get("raw_prediction") for row in rows]
    if len(codes) != len(set(codes)) or any(not code for code in codes):
        raise Top10RunError("Top10 instruments must be non-empty and unique")
    if any(isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) for score in scores):
        raise Top10RunError("Top10 raw predictions must be finite numbers")
    if scores != sorted(scores, reverse=True):
        raise Top10RunError("Top10 rows are not sorted by raw_prediction descending")
    if [row.get("rank") for row in rows] != list(range(1, 11)):
        raise Top10RunError("Top10 ranks must be 1..10")
    project_root = artifact_path.resolve().parents[4]
    candidate_path = _resolve_project_path(
        project_root,
        str(payload["candidate_artifact"]),
        field="candidate_artifact",
        must_exist=True,
    )
    if _file_sha256(candidate_path) != payload["candidate_artifact_sha256"]:
        raise Top10RunError("candidate artifact hash mismatch")
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    if candidate.get("candidate_hash") != payload["quality_gate"].get("candidate_hash"):
        raise Top10RunError("candidate hash differs from quality gate")
    if payload["quality_gate"].get("score_transform") != "raw_model_prediction":
        raise Top10RunError("Top10 quality gate must pin raw_model_prediction")
    if candidate.get("source", {}).get("model_bundle_hash") != payload["model"].get(
        "bundle_hash"
    ):
        raise Top10RunError("model bundle differs from candidate artifact")
    candidate_rows = candidate.get("candidates")
    if not isinstance(candidate_rows, list) or len(candidate_rows) != 10:
        raise Top10RunError("candidate artifact does not contain 10 rows")
    if [row.get("ts_code") for row in candidate_rows] != codes:
        raise Top10RunError("Top10 rows differ from candidate artifact")
    if [float(row.get("raw_prediction")) for row in candidate_rows] != [
        float(score) for score in scores
    ]:
        raise Top10RunError("Top10 raw predictions differ from candidate artifact")
    return payload


def _publish_top10_manifest(
    project_root: Path,
    inference_result: InferenceRunResult,
    *,
    model_entry: dict[str, Any],
    retrained: bool,
    retrain_reason: str,
    sessions_since_model: int | None,
) -> Top10RunResult:
    candidate_path = inference_result.artifact_path.resolve()
    candidate = inference_result.payload
    top_rows = [
        {
            "rank": int(row["rank"]),
            "ts_code": str(row["ts_code"]),
            "name": str(row.get("name") or ""),
            "raw_prediction": float(row["raw_prediction"]),
        }
        for row in candidate["candidates"]
    ]
    run_identity = _canonical_hash(
        {
            "strategy_id": "s180_top10",
            "signal_date": candidate["signal_date"],
            "candidate_hash": candidate["candidate_hash"],
            "feature_snapshot_hash": candidate["feature_snapshot_hash"],
            "model_bundle_hash": candidate["source"]["model_bundle_hash"],
        }
    )
    payload = {
        "schema_version": 1,
        "artifact_type": "s180_top10_signal_run",
        "status": "complete",
        "strategy_id": "s180_top10",
        "run_identity": run_identity,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "signal_date": candidate["signal_date"],
        "data_date": candidate["data_date"],
        "decision_date": candidate["decision_date"],
        "execution_date": candidate["execution_date"],
        "candidate_artifact": str(candidate_path.relative_to(project_root)),
        "candidate_artifact_sha256": _file_sha256(candidate_path),
        "model": model_entry,
        "training": {
            "retrained": retrained,
            "reason": retrain_reason,
            "sessions_since_model": sessions_since_model,
        },
        "quality_gate": {
            "status": "pass",
            "candidate_hash": candidate["candidate_hash"],
            "feature_snapshot_hash": candidate["feature_snapshot_hash"],
            "universe_hash": candidate["universe_hash"],
            "model_bundle_hash": candidate["source"]["model_bundle_hash"],
            "score_transform": candidate["blend"]["score_transform"],
            "eligible_rows": candidate["data_quality"]["eligible_rows"],
            "checks": [
                "completed-session date contract",
                "181-session label maturity",
                "dated PIT CSI1800 membership",
                "feature quality and coverage",
                "content-addressed model hashes",
                "raw prediction finite/variance/order",
            ],
        },
        "top10": top_rows,
    }
    artifact_path = candidate_path.with_name("top10_run.json")
    if artifact_path.exists():
        existing = validate_top10_run_artifact(artifact_path)
        if existing["run_identity"] != run_identity:
            raise Top10RunError(f"Top10 artifact identity collision: {artifact_path}")
        return Top10RunResult(artifact_path, existing, True)
    _atomic_write_json(artifact_path, payload)
    validated = validate_top10_run_artifact(artifact_path)
    return Top10RunResult(artifact_path, validated, False)


def run_top10_signal(
    *,
    strategy_config: dict[str, Any],
    project_root: Path,
    signal_date: str | None = "auto",
    execution_date: str | None = None,
    force_retrain: bool = False,
    reason: str | None = None,
    triggered_by: str = "manual",
    now: datetime | None = None,
) -> Top10RunResult:
    """Run the full Top10 state machine and publish one verified artifact."""

    project_root = Path(project_root).resolve()
    if strategy_config.get("strategy_id") != "s180_top10":
        raise Top10RunError("UC_TOP10_SIGNAL_RUN requires strategy_id=s180_top10")
    if force_retrain and not str(reason or "").strip():
        raise Top10RunError("force_retrain requires a non-empty reason")
    settings = strategy_config.get("top10_run")
    if not isinstance(settings, dict):
        raise Top10RunError("top10_run config must be a mapping")
    interval = int(settings.get("retrain_interval_sessions", 0))
    if interval <= 0:
        raise Top10RunError("retrain_interval_sessions must be positive")

    open_dates = load_open_dates(project_root)
    inference = strategy_config.get("inference") or {}
    dates = resolve_inference_dates(
        signal_date,
        execution_date,
        open_dates,
        now=now,
        market_close_cutoff=str(inference.get("market_close_cutoff", "18:00")),
        universe_snapshot_semantics=str(
            inference.get("universe_snapshot_semantics") or ""
        ),
        feature_snapshot_lag_sessions=int(
            inference.get("feature_snapshot_lag_sessions", 0)
        ),
    )
    snapshot_root = _resolve_project_path(
        project_root,
        str(settings.get("membership_snapshot_root") or ""),
        field="top10_run.membership_snapshot_root",
    )
    snapshot_path = snapshot_root / dates.decision_date.replace("-", "") / "membership.parquet"
    if snapshot_path.is_symlink() or not snapshot_path.is_file():
        raise Top10RunError(f"dated PIT membership snapshot is missing: {snapshot_path}")

    registry_path = _resolve_project_path(
        project_root,
        str(settings.get("model_registry") or ""),
        field="top10_run.model_registry",
    )
    bundle_root = _resolve_project_path(
        project_root,
        str(settings.get("bundle_root") or ""),
        field="top10_run.bundle_root",
    )
    state_root = _resolve_project_path(
        project_root,
        str(settings.get("state_root") or ""),
        field="top10_run.state_root",
    )
    lock_path = _resolve_project_path(
        project_root,
        str(settings.get("lock_path") or ""),
        field="top10_run.lock_path",
    )
    state_path = state_root / dates.decision_date / "state.json"

    with _exclusive_lock(lock_path):
        registry = _load_registry(registry_path, "s180_top10")
        selected = _select_model_entry(registry, decision_date=dates.decision_date)
        sessions_since_model: int | None = None
        if selected is not None:
            sessions_since_model = _sessions_since(
                open_dates, str(selected["as_of_date"]), dates.decision_date
            )
        due = selected is None or (sessions_since_model or 0) >= interval
        should_train = force_retrain or due
        retrain_reason = (
            str(reason)
            if force_retrain
            else "initial_model"
            if selected is None
            else f"interval_reached:{sessions_since_model}/{interval}"
            if due
            else f"model_reused:{sessions_since_model}/{interval}"
        )
        state = {
            "schema_version": 1,
            "strategy_id": "s180_top10",
            "decision_date": dates.decision_date,
            "signal_date": dates.signal_date,
            "stage": "preflight_passed",
            "status": "running",
            "snapshot_path": str(snapshot_path.relative_to(project_root)),
            "snapshot_sha256": _file_sha256(snapshot_path),
            "should_train": should_train,
            "retrain_reason": retrain_reason,
        }
        _atomic_write_json(state_path, state)
        try:
            if should_train:
                if bool(settings.get("refresh_labels_on_retrain", True)):
                    label_config_path = _resolve_project_path(
                        project_root,
                        str(settings.get("label_config") or ""),
                        field="top10_run.label_config",
                        must_exist=True,
                    )
                    label_path = _refresh_label(
                        project_root, label_config_path, as_of_date=dates.decision_date
                    )
                    state.update(
                        {
                            "stage": "labels_refreshed",
                            "label_path": str(Path(label_path).resolve().relative_to(project_root)),
                            "label_sha256": _file_sha256(Path(label_path)),
                        }
                    )
                    _atomic_write_json(state_path, state)
                selected, bundle = _train_and_register(
                    project_root,
                    strategy_config,
                    decision_date=dates.decision_date,
                    snapshot_path=snapshot_path,
                    registry_path=registry_path,
                    registry=registry,
                    bundle_root=bundle_root,
                    state_root=state_root,
                    triggered_by=triggered_by,
                    reuse_checkpoint=not force_retrain,
                )
                sessions_since_model = 0
                state.update(
                    {
                        "stage": "model_published",
                        "model_bundle_hash": selected["bundle_hash"],
                    }
                )
                _atomic_write_json(state_path, state)
            else:
                assert selected is not None
                bundle = _verify_registry_entry(project_root, selected)

            inference_config = _inference_config(
                strategy_config, bundle, snapshot_path=snapshot_path
            )
            inference_result = run_candidate_inference(
                strategy_id="s180_top10",
                strategy_config=inference_config,
                project_root=project_root,
                signal_date=dates.signal_date,
                execution_date=dates.execution_date,
                top_k=int(settings.get("top_k", 10)),
                now=now,
            )
            state.update(
                {
                    "stage": "inference_complete",
                    "candidate_artifact": str(
                        inference_result.artifact_path.resolve().relative_to(project_root)
                    ),
                }
            )
            _atomic_write_json(state_path, state)
            result = _publish_top10_manifest(
                project_root,
                inference_result,
                model_entry=selected,
                retrained=should_train,
                retrain_reason=retrain_reason,
                sessions_since_model=sessions_since_model,
            )
            state.update(
                {
                    "stage": "complete",
                    "status": "complete",
                    "top10_artifact": str(result.artifact_path.relative_to(project_root)),
                    "run_identity": result.payload["run_identity"],
                }
            )
            _atomic_write_json(state_path, state)
            return result
        except Exception as exc:
            state.update(
                {
                    "stage": state.get("stage", "unknown"),
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            _atomic_write_json(state_path, state)
            raise
