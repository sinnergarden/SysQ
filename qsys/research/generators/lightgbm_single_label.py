"""LightGBMSingleLabelGenerator — one label_id -> one LightGBM -> one SignalRun.

Feature cache — reusable materialized frames
--------------------------------------------
First run (write_through=True):
  Window N: qlib adapter -> builder -> frame, clean -> LightGBM training
    └── materialized frame saved to cache/{window_key}.parquet (side effect)

Second run (use_feature_cache=True):
  Window N: read cache/{feature_list_id}/{window_key}.parquet -> LightGBM training
    Builder COMPLETELY skipped. The cache identity binds the source snapshot,
    universe, ordered materialized feature list, date window, schema and builder
    version.  A model may consume an ordered subset without changing the
    artifact key; both column contracts remain explicit in metadata.

Guarantee: a cache hit is accepted only for the exact declared input identity.
"""
from __future__ import annotations

import gc
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from qsys.research.generators.utils import (
    build_next_trading_date_lookup as _build_next_trading_date_lookup,
    build_prev_trading_date_lookup as _build_prev_trading_date_lookup,
    check_training_label_maturity as _check_training_label_maturity,
    cs_zscore as _cs_zscore,
    horizon_from_label_id as _horizon_from_label_id,
)
from qsys.utils.logger import log


_WINDOW_CACHE_SCHEMA_VERSION = 8
_WINDOW_CACHE_BUILDER_ID = (
    "lightgbm_single_label_qlib_frame_v8_materialized_consumed_contracts"
)
_ANNUAL_SHARD_SCHEMA_VERSION = 1
FEATURE_VISIBILITY_CONTRACT = (
    "actual_feature_date_strictly_before_trade_date_v1"
)


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _prediction_membership_identity(path_value: str) -> tuple[str, str, set[str]]:
    """Validate and fingerprint an exact prediction-date membership snapshot.

    The snapshot is intentionally independent from the historical PIT span
    artifact.  It is an immutable, exact instrument set used only for the
    prediction rows.  A symlink is rejected so the identity cannot drift via
    a moving target such as ``latest``.
    """
    path = Path(path_value).expanduser().absolute()
    if path.is_symlink() or not path.is_file():
        raise ValueError(
            f"prediction_membership_path must be an existing regular file: {path}"
        )
    try:
        snapshot = pd.read_parquet(path)
    except Exception as exc:
        raise ValueError(
            f"Failed to read prediction_membership_path: {path}"
        ) from exc
    if "instrument" not in snapshot.columns:
        raise ValueError(
            f"prediction membership snapshot missing required 'instrument' column: {path}"
        )
    if snapshot.empty:
        raise ValueError(f"prediction membership snapshot is empty: {path}")
    instruments = snapshot["instrument"]
    if instruments.isna().any():
        raise ValueError(
            f"prediction membership snapshot contains null instruments: {path}"
        )
    normalized = instruments.astype(str).str.upper()
    if normalized.duplicated().any():
        raise ValueError(
            f"prediction membership snapshot contains duplicate instruments: {path}"
        )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return str(path), digest, set(normalized)


