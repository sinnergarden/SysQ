"""Canonical, provenance-complete trainer for the financial_rc model blend."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Sequence

from qsys.feature.availability import (
    normalise_feature_availability,
    resolve_lagged_open_session,
)
from qsys.feature.freshness import (
    normalise_shareholder_freshness,
    profile_shareholder_feature_freshness,
    shareholder_row_freshness_reasons,
)
from qsys.model.training import TrainingResult


class FinancialRCTrainingError(RuntimeError):
    """Raised when a training input or artifact violates the contract."""


@dataclass(frozen=True)
class TrainingWindow:
    """An effective, fully matured feature/label training window."""

    train_start: str
    train_end: str
    label_start: str
    label_end: str
    as_of_date: str
    horizon: int
    window_sessions: int
    maturity_sessions: int


def _normalise_date(value: Any) -> str:
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError as exc:
        raise FinancialRCTrainingError(f"invalid date: {value!r}") from exc


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


def compute_model_artifact_identity(
    *,
    artifact_hashes: dict[str, str],
    feature_list_hash: str,
    label_lineage: dict[str, Any],
    training_config_hash: str,
    feature_availability: dict[str, Any],
    universe_lineage: dict[str, Any] | None = None,
) -> str:
    """Identify the complete immutable training artifact, not just model.txt."""

    payload = {
        "serialization": "financial-rc-model-artifact-v1",
        "model_sha256": artifact_hashes["model.txt"],
        "center_sha256": artifact_hashes["center.json"],
        "scale_sha256": artifact_hashes["scale.json"],
        "training_snapshot_sha256": artifact_hashes["training_snapshot.parquet"],
        "feature_list_hash": feature_list_hash,
        "label_lineage": label_lineage,
        "training_config_hash": training_config_hash,
        "feature_availability": feature_availability,
    }
    # Omit the field for callers computing identities for pre-lineage legacy
    # artifacts; all generic trainer artifacts pass a non-None lineage map.
    if universe_lineage is not None:
        payload["universe_lineage"] = universe_lineage
    return _canonical_hash(payload)


def derive_training_window(
    open_dates: Sequence[str],
    label_dates: Sequence[str],
    *,
    as_of_date: str,
    horizon: int,
    window_sessions: int,
) -> TrainingWindow:
    """Derive the freshest fully matured fixed-session training window.

    A feature row at ``f`` is paired with the label at ``next_open(f)``.
    One additional completed session is required after the label horizon,
    matching the strict F01/F16 maturity rule used by rolling research.
    """

    sessions = sorted({_normalise_date(value) for value in open_dates})
    session_set = set(sessions)
    labels = sorted({_normalise_date(value) for value in label_dates})
    resolved_as_of = _normalise_date(as_of_date)
    if resolved_as_of not in sessions:
        raise FinancialRCTrainingError(
            f"as_of_date is not an open session: {resolved_as_of}"
        )
    if horizon <= 0 or window_sessions <= 1:
        raise FinancialRCTrainingError("horizon and window_sessions must be positive")

    as_of_index = sessions.index(resolved_as_of)
    latest_mature_label_index = as_of_index - horizon - 1
    if latest_mature_label_index <= 0:
        raise FinancialRCTrainingError(
            f"insufficient calendar history for horizon={horizon} at {resolved_as_of}"
        )
    latest_mature_label = sessions[latest_mature_label_index]
    available = [
        value
        for value in labels
        if value in session_set and value <= latest_mature_label
    ]
    if not available:
        raise FinancialRCTrainingError(
            f"no mature labels for horizon={horizon} at {resolved_as_of}"
        )

    label_end = available[-1]
    label_end_index = sessions.index(label_end)
    train_end_index = label_end_index - 1
    train_start_index = train_end_index - window_sessions + 1
    if train_start_index < 0:
        raise FinancialRCTrainingError(
            f"insufficient history for a {window_sessions}-session training window"
        )
    train_start = sessions[train_start_index]
    train_end = sessions[train_end_index]
    label_start = sessions[train_start_index + 1]
    maturity_sessions = as_of_index - train_end_index
    required = horizon + 2
    if maturity_sessions < required:
        raise FinancialRCTrainingError(
            "label maturity violation: "
            f"observed={maturity_sessions}, required={required}"
        )
    return TrainingWindow(
        train_start=train_start,
        train_end=train_end,
        label_start=label_start,
        label_end=label_end,
        as_of_date=resolved_as_of,
        horizon=horizon,
        window_sessions=window_sessions,
        maturity_sessions=maturity_sessions,
    )


def derive_purged_evaluation_train_end(
    open_dates: Sequence[str], validation_start: str, horizon: int
) -> str:
    """Return the last feature date whose label ends before validation.

    With next-session labels, ``horizon + 1`` sessions between the last
    evaluation-training feature and validation start must be purged.
    """

    sessions = sorted({_normalise_date(value) for value in open_dates})
    resolved_validation_start = _normalise_date(validation_start)
    if resolved_validation_start not in sessions or horizon <= 0:
        raise FinancialRCTrainingError(
            "validation_start must be an open session and horizon positive"
        )
    train_end_index = sessions.index(resolved_validation_start) - horizon - 2
    if train_end_index < 0:
        raise FinancialRCTrainingError("insufficient purged evaluation history")
    return sessions[train_end_index]


def profile_label_universe_coverage(
    label_instruments: Sequence[Any],
    universe_members: Sequence[Any],
    *,
    min_coverage: float,
) -> dict[str, Any]:
    """Fail when a current-snapshot label artifact belongs to an older universe."""

    members = {str(value) for value in universe_members}
    observed = {str(value) for value in label_instruments}
    if not members:
        raise FinancialRCTrainingError("label coverage requires a non-empty universe")
    if not 0 < min_coverage <= 1:
        raise FinancialRCTrainingError(
            "training.min_label_universe_coverage must be in (0, 1]"
        )
    missing = sorted(members - observed)
    coverage = (len(members) - len(missing)) / len(members)
    result = {
        "universe_size": len(members),
        "covered_members": len(members) - len(missing),
        "coverage": float(coverage),
        "min_coverage": float(min_coverage),
        "missing_members": missing,
    }
    if coverage < min_coverage:
        raise FinancialRCTrainingError(
            "label artifact does not cover the current training universe: "
            f"coverage={coverage:.4f}, required={min_coverage:.4f}, "
            f"missing_sample={missing[:20]}"
        )
    return result


@contextmanager
def _project_working_directory(project_root: Path):
    previous = Path.cwd()
    os.chdir(project_root)
    try:
        yield
    finally:
        os.chdir(previous)


def _git_state(project_root: Path) -> dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=no"],
                cwd=project_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        return {"commit": commit, "dirty": dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None}


def _package_versions() -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for package in ("lightgbm", "numpy", "pandas", "pyarrow", "pyqlib"):
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            result[package] = None
    return result


def _require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FinancialRCTrainingError(f"{name} must be a mapping")
    return value


def _resolve_project_path(project_root: Path, value: str | Path) -> Path:
    """Resolve a configured artifact path without changing process cwd."""

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve(strict=False)


def _prediction_membership_identity(
    project_root: Path, value: str | Path
) -> tuple[Path, str, set[str]]:
    """Validate and hash an exact current inference membership snapshot.

    A regular, non-symlink parquet file is required.  The bytes, rather than a
    parsed/re-serialized frame, are hashed so the identity cannot drift from
    formatting or parquet metadata changes after training.
    """

    import pandas as pd

    # Do not call resolve() here: resolving first would erase the symlink bit
    # that this contract explicitly rejects.
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project_root / path
    path = path.absolute()
    if path.is_symlink() or not path.is_file():
        raise FinancialRCTrainingError(
            "training.prediction_membership_path must be an existing regular parquet file: "
            f"{path}"
        )
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        raise FinancialRCTrainingError(
            f"cannot stat prediction membership snapshot: {path}"
        ) from exc
    # is_file() follows symlinks, so explicitly require a regular inode too.
    import stat

    if not stat.S_ISREG(mode):
        raise FinancialRCTrainingError(
            "training.prediction_membership_path must be an ordinary regular file: "
            f"{path}"
        )
    try:
        snapshot = pd.read_parquet(path)
    except Exception as exc:
        raise FinancialRCTrainingError(
            f"failed to read prediction membership parquet: {path}"
        ) from exc
    if "instrument" not in snapshot.columns:
        raise FinancialRCTrainingError(
            "prediction membership snapshot must contain an instrument column: "
            f"{path}"
        )
    if snapshot.empty:
        raise FinancialRCTrainingError(
            f"prediction membership snapshot is empty: {path}"
        )
    values = snapshot["instrument"]
    if values.isna().any():
        raise FinancialRCTrainingError(
            f"prediction membership snapshot contains null instruments: {path}"
        )
    members = values.astype(str).str.strip().str.upper()
    if (members == "").any() or members.duplicated().any():
        raise FinancialRCTrainingError(
            "prediction membership snapshot contains blank or duplicate instruments: "
            f"{path}"
        )
    return path, _file_sha256(path), set(members.tolist())


def _pit_universe_identity(project_root: Path, value: str | Path) -> tuple[Any, dict[str, Any]]:
    """Load a verified PIT store and expose its immutable provenance."""

    try:
        from qsys.research.pit_universe import PitUniverseStore
    except ModuleNotFoundError as exc:
        # ``qsys.research.__init__`` imports optional qlib-backed modules.  A
        # trainer contract test may exercise PIT validation without qlib
        # installed, while the PIT store itself only needs pandas.  Load that
        # canonical module directly in this narrow fallback.
        if exc.name not in {"qlib", "qlib.data", "qlib.data.dataset"}:
            raise
        import importlib.util
        import sys

        module_path = Path(__file__).resolve().parents[1] / "research" / "pit_universe.py"
        spec = importlib.util.spec_from_file_location(
            "qsys_research_pit_universe_standalone", module_path
        )
        if spec is None or spec.loader is None:
            raise FinancialRCTrainingError(
                f"cannot load PIT universe store: {module_path}"
            ) from exc
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        PitUniverseStore = module.PitUniverseStore

    raw = Path(value).expanduser()
    if not raw.is_absolute():
        direct = project_root / raw
        named = project_root / "data" / "research" / "universes" / raw
        artifact = named if (named / "manifest.json").is_file() else direct
    else:
        artifact = raw
    artifact = artifact.resolve(strict=False)
    try:
        store = PitUniverseStore(artifact, verify_hash=True)
    except (OSError, ValueError) as exc:
        raise FinancialRCTrainingError(
            f"failed to load training.pit_universe_artifact: {artifact}"
        ) from exc
    provenance = store.provenance.to_dict()
    try:
        provenance["artifact_dir"] = str(artifact.relative_to(project_root))
    except ValueError:
        provenance["artifact_dir"] = str(artifact)
    return store, provenance


def _filter_pit_membership(frame: Any, store: Any) -> Any:
    """Apply strict inclusive ``[effective_from, effective_to]`` PIT rows."""

    import pandas as pd

    if frame.empty:
        return frame
    required = {"trade_date", "instrument"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise FinancialRCTrainingError(
            f"PIT membership filter requires columns: {missing}"
        )
    spans = store.spans[["instrument", "effective_from", "effective_to"]].copy()
    spans["instrument"] = spans["instrument"].astype(str).str.strip().str.upper()
    spans["effective_from"] = pd.to_datetime(
        spans["effective_from"], errors="raise"
    ).dt.strftime("%Y%m%d").astype(int)
    spans["effective_to"] = pd.to_datetime(
        spans["effective_to"], errors="raise"
    ).dt.strftime("%Y%m%d").astype(int)
    work = frame.copy()
    work["_pit_row_id"] = work.index
    work["_pit_instrument"] = work["instrument"].astype(str).str.strip().str.upper()
    work["_pit_date"] = pd.to_datetime(work["trade_date"], errors="raise").dt.strftime("%Y%m%d").astype(int)
    # Membership spans are disjoint per instrument; a merge plus interval
    # comparison is both exact and independent of a collapsed qlib registry.
    merged = work.merge(
        spans,
        left_on="_pit_instrument",
        right_on="instrument",
        how="left",
        suffixes=("", "_span"),
    )
    keep = merged["effective_from"].notna() & merged["_pit_date"].between(
        merged["effective_from"], merged["effective_to"], inclusive="both"
    )
    kept_row_ids = merged.loc[keep, "_pit_row_id"].tolist()
    # A source frame must not be multiplied by overlapping spans.  The store
    # contract says spans are disjoint, but fail closed if an artifact breaks it.
    span_counts = merged.loc[keep, "_pit_row_id"].value_counts()
    if (span_counts > 1).any():
        raise FinancialRCTrainingError("PIT membership artifact has overlapping spans")
    result = work.loc[kept_row_ids].drop(
        columns=["_pit_instrument", "_pit_date", "_pit_row_id"]
    )
    if result.empty:
        raise FinancialRCTrainingError("no feature rows remain after PIT membership filter")
    if result.index.duplicated().any():
        raise FinancialRCTrainingError("PIT membership filter produced duplicate feature rows")
    return result


class FinancialRCTrainer:
    """Train and package the pinned 60d/180d LightGBM research bundle."""

    def __init__(self, config: dict[str, Any], project_root: Path) -> None:
        self.config = config
        self.project_root = project_root.resolve()
        self.strategy_id = str(config.get("strategy_id") or "financial_rc")
        self.account_id = str(config.get("account_id") or "research_financial_rc")
        self.display_name = str(config.get("display_name") or "Financial RC")
        self.model_version = str(config.get("model_version") or "financial_rc")

    @classmethod
    def from_config(
        cls,
        config: dict[str, Any],
        *,
        project_root: Path | None = None,
    ) -> "FinancialRCTrainer":
        if project_root is None:
            raise FinancialRCTrainingError("project_root is required")
        return cls(config, Path(project_root))

    def _settings(self) -> dict[str, Any]:
        training = _require_mapping(self.config.get("training"), "training")
        try:
            feature_availability = normalise_feature_availability(
                self.config.get("feature_availability")
            )
        except ValueError as exc:
            raise FinancialRCTrainingError(str(exc)) from exc
        raw_freshness = _require_mapping(
            self.config.get("feature_freshness"), "feature_freshness"
        )
        try:
            shareholder_freshness = normalise_shareholder_freshness(
                raw_freshness.get("shareholder")
            )
        except ValueError as exc:
            raise FinancialRCTrainingError(str(exc)) from exc
        engine = str(training.get("engine") or "").strip()
        supported_engines = {
            "financial_rc_lightgbm_bundle_v1",  # legacy financial_rc config
            "lightgbm_model_bundle_v1",  # generic single/multi-model bundle
        }
        if engine not in supported_engines:
            raise FinancialRCTrainingError(
                "training.engine must be financial_rc_lightgbm_bundle_v1 or "
                "lightgbm_model_bundle_v1"
            )
        models = training.get("models")
        if not isinstance(models, list) or len(models) < 1:
            raise FinancialRCTrainingError("training.models must contain at least one model")
        parsed_models: list[dict[str, Any]] = []
        tags: set[str] = set()
        for raw in models:
            model = _require_mapping(raw, "training.models[]")
            tag = str(model.get("tag") or "").strip()
            label_id = str(model.get("label_id") or "").strip()
            experiment_id = str(model.get("experiment_id") or "").strip()
            horizon = int(model.get("horizon") or 0)
            if not tag or tag in tags or not label_id or not experiment_id or horizon <= 0:
                raise FinancialRCTrainingError(f"invalid training model spec: {model}")
            tags.add(tag)
            parsed_models.append(
                {
                    "tag": tag,
                    "label_id": label_id,
                    "experiment_id": experiment_id,
                    "horizon": horizon,
                    "n_estimators": int(
                        model.get("n_estimators", training.get("n_estimators", 300))
                    ),
                    "window_sessions": int(
                        model.get(
                            "window_sessions", training.get("window_sessions", 504)
                        )
                    ),
                    "validation_sessions": int(
                        model.get(
                            "validation_sessions",
                            training.get("validation_sessions", 40),
                        )
                    ),
                    "lgb_params": model.get("lgb_params"),
                }
            )
        return {
            "engine": engine,
            "feature_list_id": str(
                training.get("feature_list_id") or self.config.get("feature_set") or ""
            ),
            "universe": str(
                training.get("training_universe")
                or training.get("universe")
                or self.config.get("universe")
                or ""
            ),
            "training_universe": str(
                training.get("training_universe")
                or training.get("universe")
                or self.config.get("universe")
                or ""
            ),
            "inference_universe": str(
                training.get("inference_universe")
                or _require_mapping(self.config.get("inference", {}), "inference").get(
                    "universe"
                )
                or training.get("universe")
                or self.config.get("universe")
                or ""
            ),
            "pit_universe_artifact": str(
                training.get("pit_universe_artifact") or ""
            ).strip(),
            "prediction_membership_path": str(
                training.get("prediction_membership_path") or ""
            ).strip(),
            "min_feature_coverage": float(training.get("min_feature_coverage", 0.5)),
            "max_feature_missing_ratio": float(
                training.get("max_feature_missing_ratio", 0.995)
            ),
            "min_label_universe_coverage": float(
                training.get("min_label_universe_coverage", 0.95)
            ),
            "pointer_write_mode": str(training.get("pointer_write_mode", "none")),
            "models": parsed_models,
            "feature_availability": feature_availability,
            "shareholder_freshness": shareholder_freshness,
            "config_hash": _canonical_hash(
                {
                    "training": training,
                    "feature_availability": feature_availability,
                    "shareholder_freshness": shareholder_freshness,
                }
            ),
        }

    def train(self, ctx: Any) -> TrainingResult:
        import numpy as np
        import pandas as pd

        from qsys.data.adapter import QlibAdapter
        from qsys.feature.registry import FeatureListRegistry
        from qsys.label.store import LabelStore
        from qsys.signal.alpha_v1.training import (
            fit_model_fixed_rounds,
            predict_model,
            train_model,
        )
        from qsys.signal.model_blend_inference import (
            compute_universe_hash,
            load_open_dates,
            load_universe_snapshot_members,
        )

        settings = self._settings()
        if settings["pointer_write_mode"] != "none":
            raise FinancialRCTrainingError(
                "financial_rc training is research-only and requires pointer_write_mode=none"
            )
        if not settings["feature_list_id"] or not settings["training_universe"]:
            raise FinancialRCTrainingError(
                "training requires feature_list_id and training universe"
            )
        as_of_date = _normalise_date(
            self.config.get("training", {}).get("end_date") or ctx.trade_date
        )
        open_dates = load_open_dates(self.project_root)
        if as_of_date not in open_dates:
            raise FinancialRCTrainingError(
                f"training as_of_date is not an open session: {as_of_date}"
            )
        features = list(FeatureListRegistry.load(settings["feature_list_id"]))
        if not features or len(features) != len(set(features)):
            raise FinancialRCTrainingError(
                "feature list must be non-empty and contain no duplicates"
            )

        pit_store = None
        pit_provenance: dict[str, Any] | None = None
        if settings["pit_universe_artifact"]:
            pit_store, pit_provenance = _pit_universe_identity(
                self.project_root, settings["pit_universe_artifact"]
            )

        prediction_path: Path | None = None
        prediction_members: set[str] | None = None
        prediction_sha256: str | None = None
        if settings["prediction_membership_path"]:
            prediction_path, prediction_sha256, prediction_members = (
                _prediction_membership_identity(
                    self.project_root, settings["prediction_membership_path"]
                )
            )

        if prediction_members is not None:
            universe_members = sorted(prediction_members)
        elif settings["inference_universe"]:
            universe_members = load_universe_snapshot_members(
                self.project_root, settings["inference_universe"], as_of_date
            )
        else:
            raise FinancialRCTrainingError("training requires an inference universe")
        universe_hash = compute_universe_hash(universe_members)
        universe_lineage: dict[str, Any] = {
            "training_universe": settings["training_universe"],
            "inference_universe": settings["inference_universe"],
            "inference_universe_hash": universe_hash,
            "as_of_date": as_of_date,
        }
        if pit_provenance is not None:
            universe_lineage["pit_universe"] = pit_provenance
        if prediction_path is not None:
            universe_lineage["prediction_membership"] = {
                "path": str(prediction_path.relative_to(self.project_root))
                if prediction_path.is_relative_to(self.project_root)
                else str(prediction_path),
                "sha256": prediction_sha256,
                "membership_count": len(prediction_members or ()),
                "semantics": "current_snapshot",
            }

        # For PIT training the label coverage gate is against the exact
        # historical union, not the current inference snapshot.  This avoids
        # rejecting valid delisted/relisted names from the training window.
        if pit_store is not None:
            label_universe_members = pit_store.instruments
        elif settings["training_universe"] != settings["inference_universe"]:
            label_universe_members = load_universe_snapshot_members(
                self.project_root, settings["training_universe"], as_of_date
            )
        else:
            label_universe_members = universe_members
        universe_lineage["training_universe_hash"] = compute_universe_hash(
            label_universe_members
        )

        label_store = LabelStore(self.project_root / "data" / "research")
        windows: dict[str, TrainingWindow] = {}
        labels_by_tag: dict[str, pd.DataFrame] = {}
        label_lineage: dict[str, dict[str, Any]] = {}
        for spec in settings["models"]:
            labels = label_store.load_labels(spec["label_id"])
            if labels.empty:
                raise FinancialRCTrainingError(f"empty label: {spec['label_id']}")
            labels["trade_date"] = labels["trade_date"].map(_normalise_date)
            labels["instrument"] = labels["instrument"].astype(str)
            if labels.duplicated(["trade_date", "instrument"]).any():
                raise FinancialRCTrainingError(
                    f"duplicate label rows: {spec['label_id']}"
                )
            label_coverage = profile_label_universe_coverage(
                labels["instrument"].unique().tolist(),
                label_universe_members,
                min_coverage=settings["min_label_universe_coverage"],
            )
            window = derive_training_window(
                open_dates,
                labels["trade_date"].unique().tolist(),
                as_of_date=as_of_date,
                horizon=spec["horizon"],
                window_sessions=spec["window_sessions"],
            )
            windows[spec["tag"]] = window
            labels_by_tag[spec["tag"]] = labels[
                (labels["trade_date"] >= window.label_start)
                & (labels["trade_date"] <= window.label_end)
            ][["trade_date", "instrument", "label_value"]].copy()
            label_path = (
                self.project_root
                / "data"
                / "research"
                / "labels"
                / spec["label_id"]
                / "labels.parquet"
            )
            manifest_path = label_path.with_name("manifest.json")
            label_lineage[spec["tag"]] = {
                "label_file": str(label_path.relative_to(self.project_root)),
                "label_sha256": _file_sha256(label_path),
                "manifest_sha256": _file_sha256(manifest_path),
                "universe_coverage": label_coverage,
            }

        union_start = min(window.train_start for window in windows.values())
        union_end = max(window.train_end for window in windows.values())
        adapter = QlibAdapter(
            qlib_dir=self.project_root / "data" / "qlib_bin",
            raw_dir=self.project_root / "data" / "canonical" / "daily",
        )
        adapter.init_qlib()
        with _project_working_directory(self.project_root):
            raw = adapter.get_features(
                settings["training_universe"],
                features,
                start_time=union_start,
                end_time=union_end,
                margin_lag_sessions=settings["feature_availability"]["margin"][
                    "lag_sessions"
                ],
            )
        if raw is None or raw.empty:
            raise FinancialRCTrainingError(
                f"no features for {settings['training_universe']} [{union_start}, {union_end}]"
            )
        frame = raw.reset_index()
        if "datetime" in frame.columns:
            frame = frame.rename(columns={"datetime": "trade_date"})
        frame = frame.loc[:, ~frame.columns.duplicated()].copy()
        missing = sorted({"trade_date", "instrument", *features} - set(frame.columns))
        if missing:
            raise FinancialRCTrainingError(f"feature frame lacks columns: {missing}")
        frame["trade_date"] = frame["trade_date"].map(_normalise_date)
        frame["instrument"] = frame["instrument"].astype(str)
        margin_lag_sessions = settings["feature_availability"]["margin"][
            "lag_sessions"
        ]
        margin_asof_by_date = {
            trade_date: resolve_lagged_open_session(
                trade_date,
                open_dates,
                margin_lag_sessions,
            )
            for trade_date in frame["trade_date"].unique().tolist()
        }
        frame["margin_asof_date"] = frame["trade_date"].map(margin_asof_by_date)
        if frame.duplicated(["trade_date", "instrument"]).any():
            raise FinancialRCTrainingError("feature frame contains duplicate rows")
        frame = frame.sort_values(["trade_date", "instrument"]).reset_index(drop=True)
        if pit_store is not None:
            frame = _filter_pit_membership(frame, pit_store)
            frame = frame.sort_values(["trade_date", "instrument"]).reset_index(drop=True)

        from qsys.ops.shareholder_sync import inspect_shareholder_sidecar_health

        shareholder_source_lineage: dict[str, dict[str, Any]] = {}
        for spec in settings["models"]:
            tag = spec["tag"]
            source_symbols = sorted(
                frame.loc[
                    frame["trade_date"] == windows[tag].train_end, "instrument"
                ].astype(str).unique().tolist()
            )
            if not source_symbols:
                raise FinancialRCTrainingError(
                    f"no active training-universe rows at train_end for {tag}"
                )
            source_health = inspect_shareholder_sidecar_health(
                project_root=self.project_root,
                symbols=source_symbols,
                as_of_date=windows[tag].train_end,
                contract=settings["shareholder_freshness"],
            )
            if source_health["status"] != "pass":
                raise FinancialRCTrainingError(
                    f"shareholder source freshness failed for {tag}: "
                    + "; ".join(source_health["violations"])
                )
            shareholder_source_lineage[tag] = source_health
        feature_list_hash = _canonical_hash(features)
        git_state = _git_state(self.project_root)
        qlib_latest = adapter.get_last_qlib_date()
        created_at = datetime.now(timezone.utc).isoformat()
        ctx.run_root.mkdir(parents=True, exist_ok=True)
        staging_root = ctx.run_root / ".staging" / uuid.uuid4().hex
        staging_root.mkdir(parents=True, exist_ok=False)

        bundle_models: list[dict[str, Any]] = []
        metrics: dict[str, Any] = {}
        artifacts: dict[str, str] = {}
        try:
            for spec in settings["models"]:
                tag = spec["tag"]
                window = windows[tag]
                window_dates = [
                    day
                    for day in open_dates
                    if window.train_start <= day <= window.train_end
                ]
                next_open = {
                    open_dates[index]: open_dates[index + 1]
                    for index in range(len(open_dates) - 1)
                }
                prepared = frame[frame["trade_date"].isin(window_dates)].copy()
                prepared["label_date"] = prepared["trade_date"].map(next_open)
                prepared = prepared.merge(
                    labels_by_tag[tag].rename(columns={"trade_date": "label_date"}),
                    on=["label_date", "instrument"],
                    how="left",
                    validate="one_to_one",
                )
                for feature in features:
                    prepared[feature] = pd.to_numeric(
                        prepared[feature], errors="coerce"
                    )
                prepared[features] = prepared[features].replace(
                    [np.inf, -np.inf], np.nan
                )
                prepared["label_value"] = pd.to_numeric(
                    prepared["label_value"], errors="coerce"
                )
                prepared = prepared[np.isfinite(prepared["label_value"])].copy()
                prepared = prepared.sort_values(
                    ["trade_date", "instrument"]
                ).reset_index(drop=True)
                if prepared.empty:
                    raise FinancialRCTrainingError(f"no training rows for {tag}")

                shareholder_feature_freshness = (
                    profile_shareholder_feature_freshness(
                        prepared,
                        settings["shareholder_freshness"],
                        date_column="trade_date",
                    )
                )
                if shareholder_feature_freshness["status"] != "pass":
                    raise FinancialRCTrainingError(
                        f"shareholder feature freshness failed for {tag}: "
                        + "; ".join(
                            shareholder_feature_freshness["violations"]
                        )
                    )

                shareholder_rows_before = len(prepared)
                shareholder_row_reasons = prepared.apply(
                    lambda row: shareholder_row_freshness_reasons(
                        row, settings["shareholder_freshness"]
                    ),
                    axis=1,
                )
                prepared = prepared[shareholder_row_reasons.map(lambda value: not value)]
                prepared = prepared.reset_index(drop=True)
                shareholder_rows_dropped = shareholder_rows_before - len(prepared)
                if prepared.empty:
                    raise FinancialRCTrainingError(
                        f"all training rows failed shareholder row freshness for {tag}"
                    )

                missing_ratio = prepared[features].isna().mean()
                feature_coverage = float(1.0 - prepared[features].isna().mean().mean())
                excessive = sorted(
                    missing_ratio[
                        missing_ratio > settings["max_feature_missing_ratio"]
                    ].index.tolist()
                )
                if feature_coverage < settings["min_feature_coverage"] or excessive:
                    raise FinancialRCTrainingError(
                        f"feature quality gate failed for {tag}: "
                        f"coverage={feature_coverage:.4f}, excessive_missing={excessive}"
                    )
                constant = [
                    feature
                    for feature in features
                    if prepared[feature].nunique(dropna=True) < 2
                ]
                if constant:
                    raise FinancialRCTrainingError(
                        f"constant training features for {tag}: {constant}"
                    )

                X = prepared[features].fillna(0.0).astype(np.float32)
                y = prepared["label_value"].astype(float)
                valid_dates = sorted(prepared["trade_date"].unique().tolist())
                if len(valid_dates) <= spec["validation_sessions"]:
                    raise FinancialRCTrainingError(
                        f"not enough dates for {tag} validation holdout"
                    )
                valid_start = valid_dates[-spec["validation_sessions"]]
                evaluation_train_end = derive_purged_evaluation_train_end(
                    open_dates, valid_start, spec["horizon"]
                )
                evaluation_mask = (
                    (prepared["trade_date"] <= evaluation_train_end)
                    | (prepared["trade_date"] >= valid_start)
                )
                evaluation = prepared[evaluation_mask].copy().reset_index(drop=True)
                evaluation_X = (
                    evaluation[features].fillna(0.0).astype(np.float32)
                )
                evaluation_y = evaluation["label_value"].astype(float)
                validation_size = int(
                    (evaluation["trade_date"] >= valid_start).sum()
                )
                evaluation_model, evaluation_center, evaluation_scale = train_model(
                    evaluation_X,
                    evaluation_y,
                    tag,
                    n_estimators=spec["n_estimators"],
                    lgb_params=spec["lgb_params"],
                    validation_size=validation_size,
                )
                selected_iterations = int(
                    evaluation_model.best_iteration or spec["n_estimators"]
                )
                evaluation_predictions = predict_model(
                    evaluation_model,
                    evaluation_center,
                    evaluation_scale,
                    evaluation_X,
                )
                valid_pred = evaluation_predictions.iloc[-validation_size:]
                valid_y = evaluation_y.iloc[-validation_size:]
                valid_frame = evaluation.iloc[-validation_size:][
                    ["trade_date"]
                ].copy()
                valid_frame["prediction"] = valid_pred.to_numpy()
                valid_frame["label"] = valid_y.to_numpy()
                daily_ic = pd.Series(
                    {
                        trade_date: group["prediction"].corr(
                            group["label"], method="spearman"
                        )
                        for trade_date, group in valid_frame.groupby(
                            "trade_date", sort=True
                        )
                    },
                    dtype=float,
                ).dropna()
                if daily_ic.empty:
                    raise FinancialRCTrainingError(
                        f"validation daily RankIC is unavailable for {tag}"
                    )
                pooled_rank_ic = float(valid_pred.corr(valid_y, method="spearman"))
                mse = float(
                    np.mean(
                        np.square(valid_pred.to_numpy() - valid_y.to_numpy())
                    )
                )

                # The evaluation model is used only to select tree count and
                # report an honest purged holdout.  Refit the serving model on
                # every fully matured row with that fixed count.
                model, center, scale = fit_model_fixed_rounds(
                    X,
                    y,
                    tag,
                    n_estimators=selected_iterations,
                    lgb_params=spec["lgb_params"],
                )
                if list(center.index) != features or list(scale.index) != features:
                    raise FinancialRCTrainingError(
                        f"ordered scaler feature contract failed for {tag}"
                    )
                model_stage = staging_root / tag
                model_stage.mkdir(parents=True, exist_ok=False)
                model.save_model(str(model_stage / "model.txt"))
                center.to_json(model_stage / "center.json")
                scale.to_json(model_stage / "scale.json")
                snapshot = prepared[
                    [
                        "trade_date",
                        "instrument",
                        "margin_asof_date",
                        "label_date",
                        "label_value",
                    ]
                ].copy()
                snapshot[features] = X
                snapshot = snapshot[
                    [
                        "trade_date",
                        "instrument",
                        "margin_asof_date",
                        "label_date",
                        "label_value",
                        *features,
                    ]
                ]
                snapshot.to_parquet(
                    model_stage / "training_snapshot.parquet",
                    index=False,
                    compression="zstd",
                )
                model_hash_full = _file_sha256(model_stage / "model.txt")
                model_hash = model_hash_full[:16]
                artifact_hashes = {
                    "model.txt": model_hash_full,
                    "center.json": _file_sha256(model_stage / "center.json"),
                    "scale.json": _file_sha256(model_stage / "scale.json"),
                    "training_snapshot.parquet": _file_sha256(
                        model_stage / "training_snapshot.parquet"
                    ),
                }
                artifact_identity_hash = compute_model_artifact_identity(
                    artifact_hashes=artifact_hashes,
                    feature_list_hash=feature_list_hash,
                    label_lineage=label_lineage[tag],
                    training_config_hash=settings["config_hash"],
                    feature_availability=settings["feature_availability"],
                    universe_lineage=(
                        universe_lineage
                        if (
                            settings["engine"] == "lightgbm_model_bundle_v1"
                            or pit_store is not None
                            or prediction_path is not None
                            or settings["training_universe"]
                            != settings["inference_universe"]
                        )
                        else None
                    ),
                )
                artifact_id = artifact_identity_hash[:16]
                model_metrics = {
                    "validation_daily_rank_ic_mean": float(daily_ic.mean()),
                    "validation_daily_rank_ic_std": float(daily_ic.std(ddof=1)),
                    "validation_daily_rank_ic_positive_ratio": float(
                        (daily_ic > 0).mean()
                    ),
                    "validation_pooled_rank_ic": pooled_rank_ic,
                    "validation_mse": mse,
                    "validation_start": valid_start,
                    "validation_end": valid_dates[-1],
                    "validation_rows": validation_size,
                    "evaluation_train_end": evaluation_train_end,
                    "evaluation_training_rows": len(evaluation) - validation_size,
                    "purge_sessions": spec["horizon"] + 1,
                    "purged_rows": int((~evaluation_mask).sum()),
                    "final_training_rows": len(prepared),
                    "shareholder_rows_dropped": shareholder_rows_dropped,
                    "feature_coverage": feature_coverage,
                    "selected_iterations": selected_iterations,
                }
                meta = {
                    "schema_version": 3,
                    "strategy_id": self.strategy_id,
                    "tag": tag,
                    "model_hash": model_hash,
                    "model_sha256": model_hash_full,
                    "artifact_id": artifact_id,
                    "artifact_identity_hash": artifact_identity_hash,
                    "feature_list_id": settings["feature_list_id"],
                    "feature_list_hash": feature_list_hash,
                    "ordered_features": features,
                    # ``universe`` is the consumer-facing inference universe.
                    # Keep the broader PIT training pool explicit in the
                    # dedicated field below instead of making inference reject
                    # an otherwise compatible model bundle.
                    "universe": settings["inference_universe"],
                    "training_universe": settings["training_universe"],
                    "inference_universe": settings["inference_universe"],
                    "universe_hash": universe_hash,
                    "training_universe_hash": universe_lineage["training_universe_hash"],
                    "universe_lineage": universe_lineage,
                    "pit_universe_artifact": settings["pit_universe_artifact"] or None,
                    "pit_membership_sha256": (
                        pit_provenance.get("membership_sha256")
                        if pit_provenance is not None
                        else None
                    ),
                    "pit_universe_provenance": pit_provenance,
                    "prediction_membership_path": (
                        str(prediction_path.relative_to(self.project_root))
                        if prediction_path is not None
                        and prediction_path.is_relative_to(self.project_root)
                        else str(prediction_path) if prediction_path is not None else None
                    ),
                    "prediction_membership_sha256": prediction_sha256,
                    "universe_snapshot_semantics": (
                        "pit_interval_membership"
                        if pit_store is not None
                        else "current_constituents_snapshot"
                    ),
                    "label_id": spec["label_id"],
                    "horizon": spec["horizon"],
                    "train_start": window.train_start,
                    "train_end": window.train_end,
                    "label_start": window.label_start,
                    "label_end": window.label_end,
                    "as_of_date": as_of_date,
                    "window_sessions": window.window_sessions,
                    "maturity_sessions": window.maturity_sessions,
                    "n_estimators": spec["n_estimators"],
                    "artifact_sha256": artifact_hashes,
                    "label_lineage": label_lineage[tag],
                    "training_config_hash": settings["config_hash"],
                    "feature_availability": settings["feature_availability"],
                    "shareholder_freshness_contract": settings[
                        "shareholder_freshness"
                    ],
                    "shareholder_source_lineage": shareholder_source_lineage[tag],
                    "shareholder_feature_freshness": shareholder_feature_freshness,
                    "metrics": model_metrics,
                    "created_at": created_at,
                    "git": git_state,
                    "qlib_latest": _normalise_date(qlib_latest) if qlib_latest else None,
                    "library_versions": _package_versions(),
                    "research_limitations": [
                        *([] if pit_store is not None else [
                            "historical rows use the current constituent snapshot; not PIT",
                        ]),
                        "validation is a horizon-purged trailing holdout, not a full rolling OOS backtest",
                    ],
                }
                (model_stage / "meta.json").write_text(
                    json.dumps(meta, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
                artifact_hashes["meta.json"] = _file_sha256(model_stage / "meta.json")

                relative_dir = (
                    Path("data")
                    / "research"
                    / "models"
                    / spec["experiment_id"]
                    / artifact_id
                )
                target = self.project_root / relative_dir
                if target.exists():
                    for filename in (
                        "model.txt",
                        "center.json",
                        "scale.json",
                        "training_snapshot.parquet",
                    ):
                        if _file_sha256(target / filename) != artifact_hashes[filename]:
                            raise FinancialRCTrainingError(
                                f"model hash collision at {target}: {filename} differs"
                            )
                    shutil.rmtree(model_stage)
                    artifact_hashes["meta.json"] = _file_sha256(target / "meta.json")
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(model_stage, target)

                required_inference_hashes = {
                    name: artifact_hashes[name]
                    for name in ("model.txt", "center.json", "scale.json", "meta.json")
                }
                bundle_models.append(
                    {
                        "tag": tag,
                        "model_hash": model_hash,
                        "artifact_id": artifact_id,
                        "model_dir": str(relative_dir),
                        "label_id": spec["label_id"],
                        "horizon": spec["horizon"],
                        "artifact_sha256": required_inference_hashes,
                        "training_snapshot_sha256": artifact_hashes[
                            "training_snapshot.parquet"
                        ],
                        "metrics": model_metrics,
                    }
                )
                metrics[tag] = model_metrics
                artifacts[tag] = str(relative_dir)

            bundle_payload = {
                "schema_version": 1,
                "bundle_format": (
                    "lightgbm_model_bundle_v1"
                    if settings["engine"] == "lightgbm_model_bundle_v1"
                    else "financial_rc_bundle_v1"
                ),
                "strategy_id": self.strategy_id,
                "as_of_date": as_of_date,
                "feature_list_id": settings["feature_list_id"],
                "feature_list_hash": feature_list_hash,
                "universe": settings["inference_universe"],
                "training_universe": settings["training_universe"],
                "inference_universe": settings["inference_universe"],
                "universe_hash": universe_hash,
                "training_universe_hash": universe_lineage["training_universe_hash"],
                "universe_lineage": universe_lineage,
                "pit_universe_artifact": settings["pit_universe_artifact"] or None,
                "pit_membership_sha256": (
                    pit_provenance.get("membership_sha256")
                    if pit_provenance is not None
                    else None
                ),
                "pit_universe_provenance": pit_provenance,
                "prediction_membership_path": (
                    str(prediction_path.relative_to(self.project_root))
                    if prediction_path is not None
                    and prediction_path.is_relative_to(self.project_root)
                    else str(prediction_path) if prediction_path is not None else None
                ),
                "prediction_membership_sha256": prediction_sha256,
                "feature_availability": settings["feature_availability"],
                "shareholder_freshness": settings["shareholder_freshness"],
                "models": bundle_models,
                "created_at": created_at,
                "git": git_state,
                "pointer_write_mode": "none",
            }
            bundle_payload["bundle_hash"] = _canonical_hash(bundle_payload)
            # New generic consumers resolve model_bundle.json.  Keep emitting
            # the legacy filename for old financial_rc configs so existing
            # operators and artifact browsers remain compatible.
            bundle_path = ctx.run_root / "model_bundle.json"
            bundle_path.write_text(
                json.dumps(bundle_payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            artifacts["bundle_manifest"] = str(bundle_path)
            if settings["engine"] == "financial_rc_lightgbm_bundle_v1":
                legacy_bundle_path = ctx.run_root / "financial_rc_bundle.json"
                legacy_bundle_path.write_text(
                    json.dumps(bundle_payload, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                # Preserve the legacy result contract while exposing the
                # generic file for consumers that have migrated.
                artifacts["bundle_manifest"] = str(legacy_bundle_path)
                artifacts["model_bundle_manifest"] = str(bundle_path)
                artifacts["legacy_bundle_manifest"] = str(legacy_bundle_path)
        finally:
            if staging_root.exists():
                shutil.rmtree(staging_root)

        return TrainingResult(
            strategy_id=self.strategy_id,
            model_version=str(bundle_payload["bundle_hash"][:16]),
            model_dir=str(ctx.run_root),
            train_start=min(window.train_start for window in windows.values()),
            train_end=max(window.train_end for window in windows.values()),
            valid_start=min(
                value["validation_start"] for value in metrics.values()
            ),
            valid_end=max(value["validation_end"] for value in metrics.values()),
            metrics=metrics,
            artifacts=artifacts,
            message=(
                "Pinned research bundle created without promotion pointer writes; "
                + (
                    "historical universe uses strict PIT interval membership."
                    if pit_store is not None
                    else "historical universe remains current-snapshot, not PIT."
                )
            ),
        )
