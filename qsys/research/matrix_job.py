"""Matrix job builder — job data structures, config, expansion, factory.

Extracted from ``rolling_runner.py``.  See that module for
``RollingResearchRunner`` and the research pipeline orchestrator.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from qsys.research.generators.base import RollingSignalGenerator


# ── Config dataclasses ─────────────────────────────────────────────────


@dataclass
class LabelConfig:
    label_id: str
    min_coverage: float | None = None


@dataclass
class BacktestConfig:
    strategy_template_id: str = "rank_weight_top20"
    allocation_method: str = "rank_weight"
    top_n: int = 20
    max_weight: float | None = None
    initial_capital: float = 1_000_000.0
    rebalance_freq: str = "weekly"
    artifact_mode: str = "summary"


@dataclass
class SignalTransformConfig:
    """Configuration for a single signal transform."""
    transform_id: str
    type: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class MatrixJob:
    """One cell in the matrix: a (generator, transform) pair with strategy configs.

    Each job produces one SignalRun (shared across all strategies).

    Parameters
    ----------
    head_signal_id:
        When set, filter the generator's output to rows whose ``signal_id``
        matches this value before saving.  Used by multi-head generators
        (e.g. DNN task towers) that return a single DataFrame containing
        multiple signal_ids.
    """
    generator_id: str
    transform_id: str
    strategy_configs: list[dict[str, Any]]
    signal_id: str
    signal_run_id: str
    head_signal_id: str | None = None


@dataclass
class RollingResearchConfig:
    """Full configuration for a rolling research run.

    v1 single-signal mode: set ``signal`` (and optionally ``backtests``).
    v2 matrix mode: set ``generators``, ``signal_transforms``, and ``strategies``.
    """

    experiment_id: str
    title: str | None = None
    description: str | None = None
    # Immutable experiment intent: partitions, arms, costs, selection gates,
    # and holdout policy. Included in every research-config identity.
    research_protocol: dict[str, Any] = field(default_factory=dict)

    calendar: dict[str, Any] = field(default_factory=dict)
    # calendar keys: start_date, end_date, train_window_days, step_days

    signal: dict[str, Any] = field(default_factory=dict)
    # signal keys: signal_id, signal_run_id, score_column

    labels: list[dict[str, Any]] = field(default_factory=list)
    # each label: label_id

    backtests: list[dict[str, Any]] = field(default_factory=list)
    # each backtest: strategy_template_id, top_n, max_weight, ...  (v1 only)

    # ── v2 matrix fields ───────────────────────────────────────────────
    generators: list[dict[str, Any]] = field(default_factory=list)
    # each generator: generator_id, type, params

    transforms: list[dict[str, Any]] = field(default_factory=list)
    # each transform: transform_id, type  (mapped from signal_transforms in YAML)

    strategies: list[dict[str, Any]] = field(default_factory=list)
    # each strategy: strategy_id, strategy_template_id, top_n, ...

    # ── Feature list reference ─────────────────────────────────────────
    feature_list_id: str | None = None

    # ── Feature cache options (opt-in) ──────────────────────────────────
    use_feature_cache: bool = False
    materialize_on_miss: bool = False
    write_through: bool = False
    feature_cache_root: str = "data/feature_cache"
    source_manifest_hash: str = ""

    # Transactional raw-prediction checkpoints for long rolling runs.
    window_checkpoints: bool = False

    # ── v2 signal combinations ──────────────────────────────────────────
    signal_combinations: list[dict[str, Any]] = field(default_factory=list)
    # each combination: combine_id, type, inputs

    @classmethod
    def from_file(
        cls,
        path: Path,
        *,
        allow_locked_holdout_for_inspection: bool = False,
    ) -> RollingResearchConfig:
        """Load config from YAML or JSON file."""
        text = path.read_text(encoding="utf-8")
        suffix = path.suffix.lower()
        if suffix in (".yaml", ".yml"):
            import yaml
            payload = yaml.safe_load(text)
        elif suffix == ".json":
            payload = json.loads(text)
        else:
            raise ValueError(f"Unsupported config format: {path}")
        config = cls.from_dict(payload)
        if not allow_locked_holdout_for_inspection:
            validate_terminal_holdout_authorization(config)
        return config

    @classmethod
    def from_dict(cls, payload: dict) -> RollingResearchConfig:
        cal = payload.get("calendar", {})
        sig = payload.get("signal", {})
        labels = payload.get("labels", [])
        backtests = payload.get("backtests", [])
        generators = payload.get("generators", [])
        transforms = payload.get("signal_transforms", [])
        strategies = payload.get("strategies", [])
        signal_combinations = payload.get("signal_combinations", [])
        feature_list_id = payload.get("feature_list_id")
        return cls(
            experiment_id=payload.get("experiment_id", "rolling_run"),
            feature_list_id=feature_list_id,
            title=payload.get("title"),
            description=payload.get("description"),
            research_protocol=payload.get("research_protocol", {}),
            calendar=cal,
            signal=sig,
            labels=labels,
            backtests=backtests,
            generators=generators,
            transforms=transforms,
            strategies=strategies,
            signal_combinations=signal_combinations,
            use_feature_cache=payload.get("use_feature_cache", False),
            materialize_on_miss=payload.get("materialize_on_miss", False),
            write_through=payload.get("write_through", False),
            feature_cache_root=payload.get("feature_cache_root", "data/feature_cache"),
            source_manifest_hash=payload.get("source_manifest_hash", ""),
            window_checkpoints=payload.get("window_checkpoints", False),
        )


def validate_terminal_holdout_authorization(config: RollingResearchConfig) -> None:
    """Reject execution-bound config loads that overlap a locked holdout."""
    holdout = config.research_protocol.get("holdout")
    if not isinstance(holdout, dict) or not holdout.get("start_date"):
        return
    if str(config.calendar.get("end_date", "")) >= str(holdout["start_date"]) and (
        holdout.get("status") != "authorized_terminal_run"
        or not str(holdout.get("authorization_ref", "")).strip()
    ):
        raise ValueError(
            "research calendar overlaps a locked holdout; set "
            "status=authorized_terminal_run with an explicit authorization_ref"
        )


# ── Expansion helpers ──────────────────────────────────────────────────


def _slugify_id(raw: str) -> str:
    """Sanitize an identifier for use in file paths and run IDs."""
    import re
    return re.sub(r"[^a-zA-Z0-9_-]", "_", raw)


def _resolve_project_root(gen_config: dict) -> Path | None:
    """Resolve project root from generator config.

    Checks the explicit ``project_root`` param first, then falls back
    to auto-detection.  Returns ``None`` if neither provides a path.
    """
    from pathlib import Path

    params = gen_config.get("params", {})
    explicit = params.get("project_root")
    if explicit:
        return Path(str(explicit))
    # Auto-detect from file location (legacy convention)
    try:
        import inspect
        caller_file = inspect.stack()[1].filename
        return Path(caller_file).resolve().parents[2]
    except Exception:
        return None


def expand_multi_label_generators(generators: list[dict]) -> list[dict]:
    """Expand ``multi_label_lightgbm`` entries into per-label ``single_label_lightgbm``."""
    expanded: list[dict] = []
    for gen_cfg in generators:
        if gen_cfg.get("type") != "multi_label_lightgbm":
            expanded.append(gen_cfg)
            continue
        params = gen_cfg.get("params", {})
        labels = params.get("labels", [])
        if not labels:
            raise ValueError(
                f"multi_label_lightgbm generator '{gen_cfg.get('generator_id')}' "
                f"requires a 'labels' list in params"
            )
        base_id = gen_cfg["generator_id"]
        for entry in labels:
            label_id = entry["label_id"]
            label_signal_id = entry.get("signal_id", label_id)
            expanded_params = {
                "label_id": label_id,
                "universe": params.get("universe", "csi300"),
                "n_estimators": params.get("n_estimators", 200),
                "lgb_params": params.get("lgb_params"),
            }
            # Forward every other multi-label param (pit_membership,
            # feature_list_id, and future keys) instead of silently dropping
            # them — a dropped param would silently change experiment semantics.
            for key, value in params.items():
                if key not in ("labels", "label_id") and key not in expanded_params:
                    expanded_params[key] = value
            expanded.append({
                "generator_id": _slugify_id(f"{base_id}__{label_id}"),
                "type": "single_label_lightgbm",
                "params": expanded_params,
                "label_signal_id": label_signal_id,
            })
    return expanded


# ── Generator factory ──────────────────────────────────────────────────


def _create_generator_from_config(
    gen_config: dict,
    feature_list_id: str | None = None,
    *,
    use_feature_cache: bool = False,
    materialize_on_miss: bool = False,
    write_through: bool = False,
    feature_cache_root: str = "data/feature_cache",
    source_manifest_hash: str = "",
) -> RollingSignalGenerator:
    """Create a generator instance from a config dict.

    Supported types:
    - ``fixture`` — deterministic fixture (tests/CI)
    - ``alpha_v1_existing`` — existing alpha_v1 prediction adapter
    - ``technical_composite`` — OHLCV-derived composite signal
    """
    from qsys.research.generators.fixture import FixtureSignalGenerator

    gen_type = gen_config.get("type", "fixture")
    params = gen_config.get("params", {})
    if gen_type == "fixture":
        return FixtureSignalGenerator(
            n_instruments=params.get("n_instruments", 100),
            seed=params.get("seed", 42),
        )
    if gen_type == "alpha_v1_existing":
        from qsys.research.generators.alpha_v1_existing import AlphaV1ExistingGenerator
        return AlphaV1ExistingGenerator()
    if gen_type == "technical_composite":
        from qsys.research.generators.technical_composite import TechnicalCompositeV1Generator
        return TechnicalCompositeV1Generator(
            momentum_short=params.get("momentum_short", 20),
            momentum_long=params.get("momentum_long", 60),
            reversal_days=params.get("reversal_days", 5),
            volatility_days=params.get("volatility_days", 20),
            volume_short=params.get("volume_short", 5),
            volume_long=params.get("volume_long", 20),
        )
    if gen_type == "dnn_multitask":
        from qsys.research.generators.dnn_multitask import DnnMultitaskGenerator
        return DnnMultitaskGenerator(
            project_root=_resolve_project_root(gen_config),
            dnn_kwargs=params.get("dnn_kwargs"),
            universe=params.get("universe", "csi300"),
            label_ids=tuple(params.get("label_ids", ("fwd_ret_5d_xsz_clip3", "fwd_ret_20d_xsz_clip3"))),
        )
    if gen_type == "lightgbm_alpha_v1":
        from qsys.research.generators.lightgbm_alpha_v1 import LightGBMAlphaV1Generator
        return LightGBMAlphaV1Generator(
            universe=params.get("universe", "csi300"),
            n_estimators=params.get("n_estimators", 200),
            lgb_params=params.get("lgb_params"),
            label_ids=tuple(params.get("label_ids", ("fwd_ret_5d_xsz_clip3", "fwd_ret_20d_xsz_clip3"))),
        )
    if gen_type in {
        "single_label_lightgbm",
        "single_label_lightgbm_temporal",
        "single_label_ridge",
    }:
        _CONSUMED_PARAMS = {
            "label_id", "universe",
            "sample_weight_policy",
            "feature_list_id", "feature_cache_list_id",
            "signal_exposure_features",
            "margin_lag_sessions",
            "pit_membership", "pit_filter_mode",
            "pit_universe_artifact", "liquidity_exclusion_path",
            "prediction_membership_path", "prediction_membership_sha256",
            "prediction_universe",
            "shareholder_holder_path", "shareholder_holder_sha256",
            "shareholder_top10_path", "shareholder_top10_sha256",
            "shareholder_manifest_path", "shareholder_manifest_sha256",
            "shareholder_freshness_contract",
            "income_sidecar_artifact_id", "income_sidecar_path",
            "income_sidecar_sha256",
            "income_sidecar_manifest_path", "income_sidecar_manifest_sha256",
            "income_source_mode", "income_sidecar_required_history_start",
        }
        if gen_type == "single_label_ridge":
            _CONSUMED_PARAMS.add("ridge_alpha")
        else:
            _CONSUMED_PARAMS.update({"n_estimators", "lgb_params"})
        unknown = set(params) - _CONSUMED_PARAMS
        if unknown:
            raise ValueError(
                f"{gen_type} params contains unknown keys that would "
                f"be silently dropped: {sorted(unknown)}.  Known keys: "
                f"{sorted(_CONSUMED_PARAMS)}."
            )
        from qsys.research.generators.lightgbm_single_label import (
            LightGBMSingleLabelGenerator,
            _prediction_membership_identity,
        )
        if gen_type == "single_label_ridge":
            from qsys.research.generators.ridge_single_label import (
                RidgeSingleLabelGenerator,
            )

            generator_class = RidgeSingleLabelGenerator
        elif gen_type == "single_label_lightgbm_temporal":
            from qsys.research.generators.temporal_validation import (
                TemporalValidationLightGBMSingleLabelGenerator,
            )

            generator_class = TemporalValidationLightGBMSingleLabelGenerator
        else:
            generator_class = LightGBMSingleLabelGenerator
        prediction_membership_path = params.get("prediction_membership_path", "")
        if params.get("prediction_membership_sha256") and not prediction_membership_path:
            raise ValueError(
                "prediction_membership_sha256 requires prediction_membership_path"
            )
        if prediction_membership_path:
            normalized_path, digest, _ = _prediction_membership_identity(
                prediction_membership_path
            )
            declared_digest = params.get("prediction_membership_sha256", "")
            if declared_digest and declared_digest != digest:
                raise ValueError(
                    "prediction_membership_sha256 does not match the snapshot content"
                )
            if not (
                params.get("pit_membership", False)
                or params.get("pit_filter_mode", "")
            ):
                raise ValueError(
                    "prediction_membership_path requires an enabled PIT filter "
                    "(pit_membership=true or pit_filter_mode)"
                )
            # The pipeline builds checkpoint identity from gen_config.  Bind
            # the normalized path and actual content hash there as well as in
            # the generator cache identity.
            gen_config["params"] = {
                **params,
                "prediction_membership_path": normalized_path,
                "prediction_membership_sha256": digest,
            }
        generator_kwargs = dict(
            label_id=params["label_id"],
            universe=params.get("universe", "csi300"),
            feature_list_id=feature_list_id or params.get("feature_list_id"),
            feature_cache_list_id=params.get("feature_cache_list_id"),
            signal_exposure_features=params.get("signal_exposure_features", ()),
            margin_lag_sessions=params.get("margin_lag_sessions", 0),
            sample_weight_policy=params.get("sample_weight_policy"),
            pit_membership=params.get("pit_membership", False),
            pit_filter_mode=params.get("pit_filter_mode", ""),
            pit_universe_artifact=params.get("pit_universe_artifact", "csi800_pit_v2"),
            liquidity_exclusion_path=params.get("liquidity_exclusion_path", ""),
            prediction_membership_path=prediction_membership_path,
            prediction_universe=params.get("prediction_universe", ""),
            shareholder_holder_path=params.get("shareholder_holder_path", ""),
            shareholder_holder_sha256=params.get("shareholder_holder_sha256", ""),
            shareholder_top10_path=params.get("shareholder_top10_path", ""),
            shareholder_top10_sha256=params.get("shareholder_top10_sha256", ""),
            shareholder_manifest_path=params.get("shareholder_manifest_path", ""),
            shareholder_manifest_sha256=params.get(
                "shareholder_manifest_sha256", ""
            ),
            income_sidecar_artifact_id=params.get(
                "income_sidecar_artifact_id", ""
            ),
            income_sidecar_path=params.get("income_sidecar_path", ""),
            income_sidecar_sha256=params.get("income_sidecar_sha256", ""),
            income_sidecar_manifest_path=params.get(
                "income_sidecar_manifest_path", ""
            ),
            income_sidecar_manifest_sha256=params.get(
                "income_sidecar_manifest_sha256", ""
            ),
            income_source_mode=params.get(
                "income_source_mode", "legacy_unverified_global_v0"
            ),
            income_sidecar_required_history_start=params.get(
                "income_sidecar_required_history_start", ""
            ),
            shareholder_freshness_contract=params.get(
                "shareholder_freshness_contract"
            ),
            use_feature_cache=use_feature_cache,
            materialize_on_miss=materialize_on_miss,
            write_through=write_through,
            feature_cache_root=feature_cache_root,
            source_manifest_hash=source_manifest_hash,
        )
        if gen_type == "single_label_ridge":
            generator_kwargs["ridge_alpha"] = params.get("ridge_alpha", 1.0)
        else:
            generator_kwargs.update({
                "n_estimators": params.get("n_estimators", 200),
                "lgb_params": params.get("lgb_params"),
            })
        return generator_class(**generator_kwargs)
    if gen_type in ("single_label_lightgbm_binary",):
        from qsys.research.generators.lightgbm_binary import LightGBMBinaryGenerator
        return LightGBMBinaryGenerator(
            label_id=params["label_id"],
            universe=params.get("universe", "csi300"),
            n_estimators=params.get("n_estimators", 300),
            feature_list_id=feature_list_id or params.get("feature_list_id"),
            lgb_params=params.get("lgb_params"),
        )
    raise ValueError(f"Unknown generator type: {gen_type!r}")


# ── Signal transforms ─────────────────────────────────────────────────


def apply_signal_transform(
    frame: pd.DataFrame,
    transform_config: SignalTransformConfig | dict[str, Any],
) -> pd.DataFrame:
    """Apply a signal transform to a predictions DataFrame."""
    if isinstance(transform_config, dict):
        transform_config = SignalTransformConfig(**transform_config)

    result = frame.copy()
    result["score_raw"] = frame["score"]
    result["transform_id"] = transform_config.transform_id

    if transform_config.type == "identity":
        result["score"] = frame["score"].copy()
    elif transform_config.type == "daily_zscore":
        def _safe_zscore(scores: pd.Series) -> pd.Series:
            std = scores.std(ddof=0)
            if pd.isna(std) or std == 0.0:
                return pd.Series(0.0, index=scores.index)
            return (scores - scores.mean()) / std

        result["score"] = result.groupby("trade_date", group_keys=False)["score"].transform(
            _safe_zscore
        )
    elif transform_config.type == "daily_linear_residual":
        exposure_columns = list(
            transform_config.params.get("exposure_columns", [])
        )
        min_observations = int(
            transform_config.params.get("min_observations", 100)
        )
        if not exposure_columns:
            raise ValueError(
                "daily_linear_residual requires params.exposure_columns"
            )
        if min_observations < len(exposure_columns) + 2:
            raise ValueError(
                "daily_linear_residual min_observations is too small"
            )
        missing = sorted(set(exposure_columns) - set(result.columns))
        if missing:
            raise ValueError(
                "daily_linear_residual missing exposure columns: "
                f"{missing}"
            )

        diagnostics: list[dict[str, Any]] = []
        residuals = pd.Series(index=result.index, dtype=float)
        for trade_date, day in result.groupby("trade_date", sort=True):
            score = pd.to_numeric(day["score"], errors="coerce")
            exposures = day[exposure_columns].apply(
                pd.to_numeric, errors="coerce"
            ).replace([np.inf, -np.inf], np.nan)
            valid = score.notna()
            if int(valid.sum()) < min_observations:
                raise ValueError(
                    "daily_linear_residual insufficient score rows on "
                    f"{trade_date}: {int(valid.sum())} < {min_observations}"
                )
            clean = exposures.loc[valid]
            medians = clean.median()
            all_missing = medians[medians.isna()].index.tolist()
            if all_missing:
                raise ValueError(
                    "daily_linear_residual all-missing exposure columns on "
                    f"{trade_date}: {all_missing}"
                )
            missing_values = int(clean.isna().sum().sum())
            clean = clean.fillna(medians)
            scale = clean.std(ddof=0).replace(0.0, 1.0)
            design = ((clean - clean.mean()) / scale).fillna(0.0)
            matrix = np.column_stack(
                [np.ones(len(design)), design.to_numpy(dtype=float)]
            )
            target = score.loc[valid].to_numpy(dtype=float)
            coefficients, *_ = np.linalg.lstsq(matrix, target, rcond=None)
            fitted = matrix @ coefficients
            day_residual = target - fitted
            residuals.loc[clean.index] = day_residual
            total_ss = float(((target - target.mean()) ** 2).sum())
            residual_ss = float((day_residual ** 2).sum())
            diagnostics.append({
                "trade_date": str(trade_date),
                "observations": int(valid.sum()),
                "imputed_exposure_values": missing_values,
                "r_squared": (
                    1.0 - residual_ss / total_ss if total_ss > 0 else 0.0
                ),
            })
        if residuals.isna().any():
            raise ValueError(
                "daily_linear_residual produced missing scores; exposure "
                "coverage must be complete for every signal row"
            )
        result["score"] = residuals
        result.attrs["transform_diagnostics"] = {
            "type": transform_config.type,
            "exposure_columns": exposure_columns,
            "min_observations": min_observations,
            "dates": len(diagnostics),
            "mean_r_squared": float(
                pd.Series([row["r_squared"] for row in diagnostics]).mean()
            ),
            "max_r_squared": float(
                pd.Series([row["r_squared"] for row in diagnostics]).max()
            ),
        }
    else:
        raise ValueError(f"Unknown signal transform type: {transform_config.type!r}")

    return result


# ── Matrix job builder ────────────────────────────────────────────────


def build_matrix_jobs(
    config: RollingResearchConfig,
    effective_generators: list[dict] | None = None,
) -> list[MatrixJob]:
    """Expand a matrix config into individual (generator, transform) jobs.

    Each job carries the full list of strategy configs so that generation
    and transform are performed once and backtests are run per strategy.

    Parameters
    ----------
    config:
        Full research config.
    effective_generators:
        Pre-expanded generator list (e.g. after multi-label expansion).
        When ``None``, uses ``config.generators``.
    """
    generators = effective_generators if effective_generators is not None else config.generators
    base_signal_id = config.signal.get("signal_id", "matrix_signal")
    experiment_id = config.experiment_id
    cal = config.calendar
    start = cal.get("start_date", "")
    end = cal.get("end_date", "")

    jobs: list[MatrixJob] = []
    for gen_cfg in generators:
        gen_id = gen_cfg["generator_id"]
        # Multi-label expanded entries carry an explicit per-label signal_id
        label_signal_id = gen_cfg.get("label_signal_id", None)
        # Multi-head generators carry per-head config
        heads = gen_cfg.get("params", {}).get("heads", None)
        for tf_cfg in config.transforms:
            tf_id = tf_cfg["transform_id"]
            if label_signal_id:
                signal_id = f"{label_signal_id}__{tf_id}"
            else:
                signal_id = f"{base_signal_id}__{gen_id}__{tf_id}"
            signal_run_id = (
                f"rolling__{experiment_id}__{gen_id}__{tf_id}__{start}_{end}"
            )
            if heads:
                for head in heads:
                    head_signal_id = head.get("signal_id", "").strip()
                    if not head_signal_id:
                        raise ValueError(
                            f"multi-head generator '{gen_id}' has a head entry "
                            f"with empty or missing signal_id"
                        )
                    head_slug = _slugify_id(head_signal_id)
                    head_job_signal_id = f"{head_signal_id}__{tf_id}"
                    jobs.append(MatrixJob(
                        generator_id=gen_id,
                        transform_id=tf_id,
                        strategy_configs=config.strategies,
                        signal_id=head_job_signal_id,
                        signal_run_id=f"{signal_run_id}__{head_slug}",
                        head_signal_id=head_signal_id,
                    ))
            else:
                jobs.append(MatrixJob(
                    generator_id=gen_id,
                    transform_id=tf_id,
                    strategy_configs=config.strategies,
                    signal_id=signal_id,
                    signal_run_id=signal_run_id,
                ))
    return jobs