@dataclass
class LightGBMSingleLabelGenerator:
    """Rolling signal generator — trains one LightGBM per label."""

    feature_visibility_contract: str = field(
        default=FEATURE_VISIBILITY_CONTRACT,
        init=False,
    )

    label_id: str = "fwd_ret_5d_xsz_clip3"
    universe: str = "csi300"
    n_estimators: int = 200
    lgb_params: dict | None = None
    # Closed, explicit model-objective experiment policy.  It is deliberately
    # absent from feature-cache identity because feature frames are reusable.
    sample_weight_policy: str | None = None
    # Ordered columns consumed by this model.
    feature_list_id: str | None = None
    # Optional ordered superset physically materialized in the cache.  This is
    # independent of label/model horizon so compatible consumers reuse bytes.
    feature_cache_list_id: str | None = None
    # ── Feature cache options (opt-in) ──
    use_feature_cache: bool = False
    materialize_on_miss: bool = False
    write_through: bool = False  # save per-window cache on first use
    cache_write_scope: str = "window"  # "window" or "annual_shard"
    feature_cache_root: str = "data/feature_cache"
    source_manifest_hash: str = ""
    # Exact open-session delay applied to daily margin source fields before
    # semantic feature construction.  It is part of both cache and model
    # checkpoint identity because changing it changes feature availability.
    margin_lag_sessions: int = 0
    # ── Point-in-Time universe restriction (opt-in) ──
    # When True, rows are restricted to csi800_pit_v2 membership at the row's
    # feature date (trade_date), applied AFTER _load_data so train and predict
    # subsets of the shared frame are filtered identically.  Membership is read
    # from the per-interval artifact spans, never from the qlib registry's
    # collapsed min/max ranges (a stock may leave and re-enter).
    #
    # pit_membership is the LEGACY flag: True → member_as_of on the default
    # artifact (csi800_pit_v2).  For other universes use the new fields:
    #   pit_filter_mode        = "", "member_as_of", "ever_member_as_of"
    #   pit_universe_artifact  = dirname under data/research/universes/
    #   liquidity_exclusion_path = parquet (trade_date, instrument) anti-join
    pit_membership: bool = False
    pit_filter_mode: str = ""
    pit_universe_artifact: str = "csi800_pit_v2"
    liquidity_exclusion_path: str = ""
    # Optional exact current-date snapshot.  Historical training remains
    # member_as_of against ``pit_universe_artifact``; only prediction rows use
    # this exact instrument set.
    prediction_membership_path: str = ""
    # Optional operational registry used only to load the latest prediction
    # feature rows.  The historical PIT-union registry may legitimately end
    # at its last immutable snapshot and must never be extended in place.
    prediction_universe: str = ""
    # Optional immutable shareholder sidecar snapshot.  Production/daily
    # inference may keep using canonical defaults, while long research runs
    # must pin both files and their declared content hashes so checkpoints
    # cannot silently span a historical bootstrap or repair.
    shareholder_holder_path: str = ""
    shareholder_holder_sha256: str = ""
    shareholder_top10_path: str = ""
    shareholder_top10_sha256: str = ""
    shareholder_manifest_path: str = ""
    shareholder_manifest_sha256: str = ""
    # Required when the selected feature list consumes growth-confirmation
    # income fields.  This is one immutable audited bootstrap artifact, never
    # an implicit mutable ``data/tushare/income.parquet`` input.
    income_sidecar_path: str = ""
    income_sidecar_sha256: str = ""
    income_sidecar_manifest_path: str = ""
    income_sidecar_manifest_sha256: str = ""
    income_source_mode: str = "legacy_unverified_global_v0"
    income_sidecar_required_history_start: str = ""
    # Optional research-only contract.  When omitted, preserve the historical
    # generator behaviour (including its existing NaN handling).  When set,
    # it is a read-only fail-closed preflight over the raw feature frame; it
    # never drops rows or changes feature values.
    shareholder_freshness_contract: dict[str, object] | None = None

    _qlib_inited: bool = field(default=False, repr=False)
    _pit_store: object | None = field(default=None, repr=False, init=False)
    _clean_features: list[str] = field(default_factory=list, repr=False)
    _materialized_features: list[str] = field(default_factory=list, repr=False)
    _call_count: int = field(default=0, repr=False)
    _prediction_membership_sha256: str = field(default="", repr=False, init=False)
    _shareholder_source_lineage: dict[str, dict[str, str]] = field(
        default_factory=dict, repr=False, init=False
    )
    _income_source_lineage: dict[str, dict[str, object]] = field(
        default_factory=dict, repr=False, init=False
    )
    _income_source_contract: dict[str, str] = field(
        default_factory=dict, repr=False, init=False
    )
    _shareholder_freshness_profiles: dict[str, dict[str, object]] = field(
        default_factory=dict, repr=False, init=False
    )
    _window_model_diagnostics: list[dict[str, object]] = field(
        default_factory=list, repr=False, init=False
    )
    _feature_cache_code_identity: list[dict[str, str]] | None = field(
        default=None, repr=False, init=False
    )

    def __post_init__(self) -> None:
        from qsys.signal.alpha_v1.training import validate_sample_weight_policy

        validate_sample_weight_policy(self.sample_weight_policy)
        if self.cache_write_scope not in {"window", "annual_shard"}:
            raise ValueError(
                "cache_write_scope must be 'window' or 'annual_shard'"
            )
        if type(self.margin_lag_sessions) is not int or self.margin_lag_sessions < 0:
            raise ValueError("margin_lag_sessions must be a non-negative integer")
        if self.prediction_universe and not self.prediction_membership_path:
            raise ValueError(
                "prediction_universe requires prediction_membership_path"
            )
        self._validate_shareholder_snapshot()
        self._validate_income_snapshot()
        if self.shareholder_freshness_contract is not None:
            from qsys.feature.freshness import normalise_shareholder_freshness

            self.shareholder_freshness_contract = normalise_shareholder_freshness(
                self.shareholder_freshness_contract
            )
        if not self.prediction_membership_path:
            return
        if not self._effective_pit_filter_mode():
            raise ValueError(
                "prediction_membership_path requires an enabled PIT filter "
                "(pit_membership=true or pit_filter_mode)"
            )
        normalized, digest, _ = _prediction_membership_identity(
            self.prediction_membership_path
        )
        self.prediction_membership_path = normalized
        self._prediction_membership_sha256 = digest

    def _validate_shareholder_snapshot(self) -> None:
        values = {
            "shareholder_holder_path": self.shareholder_holder_path,
            "shareholder_holder_sha256": self.shareholder_holder_sha256,
            "shareholder_top10_path": self.shareholder_top10_path,
            "shareholder_top10_sha256": self.shareholder_top10_sha256,
        }
        manifest_values = {
            "shareholder_manifest_path": self.shareholder_manifest_path,
            "shareholder_manifest_sha256": self.shareholder_manifest_sha256,
        }
        if not any((*values.values(), *manifest_values.values())):
            return
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise ValueError(
                "shareholder snapshot requires path and SHA-256 for both files; "
                f"missing {missing}"
            )

        lineage: dict[str, dict[str, str]] = {}
        for name, path_value, declared_hash in (
            ("holder_num", self.shareholder_holder_path, self.shareholder_holder_sha256),
            (
                "top10_holder_ratio",
                self.shareholder_top10_path,
                self.shareholder_top10_sha256,
            ),
        ):
            if len(declared_hash) != 64 or any(
                char not in "0123456789abcdef" for char in declared_hash.lower()
            ):
                raise ValueError(
                    f"{name} shareholder snapshot hash must be SHA-256"
                )
            path = Path(path_value).expanduser().absolute()
            if path.is_symlink() or not path.is_file():
                raise ValueError(
                    f"{name} shareholder snapshot must be an existing regular file: {path}"
                )
            actual_hash = _sha256_file(path)
            if actual_hash != declared_hash.lower():
                raise ValueError(
                    f"{name} shareholder snapshot hash mismatch: "
                    f"declared={declared_hash.lower()} actual={actual_hash}"
                )
            lineage[name] = {
                "path": str(path),
                "sha256": actual_hash,
            }

        self.shareholder_holder_path = lineage["holder_num"]["path"]
        self.shareholder_holder_sha256 = lineage["holder_num"]["sha256"]
        self.shareholder_top10_path = lineage["top10_holder_ratio"]["path"]
        self.shareholder_top10_sha256 = lineage["top10_holder_ratio"]["sha256"]
        if not any(manifest_values.values()):
            # Legacy research remains runnable, but without a terminal-backed
            # manifest this lineage is intentionally not certifiable.
            self._shareholder_source_lineage = lineage
            return
        missing_manifest = [
            key for key, value in manifest_values.items() if not value
        ]
        if missing_manifest:
            raise ValueError(
                "shareholder audited snapshot requires manifest path and SHA-256; "
                f"missing {missing_manifest}"
            )
        declared_manifest_sha = self.shareholder_manifest_sha256.lower()
        if len(declared_manifest_sha) != 64 or any(
            char not in "0123456789abcdef" for char in declared_manifest_sha
        ):
            raise ValueError("shareholder manifest hash must be SHA-256")
        manifest_path = Path(self.shareholder_manifest_path).expanduser().absolute()
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ValueError(
                "shareholder manifest must be an existing regular file: "
                f"{manifest_path}"
            )
        actual_manifest_sha = _sha256_file(manifest_path)
        if actual_manifest_sha != declared_manifest_sha:
            raise ValueError("shareholder manifest hash mismatch")
        if any(Path(item["path"]).parent != manifest_path.parent for item in lineage.values()):
            raise ValueError("shareholder manifest and artifacts must share one directory")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("shareholder manifest is invalid JSON") from exc
        from qsys.ops.shareholder_sync import (
            AUDITED_SNAPSHOT_CONTRACT,
            AUDITED_SNAPSHOT_SCHEMA,
        )

        immutable = manifest.get("identity") if isinstance(manifest, dict) else None
        artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else None
        scope = manifest.get("scope") if isinstance(manifest, dict) else None
        evidence = manifest.get("source_evidence") if isinstance(manifest, dict) else None
        canonical_identity = (
            json.dumps(
                immutable, indent=2, sort_keys=True, ensure_ascii=False, default=str,
            ) + "\n"
        ).encode("utf-8") if isinstance(immutable, dict) else b""
        if (
            manifest.get("schema_version") != 2
            or manifest.get("artifact_type") != AUDITED_SNAPSHOT_SCHEMA
            or not isinstance(immutable, dict)
            or immutable.get("schema") != AUDITED_SNAPSHOT_SCHEMA
            or immutable.get("contract") != AUDITED_SNAPSHOT_CONTRACT
            or manifest.get("artifact_id")
            != hashlib.sha256(canonical_identity).hexdigest()
            or not isinstance(artifacts, dict)
            or not isinstance(scope, dict)
            or not isinstance(evidence, dict)
            or evidence.get("run_id") != immutable.get("source_run_id")
            or evidence.get("terminal_receipt_sha256")
            != immutable.get("terminal_receipt_sha256")
        ):
            raise ValueError("shareholder manifest contract/identity mismatch")
        for name in ("holder_num", "top10_holder_ratio"):
            spec = artifacts.get(name)
            if (
                not isinstance(spec, dict)
                or spec.get("path") != Path(lineage[name]["path"]).name
                or spec.get("sha256") != lineage[name]["sha256"]
            ):
                raise ValueError(
                    f"shareholder manifest artifact identity mismatch: {name}"
                )
        if any(
            scope.get(key) != immutable.get(key)
            for key in (
                "scope_key", "range_start", "range_end", "symbol_count",
                "symbols_sha256",
            )
        ):
            raise ValueError("shareholder manifest scope identity mismatch")
        self.shareholder_manifest_path = str(manifest_path)
        self.shareholder_manifest_sha256 = actual_manifest_sha
        lineage["shareholder_sidecar"] = {
            "path": self.shareholder_manifest_path,
            "sha256": self.shareholder_manifest_sha256,
            "artifact_id": str(manifest["artifact_id"]),
            "source_run_id": str(evidence["run_id"]),
            "terminal_receipt_sha256": str(
                evidence["terminal_receipt_sha256"]
            ),
            "scope_key": str(scope["scope_key"]),
            "range_start": str(scope["range_start"]),
            "range_end": str(scope["range_end"]),
            "symbol_count": int(scope["symbol_count"]),
            "symbols_sha256": str(scope["symbols_sha256"]),
            "transform_contract": AUDITED_SNAPSHOT_CONTRACT,
        }
        self._shareholder_source_lineage = lineage

    def _validate_income_snapshot(self) -> None:
        from qsys.data.income_sidecar import (
            INCOME_SOURCE_MODE_AUDITED,
            normalize_income_feature_source,
            validate_income_sidecar_identity,
        )

        source = normalize_income_feature_source({
            "mode": self.income_source_mode,
            "artifact_path": self.income_sidecar_path,
            "artifact_sha256": self.income_sidecar_sha256,
            "manifest_path": self.income_sidecar_manifest_path,
            "manifest_sha256": self.income_sidecar_manifest_sha256,
            "required_history_start": self.income_sidecar_required_history_start,
        })
        self._income_source_contract = source
        self.income_source_mode = source["mode"]
        self.income_sidecar_required_history_start = source[
            "required_history_start"
        ]
        if source["mode"] != INCOME_SOURCE_MODE_AUDITED:
            return

        identity = validate_income_sidecar_identity(
            artifact_path=source["artifact_path"],
            artifact_sha256=source["artifact_sha256"],
            manifest_path=source["manifest_path"],
            manifest_sha256=source["manifest_sha256"],
            required_history_start=source["required_history_start"],
        )
        manifest = identity["manifest"]
        self.income_sidecar_path = identity["artifact_path"]
        self.income_sidecar_sha256 = identity["artifact_sha256"]
        self.income_sidecar_manifest_path = identity["manifest_path"]
        self.income_sidecar_manifest_sha256 = identity["manifest_sha256"]
        self._income_source_lineage = {
            "income_sidecar": {
                "path": self.income_sidecar_path,
                "sha256": self.income_sidecar_sha256,
                "manifest_path": self.income_sidecar_manifest_path,
                "manifest_sha256": self.income_sidecar_manifest_sha256,
                "artifact_id": manifest["artifact_id"],
                "source_run_id": manifest["source_evidence"]["run_id"],
                "terminal_receipt_sha256": manifest["source_evidence"][
                    "terminal_receipt_sha256"
                ],
                "scope_key": manifest["scope"]["scope_key"],
                "range_start": manifest["scope"]["range_start"],
                "range_end": manifest["scope"]["range_end"],
                "availability_cutoff": manifest["scope"][
                    "availability_cutoff"
                ],
                "required_history_start": manifest["scope"][
                    "required_history_start"
                ],
                "symbol_count": int(manifest["scope"]["symbol_count"]),
                "symbols_sha256": str(manifest["scope"]["symbols_sha256"]),
                "transform_contract": manifest["contracts"]["transform"],
                "financial_availability_contract": manifest["contracts"][
                    "financial_availability"
                ],
                "availability_rule": manifest["contracts"][
                    "availability_rule"
                ],
            }
        }

    @property
    def feature_source_lineage(self) -> dict[str, dict[str, object]]:
        return {
            **self._shareholder_source_lineage,
            **self._income_source_lineage,
        }

    @property
    def materialized_features(self) -> list[str]:
        """Ordered columns represented by the current cache artifact."""
        return list(self._materialized_features)

    @property
    def shareholder_freshness_lineage(self) -> dict[str, object] | None:
        """Return the opt-in contract and per-window preflight evidence."""
        if self.shareholder_freshness_contract is None:
            return None
        return {
            "contract": self.shareholder_freshness_contract,
            "profiles": dict(self._shareholder_freshness_profiles),
        }

    @property
    def model_diagnostics_lineage(self) -> dict[str, object] | None:
        """Return basic, per-window validation and feature-importance evidence."""
        if not self._window_model_diagnostics:
            return None
        return {
            "schema_version": "rolling_model_diagnostics_v1",
            "windows": list(self._window_model_diagnostics),
        }

    @property
    def checkpoint_contract_identity(self) -> dict[str, object]:
        """Contracts that alter the generator's acceptance semantics."""
        contracts: dict[str, object] = {
            "income_feature_source": self._income_source_contract,
            "margin_feature_availability": {
                "lag_sessions": self.margin_lag_sessions,
            },
        }
        if self.shareholder_freshness_contract is not None:
            contracts["shareholder_freshness_contract"] = (
                self.shareholder_freshness_contract
            )
        if "shareholder_sidecar" in self._shareholder_source_lineage:
            contracts["shareholder_sidecar"] = {
                key: value
                for key, value in self._shareholder_source_lineage[
                    "shareholder_sidecar"
                ].items()
                if key not in {"path"}
            }
        return contracts

    @property
    def checkpoint_input_artifacts(self) -> list[dict[str, str]]:
        artifacts = [
            {"name": name, "sha256": payload["sha256"]}
            for name, payload in sorted(self._shareholder_source_lineage.items())
        ]
        if self._income_source_lineage:
            income = self._income_source_lineage["income_sidecar"]
            artifacts.extend([
                {"name": "income_sidecar", "sha256": str(income["sha256"])},
                {
                    "name": "income_sidecar_manifest",
                    "sha256": str(income["manifest_sha256"]),
                },
            ])
        return artifacts

    @property
    def checkpoint_code_dependencies(self) -> dict[str, Path]:
        """Code files whose changes invalidate rolling window checkpoints.

        The generator source hash covers this class, but the model-training
        semantics live in the shared Alpha V1 training module as well.  Keep
        that dependency explicit so a training-only change cannot reuse old
        predictions.  Paths are resolved by the pipeline and only the stable
        dependency name plus content hash enter checkpoint identity.
        """
        from qsys.data import _merge_helpers, adapter
        from qsys.feature import builder, transforms
        from qsys.feature.groups import (
            fundamental_context,
            growth_confirmation_v0,
            liquidity,
            relative_strength,
            value_growth_v3a,
        )
        from qsys.data import income_sidecar
        from qsys.research import pit_universe
        from qsys.signal.alpha_v1 import training

        dependencies = {
            "qsys.data._merge_helpers": Path(_merge_helpers.__file__).resolve(),
            "qsys.data.adapter": Path(adapter.__file__).resolve(),
            "qsys.feature.builder": Path(builder.__file__).resolve(),
            "qsys.feature.transforms": Path(transforms.__file__).resolve(),
            "qsys.feature.groups.fundamental_context": Path(
                fundamental_context.__file__
            ).resolve(),
            "qsys.feature.groups.liquidity": Path(liquidity.__file__).resolve(),
            "qsys.feature.groups.relative_strength": Path(
                relative_strength.__file__
            ).resolve(),
            "qsys.feature.groups.value_growth_v3a": Path(
                value_growth_v3a.__file__
            ).resolve(),
            "qsys.research.pit_universe": Path(pit_universe.__file__).resolve(),
            "qsys.signal.alpha_v1.training": Path(training.__file__).resolve(),
        }
        if self._income_source_lineage:
            dependencies.update({
                "qsys.data.income_sidecar": Path(income_sidecar.__file__).resolve(),
                "qsys.feature.groups.growth_confirmation_v0": Path(
                    growth_confirmation_v0.__file__
                ).resolve(),
            })
        return dependencies

    # ═══════════════════════════════════════════════════════════════
    # Per-window cache: content identity, not date range alone.
    # ═══════════════════════════════════════════════════════════════

    def _cache_identity(
        self,
        start: str,
        end: str,
        features: list[str],
        *,
        consumed_features: list[str] | None = None,
    ) -> dict[str, object]:
        from qsys.data._merge_helpers import (
            FINANCIAL_AVAILABILITY_CONTRACT,
            TUSHARE_FINA_INDICATOR_UNIT_CONTRACT,
        )

        from qsys.feature.registry import FeatureListRegistry

        mode = self._effective_pit_filter_mode()
        membership_hash = ""
        if mode:
            try:
                from qsys.research.pit_universe import PitUniverseStore

                membership_hash = PitUniverseStore(
                    self.pit_universe_artifact
                ).provenance.membership_sha256
            except FileNotFoundError:
                membership_hash = "missing"
        exclusion_hash = ""
        if self.liquidity_exclusion_path:
            exclusion_path = Path(self.liquidity_exclusion_path)
            if exclusion_path.is_file():
                exclusion_hash = hashlib.sha256(
                    exclusion_path.read_bytes()
                ).hexdigest()
            else:
                exclusion_hash = "missing"
        prediction_path = ""
        prediction_hash = ""
        if self.prediction_membership_path:
            prediction_path, prediction_hash, _ = _prediction_membership_identity(
                self.prediction_membership_path
            )
        if self._feature_cache_code_identity is None:
            dependencies = {
                "qsys.research.generators.lightgbm_single_label": Path(__file__),
                **{
                    name: path
                    for name, path in self.checkpoint_code_dependencies.items()
                    if name != "qsys.signal.alpha_v1.training"
                },
            }
            self._feature_cache_code_identity = [
                {"name": name, "sha256": _sha256_file(Path(path))}
                for name, path in sorted(dependencies.items())
            ]
        code_payload = json.dumps(
            self._feature_cache_code_identity,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        consumed = list(features if consumed_features is None else consumed_features)
        feature_list_contract = (
            {
                key: value
                for key, value in FeatureListRegistry.contract(
                    self.feature_list_id
                ).items()
                if key != "features"
            }
            if self.feature_list_id
            else None
        )
        materialized_feature_list_id = (
            self.feature_cache_list_id or self.feature_list_id
        )
        materialized_feature_list_contract = (
            {
                key: value
                for key, value in FeatureListRegistry.contract(
                    materialized_feature_list_id
                ).items()
                if key != "features"
            }
            if materialized_feature_list_id
            else None
        )
        column_contract = {
            "key_columns": ["trade_date", "instrument"],
            "materialized_features": list(features),
            "stored_columns": ["trade_date", "instrument", *features],
            "consumed_features": consumed,
        }
        identity = {
            "schema_version": _WINDOW_CACHE_SCHEMA_VERSION,
            "builder_id": _WINDOW_CACHE_BUILDER_ID,
            "builder_code_sha256": hashlib.sha256(code_payload).hexdigest(),
            "builder_code_dependencies": self._feature_cache_code_identity,
            "canonical_financial_contracts": {
                "availability": FINANCIAL_AVAILABILITY_CONTRACT,
                "fina_indicator_units": TUSHARE_FINA_INDICATOR_UNIT_CONTRACT,
            },
            "feature_availability_contracts": {
                "margin": {"lag_sessions": self.margin_lag_sessions},
            },
            "feature_history_contract": (
                "continuous_listed_history_member_only_cross_section_v1"
            ),
            "source_manifest_hash": self.source_manifest_hash,
            "universe": self.universe,
            "feature_list_id": self.feature_list_id,
            "feature_list_contract": feature_list_contract,
            "feature_cache_list_id": materialized_feature_list_id,
            "materialized_feature_list_contract": (
                materialized_feature_list_contract
            ),
            "features": list(features),
            "column_contract": column_contract,
            "pit_membership": self.pit_membership,
            "pit_filter_mode": mode,
            "pit_universe_artifact": self.pit_universe_artifact if mode else "",
            "pit_membership_sha256": membership_hash,
            "liquidity_exclusion_path": self.liquidity_exclusion_path,
            "liquidity_exclusion_sha256": exclusion_hash,
            "prediction_membership_path": prediction_path,
            "prediction_membership_sha256": prediction_hash,
            "prediction_universe": self.prediction_universe,
            "start": start,
            "end": end,
        }
        if self.shareholder_freshness_contract is not None:
            identity["shareholder_freshness_contract"] = (
                self.shareholder_freshness_contract
            )
        if self._shareholder_source_lineage:
            identity["shareholder_source_artifacts"] = {
                name: str(payload["sha256"])
                for name, payload in sorted(
                    self._shareholder_source_lineage.items()
                )
            }
        if self._income_source_lineage:
            income = self._income_source_lineage["income_sidecar"]
            identity["income_sidecar_sha256"] = income["sha256"]
            identity["income_sidecar_manifest_sha256"] = income[
                "manifest_sha256"
            ]
        identity["income_source_mode"] = self.income_source_mode
        identity["income_required_history_start"] = (
            self.income_sidecar_required_history_start
        )
        return identity

    @staticmethod
    def _cache_artifact_identity(
        identity: dict[str, object],
    ) -> dict[str, object]:
        """Return only fields that define the physically stored frame.

        Consumer-specific identity remains auditable in metadata, but cannot
        fork identical materialized bytes into one cache file per model.
        """
        artifact = dict(identity)
        artifact.pop("feature_list_id", None)
        artifact.pop("feature_list_contract", None)
        column_contract = dict(artifact.get("column_contract", {}))
        column_contract.pop("consumed_features", None)
        artifact["column_contract"] = column_contract
        return artifact

    def _window_key(
        self,
        start: str,
        end: str,
        features: list[str],
        *,
        consumed_features: list[str] | None = None,
    ) -> str:
        identity = self._cache_identity(
            start,
            end,
            features,
            consumed_features=consumed_features,
        )
        raw = json.dumps(
            self._cache_artifact_identity(identity),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def _window_cache_dir(self) -> Path:
        """All windows share one directory."""
        return Path(self.feature_cache_root) / "per_window"

    def _window_cache_path(
        self, start: str, end: str, features: list[str]
    ) -> Path:
        return self._window_cache_dir() / f"{self._window_key(start, end, features)}.parquet"

    def _window_meta_path(
        self, start: str, end: str, features: list[str]
    ) -> Path:
        return Path(str(self._window_cache_path(start, end, features)) + ".meta.json")

    def _window_has_cache(
        self, start: str, end: str, features: list[str]
    ) -> bool:
        path = self._window_cache_path(start, end, features)
        return path.exists() and self._window_meta_path(start, end, features).exists()

    def _annual_shard_dir(self) -> Path:
        return Path(self.feature_cache_root) / "annual_shards"

    def _annual_shard_path(
        self, start: str, end: str, features: list[str]
    ) -> Path:
        return self._annual_shard_dir() / f"{self._window_key(start, end, features)}.parquet"

    def _annual_shard_meta_path(
        self, start: str, end: str, features: list[str]
    ) -> Path:
        return Path(str(self._annual_shard_path(start, end, features)) + ".meta.json")

    @staticmethod
    def _cache_identity_without_range(identity: dict[str, object]) -> dict[str, object]:
        artifact = LightGBMSingleLabelGenerator._cache_artifact_identity(identity)
        return {
            key: value
            for key, value in artifact.items()
            if key not in {"start", "end"}
        }

    @staticmethod
    def _annual_ranges(start: str, end: str) -> list[tuple[str, str]]:
        start_year = int(start[:4])
        end_year = int(end[:4])
        return [
            (f"{year:04d}-01-01", f"{year:04d}-12-31")
            for year in range(start_year, end_year + 1)
        ]

    def _read_cache_frame(
        self,
        path: Path,
        features: list[str],
        *,
        expected_data_sha256: str | None = None,
        expected_rows: int | None = None,
        expected_cols: int | None = None,
    ) -> pd.DataFrame | None:
        if not path.is_file():
            return None
        if expected_data_sha256:
            actual = _sha256_file(path)
            if actual != expected_data_sha256:
                return None
        try:
            df = pd.read_parquet(path)
        except Exception:
            return None
        if expected_rows is not None and len(df) != expected_rows:
            return None
        if expected_cols is not None and len(df.columns) != expected_cols:
            return None
        if "trade_date" not in df.columns or "instrument" not in df.columns:
            return None
        needed = {"trade_date", "instrument"} | set(features)
        if needed - set(df.columns):
            return None
        df = df[["trade_date", "instrument", *features]].copy()
        df["trade_date"] = df["trade_date"].astype(str).str[:10]
        return df

    def _load_annual_shard_cache(
        self,
        start: str,
        end: str,
        features: list[str],
        *,
        consumed_features: list[str] | None = None,
    ) -> pd.DataFrame | None:
        """Compose complete calendar-year shards, or return None on any miss."""
        consumed = list(features if consumed_features is None else consumed_features)
        ranges = self._annual_ranges(start, end)
        pieces: list[pd.DataFrame] = []
        expected_base: dict[str, object] | None = None
        for shard_start, shard_end in ranges:
            identity = self._cache_identity(
                shard_start,
                shard_end,
                features,
                consumed_features=consumed,
            )
            artifact_identity = self._cache_artifact_identity(identity)
            if expected_base is None:
                expected_base = self._cache_identity_without_range(identity)
            elif self._cache_identity_without_range(identity) != expected_base:
                return None
            meta_path = self._annual_shard_meta_path(shard_start, shard_end, features)
            path = self._annual_shard_path(shard_start, shard_end, features)
            if not meta_path.is_file():
                return None
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                return None
            if meta.get("schema_version") != _ANNUAL_SHARD_SCHEMA_VERSION:
                return None
            stored_identity = meta.get("identity")
            if (
                meta.get("artifact_identity") != artifact_identity
                or not isinstance(stored_identity, dict)
                or self._cache_artifact_identity(stored_identity)
                != artifact_identity
            ):
                return None
            coverage_start = str(meta.get("source_coverage_start", shard_start))
            coverage_end = str(meta.get("source_coverage_end", shard_end))
            required_start = max(start, shard_start)
            required_end = min(end, shard_end)
            if coverage_start > required_start or coverage_end < required_end:
                return None
            piece = self._read_cache_frame(
                path,
                features,
                expected_data_sha256=meta.get("data_sha256"),
                expected_rows=meta.get("rows"),
                expected_cols=meta.get("cols"),
            )
            if piece is None:
                return None
            if not piece["trade_date"].between(shard_start, shard_end).all():
                return None
            pieces.append(piece)
        if not pieces:
            return None
        result = pd.concat(pieces, ignore_index=True)
        result = result[
            (result["trade_date"] >= start) & (result["trade_date"] <= end)
        ]
        if result.duplicated(subset=["trade_date", "instrument"]).any():
            raise ValueError("annual feature cache contains duplicate instrument/date keys")
        result = result.sort_values(
            ["instrument", "trade_date"], kind="mergesort"
        ).reset_index(drop=True)
        if result.empty:
            return None
        result = result[["trade_date", "instrument", *consumed]].copy()
        log.info(
            "Annual feature cache composed [{}, {}] from {} shards ({} rows)",
            start,
            end,
            len(pieces),
            len(result),
        )
        return result

    def _write_cache_frame(
        self,
        frame: pd.DataFrame,
        start: str,
        end: str,
        features: list[str],
        *,
        consumed_features: list[str] | None = None,
        source_coverage_start: str | None = None,
        source_coverage_end: str | None = None,
    ) -> Path:
        consumed = list(features if consumed_features is None else consumed_features)
        missing = [feature for feature in features if feature not in frame.columns]
        if missing:
            raise ValueError(
                "feature cache frame is missing materialized columns: "
                f"{missing}"
            )
        identity = self._cache_identity(
            start,
            end,
            features,
            consumed_features=consumed,
        )
        artifact_identity = self._cache_artifact_identity(identity)
        out = frame[["trade_date", "instrument", *features]].copy()
        if self.cache_write_scope == "annual_shard":
            path = self._annual_shard_path(start, end, features)
            meta_path = self._annual_shard_meta_path(start, end, features)
            meta = {
                "schema_version": _ANNUAL_SHARD_SCHEMA_VERSION,
                "identity": identity,
                "artifact_identity": artifact_identity,
                "rows": len(out),
                "cols": len(out.columns),
                "source_coverage_start": source_coverage_start or start,
                "source_coverage_end": source_coverage_end or end,
            }
        else:
            path = self._window_cache_path(start, end, features)
            meta_path = self._window_meta_path(start, end, features)
            meta = {
                **identity,
                "artifact_identity": artifact_identity,
                "window_key": self._window_key(start, end, features),
                "rows": len(out),
                "cols": len(out.columns),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        path.parent.mkdir(parents=True, exist_ok=True)
        data_fd, data_tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".parquet.tmp", dir=str(path.parent)
        )
        os.close(data_fd)
        try:
            out.to_parquet(data_tmp_name, index=False)
            with open(data_tmp_name, "rb") as handle:
                os.fsync(handle.fileno())
            os.replace(data_tmp_name, path)
            _fsync_directory(path.parent)
        finally:
            if os.path.exists(data_tmp_name):
                os.unlink(data_tmp_name)
        meta["data_sha256"] = _sha256_file(path)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{meta_path.name}.", suffix=".tmp", dir=str(meta_path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(meta, handle, indent=2, ensure_ascii=False, default=str)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, meta_path)
            _fsync_directory(meta_path.parent)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        return path

    # ═══════════════════════════════════════════════════════════════
    # Data loader
    # ═══════════════════════════════════════════════════════════════

    def _ensure_qlib(self) -> None:
        if not self._qlib_inited:
            from qsys.data.adapter import QlibAdapter
            QlibAdapter(
                shareholder_holder_path=self.shareholder_holder_path or None,
                shareholder_top10_path=self.shareholder_top10_path or None,
                income_sidecar_path=self.income_sidecar_path or None,
                income_sidecar_sha256=self.income_sidecar_sha256,
                income_sidecar_manifest_path=(
                    self.income_sidecar_manifest_path or None
                ),
                income_sidecar_manifest_sha256=(
                    self.income_sidecar_manifest_sha256
                ),
                income_source_mode=self.income_source_mode,
                income_sidecar_required_history_start=(
                    self.income_sidecar_required_history_start
                ),
            ).init_qlib()
            self._qlib_inited = True

    def _load_data(self, start: str, end: str) -> tuple[pd.DataFrame, list[str]]:
        from qsys.feature.registry import FeatureListRegistry

        if self.feature_list_id:
            consumed = FeatureListRegistry.load(self.feature_list_id)
        else:
            from qsys.feature.registry import get_feature_fields
            from qsys.strategy.alpha_v1.spec import get_clean_features
            all_feats = get_feature_fields("semantic_all_features")
            consumed = get_clean_features(all_feats)
        materialized_feature_list_id = (
            self.feature_cache_list_id or self.feature_list_id
        )
        materialized = (
            FeatureListRegistry.load(materialized_feature_list_id)
            if materialized_feature_list_id
            else list(consumed)
        )
        if len(set(materialized)) != len(materialized):
            raise ValueError("Materialized feature list contains duplicate columns")
        if len(set(consumed)) != len(consumed):
            raise ValueError("Consumed feature list contains duplicate columns")
        positions = {feature: index for index, feature in enumerate(materialized)}
        missing = [feature for feature in consumed if feature not in positions]
        indices = [positions[feature] for feature in consumed if feature in positions]
        if missing or indices != sorted(indices):
            raise ValueError(
                "Consumed feature list must be an ordered subset of the "
                f"materialized cache list; missing={missing}"
            )
        self._clean_features = consumed
        self._materialized_features = materialized

        if self.use_feature_cache:
            if not self.feature_list_id:
                raise ValueError("Feature cache requires an explicit feature_list_id")
            if not self.source_manifest_hash.strip():
                raise ValueError(
                    "Feature cache requires a non-empty source_manifest_hash; "
                    "date-only cache reuse is forbidden"
                )

        # ── Cache hit: read per-window parquet → return directly ──
        if (
            self.use_feature_cache
            and self.cache_write_scope == "window"
            and self._window_has_cache(start, end, materialized)
        ):
            path = self._window_cache_path(start, end, materialized)
            meta_path = self._window_meta_path(start, end, materialized)
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            expected_identity = self._cache_identity(
                start,
                end,
                materialized,
                consumed_features=consumed,
            )
            expected_artifact_identity = self._cache_artifact_identity(
                expected_identity
            )
            stored_identity = {
                key: meta.get(key) for key in expected_identity
            }
            if (
                meta.get("artifact_identity") != expected_artifact_identity
                or self._cache_artifact_identity(stored_identity)
                != expected_artifact_identity
            ):
                raise ValueError(
                    "Window cache materialization identity mismatch"
                )
            df = self._read_cache_frame(
                path,
                materialized,
                expected_data_sha256=meta.get("data_sha256"),
                expected_rows=meta.get("rows"),
                expected_cols=meta.get("cols"),
            )
            if df is None:
                raise ValueError(
                    f"Cache missing or malformed features needed by '{self.feature_list_id}'. "
                    "Re-run with write_through=True for this exact feature list."
                )

            df = df[["trade_date", "instrument", *consumed]].copy()
            log.info("Cache HIT: {} ({} rows x {} cols, subset={} feats)",
                     path.name, len(df), len(df.columns), len(consumed))
            return df, consumed

        if self.use_feature_cache:
            composed = self._load_annual_shard_cache(
                start,
                end,
                materialized,
                consumed_features=consumed,
            )
            if composed is not None:
                return composed, consumed

        # ── Original qlib path (cache miss or disabled) ──
        self._call_count += 1
        log.info("Loading qlib data [{}, {}] (call #{})", start, end, self._call_count)

        from qsys.data.adapter import QlibAdapter
        adapter = QlibAdapter(
            shareholder_holder_path=self.shareholder_holder_path or None,
            shareholder_top10_path=self.shareholder_top10_path or None,
            income_sidecar_path=self.income_sidecar_path or None,
            income_sidecar_sha256=self.income_sidecar_sha256,
            income_sidecar_manifest_path=(
                self.income_sidecar_manifest_path or None
            ),
            income_sidecar_manifest_sha256=self.income_sidecar_manifest_sha256,
            income_source_mode=self.income_source_mode,
            income_sidecar_required_history_start=(
                self.income_sidecar_required_history_start
            ),
        )

        feature_instruments: str | list[str] = self.universe
        semantic_spans: pd.DataFrame | None = None
        semantic_mode = self._effective_pit_filter_mode()
        if semantic_mode:
            if self._pit_store is None:
                from qsys.research.pit_universe import PitUniverseStore

                self._pit_store = PitUniverseStore(self.pit_universe_artifact)
            # Materialize time-series features from continuous listed history
            # for every symbol that ever appears in the frozen PIT artifact.
            # The adapter carries the spans as a separate mask so same-date
            # ranks/z-scores still use only the eligible PIT cross-section.
            feature_instruments = self._pit_store.instruments
            semantic_spans = self._pit_store.spans

        # Build features via qlib + phase1 builder
        raw = adapter.get_features(
            feature_instruments,
            list(dict.fromkeys([*materialized, "$close"])),
            start_time=start,
            end_time=end,
            margin_lag_sessions=self.margin_lag_sessions,
            semantic_pit_membership_spans=semantic_spans,
            semantic_pit_filter_mode=semantic_mode,
        )
        frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
        frame = frame.loc[:, ~frame.columns.duplicated()]
        frame["trade_date"] = frame["trade_date"].astype(str).str[:10]
        if "instrument" not in frame.columns and "ts_code" in frame.columns:
            frame = frame.rename(columns={"ts_code": "instrument"})

        # ── Write-through: save this window's frame to per-window cache ──
        if self.use_feature_cache and (
            self.materialize_on_miss or self.write_through
        ):
            path = self._write_cache_frame(
                frame,
                start,
                end,
                materialized,
                consumed_features=consumed,
            )

            log.info("Cache WRITTEN: {} ({} rows x {} cols, {:.1f} MB)",
                     path.name, len(frame), len(frame.columns), path.stat().st_size / 1024 / 1024)

        return frame, consumed

    def _effective_pit_filter_mode(self) -> str:
        """Resolve the active filter mode from the new + legacy fields.

        ``pit_filter_mode`` wins; otherwise the legacy ``pit_membership``
        boolean maps to member_as_of.  Empty means no PIT restriction.
        """
        if self.pit_filter_mode:
            return self.pit_filter_mode
        return "member_as_of" if self.pit_membership else ""

    def _apply_pit_membership(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Restrict rows to a PIT membership artifact at each row's feature date.

        Dispatch on ``pit_filter_mode`` (defaults to member_as_of when the
        legacy ``pit_membership`` flag is set, preserving old behaviour):

        - ``member_as_of``: keep rows whose trade_date falls inside a span of
          the artifact (``PitUniverseStore(pit_universe_artifact)``).
        - ``ever_member_as_of``: keep rows whose trade_date >= the earliest
          span effective_from (ever-member monotonic; idempotent, ignores
          effective_to).
        - ``""``: no membership filter.

        After the span filter, if ``liquidity_exclusion_path`` is set, its
        ``(trade_date, instrument)`` rows are anti-joined (U3 diagnostic).

        Membership is read from the per-interval artifact spans, never from
        the qlib registry's collapsed min/max ranges — a stock that left the
        index and re-entered must be excluded during its non-member gap.
        Applied once, right after _load_data, so the train and predict
        subsets of the shared frame see identical rows (PIT semantics apply
        to training data too, per audit Section 17).
        """
        mode = self._effective_pit_filter_mode()
        if mode == "":
            return frame

        if self._pit_store is None:
            from qsys.research.pit_universe import PitUniverseStore
            self._pit_store = PitUniverseStore(self.pit_universe_artifact)

        spans = self._pit_store.spans[
            ["instrument", "effective_from", "effective_to"]
        ].rename(
            columns={"effective_from": "_eff_from", "effective_to": "_eff_to"}
        )
        spans["_eff_from"] = spans["_eff_from"].astype(int)
        spans["_eff_to"] = spans["_eff_to"].astype(int)

        merged = frame.merge(spans, on="instrument", how="inner")
        if merged.empty:
            raise ValueError(
                "pit_membership: no rows matched any membership span — "
                "check universe registry vs PIT artifact symbol format"
            )
        date_int = (
            merged["trade_date"].astype(str).str.replace("-", "", regex=False).astype(int)
        )
        if mode == "ever_member_as_of":
            keep_mask = date_int >= merged["_eff_from"]
        else:  # member_as_of
            keep_mask = (date_int >= merged["_eff_from"]) & (date_int <= merged["_eff_to"])
        keep = merged.loc[keep_mask, frame.columns].drop_duplicates()

        if self.liquidity_exclusion_path and not keep.empty:
            exclusions = pd.read_parquet(self.liquidity_exclusion_path)
            exclusions["trade_date"] = exclusions["trade_date"].astype(str).str[:10]
            exclusions["instrument"] = exclusions["instrument"].astype(str).str.upper()
            key = pd.MultiIndex.from_arrays(
                [keep["trade_date"].astype(str).str[:10], keep["instrument"]]
            )
            excl = pd.MultiIndex.from_arrays(
                [exclusions["trade_date"], exclusions["instrument"]]
            )
            keep = keep[~key.isin(excl)]
            log.info(
                "liquidity exclusion anti-join: {} -> {} rows",
                len(key), len(keep),
            )

        n_dropped = len(frame) - len(keep)
        log.info(
            "pit_membership filter [mode={}, artifact={}]: {} -> {} rows "
            "(dropped {})",
            mode, self.pit_universe_artifact, len(frame), len(keep), n_dropped,
        )
        if keep.empty:
            raise ValueError("pit_membership: no rows remain after membership filter")
        return keep

    def _apply_prediction_membership(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Restrict prediction rows to the exact immutable snapshot set."""
        if not self.prediction_membership_path:  # pragma: no cover - defensive
            return frame
        path, digest, instruments = _prediction_membership_identity(
            self.prediction_membership_path
        )
        if self._prediction_membership_sha256 and digest != self._prediction_membership_sha256:
            raise ValueError(
                "prediction membership snapshot changed after generator initialization: "
                f"{path}"
            )
        keep = frame["instrument"].astype(str).str.upper().isin(instruments)
        result = frame.loc[keep].copy()
        if result.empty:
            raise ValueError(
                "prediction membership snapshot has no matching prediction rows: "
                f"{path}"
            )
        return result

    def _check_shareholder_freshness(
        self,
        frame: pd.DataFrame,
        *,
        role: str,
        start: str,
        end: str,
    ) -> None:
        """Fail closed on an opted-in shareholder contract without mutation.

        This deliberately runs on the constructed feature frame, before label
        joins and before the legacy ``fillna(0)`` model-input conversion.  The
        returned profile is evidence only; no rows or values are changed.
        """
        contract = self.shareholder_freshness_contract
        if contract is None:
            return
        from qsys.feature.freshness import profile_shareholder_feature_freshness

        profile = profile_shareholder_feature_freshness(
            frame[
                (frame["trade_date"] >= start) & (frame["trade_date"] <= end)
            ],
            contract,
            date_column="trade_date",
        )
        key = f"{role}:{start}:{end}"
        self._shareholder_freshness_profiles[key] = profile
        if profile["status"] != "pass":
            violations = "; ".join(profile["violations"])
            raise ValueError(
                "shareholder feature freshness failed for "
                f"{role} window [{start}, {end}]: {violations}"
            )

    def _load_prediction_data(
        self,
        start: str,
        end: str,
        clean_features: list[str],
    ) -> pd.DataFrame:
        """Load latest feature rows from the prediction-only registry."""
        if not self.prediction_universe:
            raise ValueError("prediction_universe is not configured")
        from qsys.data.adapter import QlibAdapter

        raw = QlibAdapter(
            shareholder_holder_path=self.shareholder_holder_path or None,
            shareholder_top10_path=self.shareholder_top10_path or None,
            income_sidecar_path=self.income_sidecar_path or None,
            income_sidecar_sha256=self.income_sidecar_sha256,
            income_sidecar_manifest_path=(
                self.income_sidecar_manifest_path or None
            ),
            income_sidecar_manifest_sha256=self.income_sidecar_manifest_sha256,
            income_source_mode=self.income_source_mode,
            income_sidecar_required_history_start=(
                self.income_sidecar_required_history_start
            ),
        ).get_features(
            self.prediction_universe,
            clean_features + ["$close"],
            start_time=start,
            end_time=end,
            margin_lag_sessions=self.margin_lag_sessions,
        )
        frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
        frame = frame.loc[:, ~frame.columns.duplicated()]
        if "trade_date" not in frame.columns:
            raise ValueError(
                f"prediction_universe {self.prediction_universe!r} returned no feature rows"
            )
        frame["trade_date"] = frame["trade_date"].astype(str).str[:10]
        if "instrument" not in frame.columns and "ts_code" in frame.columns:
            frame = frame.rename(columns={"ts_code": "instrument"})
        return frame

    # ═══════════════════════════════════════════════════════════════
    # Training + prediction
    # ═══════════════════════════════════════════════════════════════

    def _train_window_model(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        label_dates: pd.Series,
        *,
        window_id: str,
    ):
        from qsys.signal.alpha_v1.training import (
            compute_train_partition_sample_weight,
            resolve_validation_size,
            train_model,
        )

        validation_size = resolve_validation_size(len(y_train))
        sample_weight = compute_train_partition_sample_weight(
            y_train,
            label_dates,
            self.sample_weight_policy,
            validation_size=validation_size,
        )
        model, center, scale = train_model(
            X_train,
            y_train,
            "window",
            n_estimators=self.n_estimators,
            lgb_params=self.lgb_params,
            validation_size=validation_size,
            sample_weight=sample_weight,
        )
        validation_pred = self._predict_window_model(
            model,
            center,
            scale,
            X_train.iloc[-validation_size:],
        )
        validation_rank_ic = validation_pred.corr(
            y_train.iloc[-validation_size:], method="spearman"
        )
        feature_importance = getattr(model, "feature_importance", None)
        if callable(feature_importance):
            gain = feature_importance(importance_type="gain")
            split = feature_importance(importance_type="split")
        else:
            gain = np.zeros(len(X_train.columns), dtype=float)
            split = np.zeros(len(X_train.columns), dtype=int)
        best_iteration = getattr(model, "best_iteration", None)
        self._window_model_diagnostics.append({
            "window_id": window_id,
            "model_type": "lightgbm_regression",
            "train_rows": int(len(y_train) - validation_size),
            "validation_rows": validation_size,
            "validation_rank_ic": (
                float(validation_rank_ic) if pd.notna(validation_rank_ic) else None
            ),
            "best_iteration": int(best_iteration or self.n_estimators),
            "feature_importance_gain": {
                feature: float(value) for feature, value in zip(X_train.columns, gain)
            },
            "feature_importance_split": {
                feature: int(value) for feature, value in zip(X_train.columns, split)
            },
        })
        return model, center, scale

    @staticmethod
    def _predict_window_model(model, center, scale, X_predict: pd.DataFrame) -> pd.Series:
        from qsys.signal.alpha_v1.training import predict_model

        return predict_model(model, center, scale, X_predict)

    @staticmethod
    def _release_window_model(model) -> None:
        free_dataset = getattr(model, "free_dataset", None)
        if callable(free_dataset):
            free_dataset()

    def generate(
        self,
        *,
        train_start: str,
        train_end: str,
        predict_start: str,
        predict_end: str,
        signal_id: str,
        signal_run_id: str,
    ) -> pd.DataFrame:
        self._ensure_qlib()

        from qsys.data.calendar import get_trading_calendar

        window_cal = get_trading_calendar(predict_start, predict_end)
        prev_td = _build_prev_trading_date_lookup(predict_start, predict_end)
        feature_dates = sorted({prev_td.get(d, d) for d in window_cal})
        # The execution window consumes only ``prev_td(d)`` for dates
        # ``d <= predict_end``.  Loading an extra future month cannot affect
        # those past-only features, but it incorrectly expands frozen source
        # coverage requirements beyond the experiment terminal.
        load_end = train_end if self.prediction_universe else predict_end

        log.info("Loading data [{}, {}]", train_start, load_end)
        frame, clean_features = self._load_data(train_start, load_end)

        # Historical train rows and current prediction rows can use different
        # PIT artifacts.  Preserve the old single-frame behavior when no exact
        # prediction snapshot is supplied.
        if self.prediction_membership_path:
            train_frame = self._apply_pit_membership(frame)
            if self.prediction_universe:
                prediction_frame = self._load_prediction_data(
                    min(feature_dates), max(feature_dates), clean_features,
                )
            else:
                prediction_frame = frame
            prediction_frame = self._apply_prediction_membership(prediction_frame)
        else:
            if self._effective_pit_filter_mode():
                frame = self._apply_pit_membership(frame)
            train_frame = frame
            prediction_frame = frame

        # Gate both sides of the rolling window after PIT membership has been
        # applied and before labels/model preprocessing.  Prediction dates are
        # the feature dates actually consumed by this execution window.
        self._check_shareholder_freshness(
            train_frame,
            role="train",
            start=train_start,
            end=train_end,
        )
        self._check_shareholder_freshness(
            prediction_frame,
            role="predict",
            start=min(feature_dates),
            end=max(feature_dates),
        )

        from qsys.label.store import LabelStore
        label_df = LabelStore().load_labels(self.label_id)

        # Train
        log.info("Training window: {} -> {}", train_start, train_end)
        # F01 (Option A, strict): features at date f are paired with the forward
        # return that starts on the NEXT trading day (the actual buy day's
        # close-to-close proxy), matching inference where trade_date = next_td(f).
        # Removes the same-day-close lookahead from research signal generation.
        next_td = _build_next_trading_date_lookup(train_start, train_end)
        # F01/F16: with the label shifted to next_td(f), enforce that no
        # training label extends into the predict window (fail loudly).
        _check_training_label_maturity(
            train_end, predict_start, _horizon_from_label_id(self.label_id),
        )
        train = train_frame[
            (train_frame["trade_date"] >= train_start)
            & (train_frame["trade_date"] <= train_end)
        ].copy()
        train["label_date"] = train["trade_date"].map(next_td)
        train = train.merge(
            label_df[["trade_date", "instrument", "label_value"]].rename(
                columns={"trade_date": "label_date"}),
            on=["label_date", "instrument"], how="left",
        )

        y_valid = train["label_value"].notna()
        X_tr = train[clean_features].fillna(0.0).astype(np.float32)
        y_tr = train.loc[y_valid, "label_value"].astype(float)
        if y_tr.empty:
            raise ValueError(f"No valid training samples for {self.label_id}")
        model, center, scale = self._train_window_model(
            X_tr.loc[y_tr.index],
            y_tr,
            train.loc[y_valid, "label_date"],
            window_id=(
                f"train={train_start}:{train_end};predict={predict_start}:{predict_end}"
            ),
        )

        # Predict — F01 backward-shift: the configured [predict_start,
        # predict_end] is the EXECUTION window.  Each execution day d uses
        # features from the previous trading day prev_td(d) (data_date), so the
        # output stays inside the window and no feature bar at/after trade_date
        # is used (no same-day-close lookahead).
        pred = prediction_frame[
            prediction_frame["trade_date"].isin(feature_dates)
        ].copy()
        if pred.empty:
            raise ValueError(f"No feature data for execution window [{predict_start}, {predict_end}]")

        pred["pred"] = self._predict_window_model(
            model, center, scale, pred[clean_features].fillna(0.0).astype(np.float32)
        ).values
        # A Booster retains native Dataset handles beyond ordinary DataFrame
        # lifetimes.  Release them as soon as prediction is complete so long
        # rolling runs do not grow until the kernel OOM killer intervenes.
        self._release_window_model(model)
        del model, center, scale, X_tr, y_tr, train, label_df
        gc.collect()

        # feature date f -> execution day d (prev_td is a bijection on calendar)
        f_to_d = {prev_td.get(d, d): d for d in window_cal}
        rows: list[dict] = []
        for f in feature_dates:
            td = f_to_d.get(f)
            sub = pred[pred["trade_date"] == f]
            if td is None or sub.empty:
                continue
            assert str(f) < td, f"F01 lookahead: feature date {f} >= trade_date {td}"
            z = _cs_zscore(sub["pred"])
            for i, (_, r) in enumerate(sub.iterrows()):
                rows.append({
                    "trade_date": td,
                    "data_date": str(f),
                    "instrument": str(r["instrument"]),
                    "signal_id": signal_id,
                    "signal_run_id": signal_run_id,
                    "score_model_raw": float(r["pred"]),
                    "score": float(z.iloc[i]) if pd.notna(z.iloc[i]) else 0.0,
                })

        result = pd.DataFrame(rows)
        log.info("Generated {} rows across {} trade dates", len(result), result["trade_date"].nunique())
        del pred, frame, train_frame, prediction_frame, rows
        gc.collect()
        return result
