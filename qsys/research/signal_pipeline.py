"""SignalResearchPipeline — signal generation + IC evaluation only.

Enforces the boundary: signal research produces SignalRun + IC evidence.
Strategy backtest is a separate concern (see ``BacktestRunner``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import gc
import hashlib
import inspect
import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.feature.registry import FeatureListRegistry
from qsys.research.generators.base import RollingSignalGenerator
from qsys.research.generators.fixture import FixtureSignalGenerator
from qsys.research.manifest import write_manifest, with_standard_metadata
from qsys.research.matrix_job import (
    RollingResearchConfig,
    _create_generator_from_config,
    apply_signal_transform,
    build_matrix_jobs,
    expand_multi_label_generators,
)
from qsys.research.paths import ResearchPaths
from qsys.research.rolling_window import RollingWindow, build_rolling_windows
from qsys.research.window_checkpoint import (
    WindowCheckpointRef,
    WindowPredictionCheckpointStore,
)
from qsys.signal.store import SignalStore


log = logging.getLogger(__name__)


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"


def _research_config_sha256(config: RollingResearchConfig) -> str:
    payload = json.dumps(
        asdict(config), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _generator_dependency_code_identity(
    generator: RollingSignalGenerator,
) -> list[dict[str, str]]:
    """Hash explicitly declared generator code dependencies.

    Generators that do not declare dependencies retain the historical identity
    shape.  A dependency declaration is a name-to-path mapping; only the
    stable name and content hash are persisted, so checkout location does not
    become part of the checkpoint identity.
    """
    declared = getattr(generator, "checkpoint_code_dependencies", None)
    if declared is None:
        return []
    if callable(declared):
        declared = declared()
    if not isinstance(declared, dict):
        raise ValueError(
            "checkpoint_code_dependencies must be a mapping of name to file path"
        )
    dependencies: list[dict[str, str]] = []
    for name, raw_path in sorted(declared.items(), key=lambda item: str(item[0])):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("checkpoint code dependency names must be non-empty strings")
        if not isinstance(raw_path, (str, Path)):
            raise ValueError(
                f"checkpoint code dependency {name!r} must resolve to a filesystem path"
            )
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise ValueError(
                f"checkpoint code dependency {name!r} is not a readable file: {path}"
            )
        dependencies.append({"name": name, "sha256": _sha256_path(path)})
    return dependencies


@dataclass
class SignalRunRef:
    """Reference to one produced SignalRun."""
    generator_id: str
    transform_id: str
    signal_id: str
    signal_run_id: str
    head_signal_id: str | None = None


@dataclass
class SignalEvalRef:
    """Reference to one evaluation result."""
    signal_id: str
    signal_run_id: str
    label_id: str
    eval_id: str | None = None


@dataclass
class SignalResearchResult:
    """Result of a SignalResearchPipeline run."""
    experiment_id: str
    signal_runs: list[SignalRunRef]
    eval_refs: list[SignalEvalRef]
    manifest_path: Path


class CheckpointBatchComplete(RuntimeError):
    """A bounded checkpoint batch committed successfully; restart to resume."""

    def __init__(
        self, *, generator_id: str, completed_windows: int, total_windows: int
    ) -> None:
        self.generator_id = generator_id
        self.completed_windows = completed_windows
        self.total_windows = total_windows
        super().__init__(
            f"Checkpoint batch complete for {generator_id}: "
            f"{completed_windows}/{total_windows} windows"
        )


class SignalResearchPipeline:
    """Signal research pipeline — generation + IC evaluation only.

    Does **not** run backtests, strategy allocation, or candidate promotion.
    Use :class:`~qsys.research.rolling_runner.RollingResearchRunner` (deprecated)
    if you need the combined signal→backtest→experiment-index workflow.
    """

    def __init__(self, root: str | Path = "data/research") -> None:
        self.root = Path(root).resolve()
        self._paths = ResearchPaths(str(self.root))
        self._signal_store = SignalStore(str(self.root))

    def run(
        self,
        config: RollingResearchConfig | dict[str, Any] | str | Path,
        *,
        signal_generator: RollingSignalGenerator | None = None,
        overwrite_signal: bool = False,
        overwrite_eval: bool = False,
        use_feature_cache: bool | None = None,
        materialize_on_miss: bool | None = None,
        feature_cache_root: str | None = None,
        source_manifest_hash: str | None = None,
        write_through: bool | None = None,
        checkpoint_batch_size: int | None = None,
    ) -> SignalResearchResult:
        """Execute signal research pipeline.

        Parameters
        ----------
        config:
            Configuration (``RollingResearchConfig``, ``dict``, or path).
        signal_generator:
            Optional override generator (used when config has no generators).
        overwrite_signal:
            Allow overwriting existing SignalRun.
        overwrite_eval:
            Allow overwriting existing evaluations.
        use_feature_cache / materialize_on_miss / feature_cache_root / source_manifest_hash:
            Feature cache options.  CLI overrides YAML when set (not None).
        checkpoint_batch_size:
            Maximum number of new window checkpoints to commit in this
            process.  When more windows remain, raise
            :class:`CheckpointBatchComplete` so a supervisor can restart with
            a fresh address space.  The runtime limit is deliberately absent
            from checkpoint identity.
        """
        if isinstance(config, (str, Path)):
            config = RollingResearchConfig.from_file(Path(config))
        elif isinstance(config, dict):
            config = RollingResearchConfig.from_dict(config)

        # CLI overrides YAML config for cache params (only when explicitly set)
        if use_feature_cache is not None:
            config.use_feature_cache = use_feature_cache
        if materialize_on_miss is not None:
            config.materialize_on_miss = materialize_on_miss
        if feature_cache_root is not None:
            config.feature_cache_root = feature_cache_root
        if source_manifest_hash is not None:
            config.source_manifest_hash = source_manifest_hash
        if write_through is not None:
            config.write_through = write_through

        self._validate_config(config)
        if checkpoint_batch_size is not None and checkpoint_batch_size <= 0:
            raise ValueError("checkpoint_batch_size must be a positive integer")
        if checkpoint_batch_size is not None and not config.window_checkpoints:
            raise ValueError(
                "checkpoint_batch_size requires window_checkpoints=true"
            )

        exp_dir = self._paths.experiment_dir(config.experiment_id)

        # ── 1. Build rolling windows ──
        # F01/F16: use the MAX declared lag across all labels.  A config may
        # list several horizons (e.g. 60/120/180d); reading only labels[0]
        # would under-shift train_end for the longest-horizon label and let
        # its training labels leak into the predict window.
        lag = 0
        if config.labels:
            lag = max(
                (l.get("label_maturity_lag_trading_days") or 0)
                for l in config.labels
            )
        windows = build_rolling_windows(
            config.calendar.get("start_date", ""),
            config.calendar.get("end_date", ""),
            train_window_days=config.calendar.get("train_window_days", 252),
            step_days=config.calendar.get("step_days", 5),
            label_maturity_lag_trading_days=lag,
        )
        window_df = pd.DataFrame([{
            "window_id": w.window_id,
            "train_start": w.train_start,
            "train_end": w.train_end,
            "predict_start": w.predict_start,
            "predict_end": w.predict_end,
        } for w in windows])
        exp_dir.mkdir(parents=True, exist_ok=True)
        window_df.to_csv(exp_dir / "rolling_windows.csv", index=False)

        # ── Dispatch: matrix vs single ──
        if config.generators:
            return self._run_matrix(
                config, windows,
                signal_generator=signal_generator,
                overwrite_signal=overwrite_signal,
                overwrite_eval=overwrite_eval,
                checkpoint_batch_size=checkpoint_batch_size,
            )

        return self._run_single(
            config, windows,
            signal_generator=signal_generator,
            overwrite_signal=overwrite_signal,
            overwrite_eval=overwrite_eval,
        )

    # ------------------------------------------------------------------
    # Config validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_config(config: RollingResearchConfig) -> None:
        """Reject configs that mix strategy/backtest concerns."""
        if config.window_checkpoints and not config.source_manifest_hash.strip():
            raise ValueError(
                "window_checkpoints requires a non-empty source_manifest_hash; "
                "resuming against an unidentified feature snapshot is forbidden"
            )
        if config.strategies:
            raise ValueError(
                f"SignalResearchPipeline does not accept strategies. "
                f"Got {len(config.strategies)} strategy config(s). "
                f"Use BacktestRunner.run_from_signal_cache() separately."
            )
        if config.backtests:
            raise ValueError(
                f"SignalResearchPipeline does not accept backtests. "
                f"Got {len(config.backtests)} backtest config(s). "
                f"Use BacktestRunner.run_from_signal_cache() separately."
            )

    def _window_checkpoint_base_identity(
        self,
        config: RollingResearchConfig,
        generator_config: dict[str, Any],
        generator: RollingSignalGenerator,
    ) -> dict[str, Any]:
        label_artifacts: list[dict[str, Any]] = []
        for label in config.labels:
            label_id = str(label["label_id"])
            manifest_path = self._paths.label_manifest(label_id)
            data_path = self._paths.label_file(label_id)
            label_artifacts.append({
                "label_id": label_id,
                "manifest_sha256": _sha256_path(manifest_path),
                "labels_sha256": _sha256_path(data_path),
            })

        generator_source = inspect.getsourcefile(generator.__class__)
        generator_source_path = Path(generator_source) if generator_source else Path("")
        identity = {
            "experiment_id": config.experiment_id,
            "research_config": asdict(config),
            "generator_config": generator_config,
            "generator_class": (
                f"{generator.__class__.__module__}.{generator.__class__.__qualname__}"
            ),
            "generator_code_sha256": _sha256_path(generator_source_path),
            "pipeline_code_sha256": _sha256_path(Path(__file__)),
            "source_manifest_hash": config.source_manifest_hash,
            "label_artifacts": label_artifacts,
        }
        dependency_code = _generator_dependency_code_identity(generator)
        if dependency_code:
            identity["generator_dependency_code"] = dependency_code
        input_artifacts = getattr(generator, "checkpoint_input_artifacts", None)
        if input_artifacts:
            identity["generator_input_artifacts"] = input_artifacts
        contract_identity = getattr(generator, "checkpoint_contract_identity", None)
        if contract_identity:
            identity["generator_contracts"] = contract_identity
        if config.feature_list_id:
            contract = FeatureListRegistry.contract(config.feature_list_id)
            identity["feature_list_contract"] = {
                key: value for key, value in contract.items() if key != "features"
            }
        return identity

    # ------------------------------------------------------------------
    # Single-signal path (v1 compatibility)
    # ------------------------------------------------------------------

    def _run_single(
        self,
        config: RollingResearchConfig,
        windows: list[RollingWindow],
        *,
        signal_generator: RollingSignalGenerator | None = None,
        overwrite_signal: bool = False,
        overwrite_eval: bool = False,
    ) -> SignalResearchResult:
        """Single-signal path (v1 compatibility, no backtest)."""
        from qsys.research.evaluation import SignalEvaluator

        signal_id = config.signal.get("signal_id", "rolling_signal")
        signal_run_id = config.signal.get("signal_run_id", "rolling_run")
        gen = signal_generator or FixtureSignalGenerator()

        all_preds: list[pd.DataFrame] = []
        for w in windows:
            pred = gen.generate(
                train_start=w.train_start,
                train_end=w.train_end,
                predict_start=w.predict_start,
                predict_end=w.predict_end,
                signal_id=signal_id,
                signal_run_id=signal_run_id,
            )
            all_preds.append(pred)

        predictions = pd.concat(all_preds, ignore_index=True)
        signal_manifest = {
            "model_mode": "rolling_train",
            "window_count": len(windows),
            "train_window_days": config.calendar.get("train_window_days"),
        }
        feature_visibility_contract = getattr(
            gen, "feature_visibility_contract", None
        )
        if feature_visibility_contract:
            signal_manifest["feature_visibility_contract"] = (
                feature_visibility_contract
            )
        shareholder_freshness = getattr(
            gen, "shareholder_freshness_lineage", None
        )
        if shareholder_freshness is not None:
            signal_manifest["shareholder_freshness_lineage"] = (
                shareholder_freshness
            )
        if config.source_manifest_hash:
            signal_manifest["source_manifest_hash"] = (
                config.source_manifest_hash
            )
        self._signal_store.save_signal_run(
            signal_id, signal_run_id, predictions,
            manifest=signal_manifest,
            overwrite=overwrite_signal,
        )

        signal_refs = [SignalRunRef(
            generator_id="single",
            transform_id="raw",
            signal_id=signal_id,
            signal_run_id=signal_run_id,
        )]

        # ── Evaluate ──
        evaluator = SignalEvaluator(str(self.root))
        eval_refs: list[SignalEvalRef] = []
        for lcfg in config.labels:
            result = evaluator.evaluate(
                signal_id=signal_id,
                signal_run_id=signal_run_id,
                label_id=lcfg["label_id"],
                score_column=config.signal.get("score_column", "score"),
                overwrite=overwrite_eval,
                require_pit_lineage=bool(lcfg.get("require_pit_lineage", False)),
                research_config_sha256=_research_config_sha256(config),
            )
            eval_refs.append(SignalEvalRef(
                signal_id=signal_id,
                signal_run_id=signal_run_id,
                label_id=lcfg["label_id"],
                eval_id=str(result.output_dir) if result.output_dir else None,
            ))

        # ── Manifest ──
        manifest = with_standard_metadata({
            "artifact_type": "signal_research",
            "mode": "single",
            "experiment_id": config.experiment_id,
            "signal_id": signal_id,
            "signal_run_id": signal_run_id,
            "window_count": len(windows),
            "date_range": {
                "start": config.calendar.get("start_date"),
                "end": config.calendar.get("end_date"),
            },
            "labels": config.labels,
            "output_signal_path": str(self._paths.signal_dir(signal_id, signal_run_id)),
        })
        manifest_path = self._paths.experiment_dir(config.experiment_id) / "signal_research_manifest.json"
        write_manifest(manifest_path, manifest)

        return SignalResearchResult(
            experiment_id=config.experiment_id,
            signal_runs=signal_refs,
            eval_refs=eval_refs,
            manifest_path=manifest_path,
        )

    # ------------------------------------------------------------------
    # Matrix experiment path
    # ------------------------------------------------------------------

    def _run_matrix(
        self,
        config: RollingResearchConfig,
        windows: list[RollingWindow],
        *,
        signal_generator: RollingSignalGenerator | None = None,
        overwrite_signal: bool = False,
        overwrite_eval: bool = False,
        checkpoint_batch_size: int | None = None,
    ) -> SignalResearchResult:
        """Matrix experiment: generators × transforms (no backtest)."""
        from qsys.research.evaluation import SignalEvaluator
        from qsys.research.signal_combine import (
            build_combine_spec_from_config,
            build_cross_signal_index,
            combine_signals,
        )

        evaluator = SignalEvaluator(str(self.root))
        exp_dir = self._paths.experiment_dir(config.experiment_id)
        feature_list_contract: dict[str, Any] | None = None
        if config.feature_list_id:
            loaded_contract = FeatureListRegistry.contract(config.feature_list_id)
            feature_list_contract = {
                key: value
                for key, value in loaded_contract.items()
                if key != "features"
            }

        # ── 1. Expand multi-label → per-label entries ──
        effective_generators = expand_multi_label_generators(config.generators)

        # ── 2. Build matrix jobs ──
        jobs = build_matrix_jobs(config, effective_generators=effective_generators)
        explicit_generator = signal_generator is not None

        # ── 3. Generate raw predictions once per generator ──
        raw_predictions: dict[str, pd.DataFrame] = {}
        generator_visibility_contracts: dict[str, str | None] = {}
        generator_feature_source_lineage: dict[str, dict[str, Any]] = {}
        generator_shareholder_freshness: dict[str, dict[str, Any] | None] = {}
        generator_model_diagnostics: dict[str, dict[str, Any] | None] = {}
        generator_checkpoint_hashes: dict[str, str] = {}
        for gen_cfg in effective_generators:
            gen_id = gen_cfg["generator_id"]
            gen = signal_generator if explicit_generator else _create_generator_from_config(
                gen_cfg, feature_list_id=config.feature_list_id,
                use_feature_cache=config.use_feature_cache,
                materialize_on_miss=config.materialize_on_miss,
                write_through=config.write_through,
                feature_cache_root=config.feature_cache_root,
                source_manifest_hash=config.source_manifest_hash,
            )
            generator_visibility_contracts[gen_id] = getattr(
                gen, "feature_visibility_contract", None
            )
            generator_feature_source_lineage[gen_id] = getattr(
                gen, "feature_source_lineage", {}
            )
            checkpoint_store: WindowPredictionCheckpointStore | None = None
            checkpoint_refs: list[WindowCheckpointRef] = []
            new_checkpoint_count = 0
            if config.window_checkpoints:
                checkpoint_store = WindowPredictionCheckpointStore(
                    self._paths.window_checkpoint_dir(config.experiment_id, gen_id),
                    self._window_checkpoint_base_identity(config, gen_cfg, gen),
                )

            all_preds: list[pd.DataFrame] = []
            for w in windows:
                checkpoint_ref = (
                    checkpoint_store.validate(w)
                    if checkpoint_store is not None
                    else None
                )
                if checkpoint_ref is not None:
                    checkpoint_refs.append(checkpoint_ref)
                    log.info("Checkpoint HIT: %s / %s", gen_id, w.window_id)
                    continue

                pred = gen.generate(
                    train_start=w.train_start,
                    train_end=w.train_end,
                    predict_start=w.predict_start,
                    predict_end=w.predict_end,
                    signal_id="__internal__",
                    signal_run_id="__internal__",
                )
                if checkpoint_store is not None:
                    checkpoint_refs.append(checkpoint_store.save(w, pred))
                    del pred
                    gc.collect()
                    new_checkpoint_count += 1
                    if (
                        checkpoint_batch_size is not None
                        and new_checkpoint_count >= checkpoint_batch_size
                        and len(checkpoint_refs) < len(windows)
                    ):
                        raise CheckpointBatchComplete(
                            generator_id=gen_id,
                            completed_windows=len(checkpoint_refs),
                            total_windows=len(windows),
                        )
                else:
                    all_preds.append(pred)

            if checkpoint_store is not None:
                if len(checkpoint_refs) != len(windows):
                    raise RuntimeError(
                        f"Incomplete checkpoint set for {gen_id}: "
                        f"{len(checkpoint_refs)}/{len(windows)}"
                    )
                # Only materialize the complete set after every window has a
                # validated commit marker.  Resume never loads prior windows
                # while expensive model training is still in progress.
                all_preds = [checkpoint_store.load(w) for w in windows]
                generator_checkpoint_hashes[gen_id] = (
                    checkpoint_store.checkpoint_set_sha256(checkpoint_refs)
                )
            raw_predictions[gen_id] = pd.concat(all_preds, ignore_index=True)
            # Read this only after every window has been generated.  The
            # lineage property snapshots the accumulated per-window profiles;
            # capturing it before generation would persist an empty profile
            # set even though the gate actually ran.
            generator_shareholder_freshness[gen_id] = getattr(
                gen, "shareholder_freshness_lineage", None
            )
            generator_model_diagnostics[gen_id] = getattr(
                gen, "model_diagnostics_lineage", None
            )
            del all_preds
            gc.collect()

        # ── 4. Per-job: transform → save SignalRun → evaluate ──
        signal_refs: list[SignalRunRef] = []
        eval_refs: list[SignalEvalRef] = []

        for job in jobs:
            raw = raw_predictions[job.generator_id]

            # Multi-head: filter to rows belonging to this head
            if job.head_signal_id:
                raw = raw[raw["signal_id"] == job.head_signal_id].copy()

            tf_cfg = next(
                (t for t in config.transforms if t["transform_id"] == job.transform_id),
                None,
            )
            if tf_cfg is None:
                raise ValueError(f"Transform config not found: {job.transform_id}")

            transformed = apply_signal_transform(raw, tf_cfg)
            transformed["signal_id"] = job.signal_id
            transformed["signal_run_id"] = job.signal_run_id

            signal_manifest = {
                "model_mode": "signal_research_matrix",
                "window_count": len(windows),
                "generator_id": job.generator_id,
                "transform_id": job.transform_id,
                "train_window_days": config.calendar.get("train_window_days"),
            }
            feature_visibility_contract = generator_visibility_contracts.get(
                job.generator_id
            )
            if feature_visibility_contract:
                signal_manifest["feature_visibility_contract"] = (
                    feature_visibility_contract
                )
            feature_source_lineage = generator_feature_source_lineage.get(
                job.generator_id
            )
            if feature_source_lineage:
                signal_manifest["feature_source_lineage"] = (
                    feature_source_lineage
                )
            shareholder_freshness = generator_shareholder_freshness.get(
                job.generator_id
            )
            if shareholder_freshness is not None:
                signal_manifest["shareholder_freshness_lineage"] = (
                    shareholder_freshness
                )
            model_diagnostics = generator_model_diagnostics.get(job.generator_id)
            if model_diagnostics is not None:
                signal_manifest["model_diagnostics"] = model_diagnostics
            if config.source_manifest_hash:
                signal_manifest["source_manifest_hash"] = (
                    config.source_manifest_hash
                )
            if feature_list_contract is not None:
                signal_manifest["feature_list_contract"] = feature_list_contract
            checkpoint_hash = generator_checkpoint_hashes.get(job.generator_id)
            if checkpoint_hash:
                signal_manifest["window_checkpoint_set_sha256"] = checkpoint_hash
            self._signal_store.save_signal_run(
                job.signal_id, job.signal_run_id, transformed,
                manifest=signal_manifest,
                overwrite=overwrite_signal,
            )

            signal_refs.append(SignalRunRef(
                generator_id=job.generator_id,
                transform_id=job.transform_id,
                signal_id=job.signal_id,
                signal_run_id=job.signal_run_id,
                head_signal_id=job.head_signal_id,
            ))

            for lcfg in config.labels:
                result = evaluator.evaluate(
                    signal_id=job.signal_id,
                    signal_run_id=job.signal_run_id,
                    label_id=lcfg["label_id"],
                    score_column=config.signal.get("score_column", "score"),
                    overwrite=overwrite_eval,
                    require_pit_lineage=bool(
                        lcfg.get("require_pit_lineage", False)
                    ),
                    research_config_sha256=_research_config_sha256(config),
                )
                eval_refs.append(SignalEvalRef(
                    signal_id=job.signal_id,
                    signal_run_id=job.signal_run_id,
                    label_id=lcfg["label_id"],
                    eval_id=str(result.output_dir) if result.output_dir else None,
                ))

        # ── 5. Signal combinations ──
        combined_signal_refs: list[SignalRunRef] = []
        combined_eval_refs: list[SignalEvalRef] = []

        if config.signal_combinations:
            signal_id_map = {f"{j.generator_id}__{j.transform_id}": j.signal_id for j in jobs}
            signal_run_id_map = {f"{j.generator_id}__{j.transform_id}": j.signal_run_id for j in jobs}

            combine_specs: list[Any] = []
            combined_output_ids: list[str] = []
            combined_output_run_ids: list[str] = []

            for comb_cfg in config.signal_combinations:
                spec = build_combine_spec_from_config(
                    comb_cfg, signal_id_map, signal_run_id_map,
                )
                combine_specs.append(spec)
                out_sig_id = (
                    config.signal.get("signal_id", "matrix_signal")
                    + f"__{spec.combine_id}"
                )
                cal = config.calendar
                out_run_id = (
                    f"rolling__{config.experiment_id}__{spec.combine_id}"
                    f"__{cal.get('start_date', '')}_{cal.get('end_date', '')}"
                )
                combined_output_ids.append(out_sig_id)
                combined_output_run_ids.append(out_run_id)

                combine_signals(
                    spec,
                    output_signal_id=out_sig_id,
                    output_signal_run_id=out_run_id,
                    signal_store=self._signal_store,
                    research_paths=self._paths,
                    overwrite=overwrite_signal,
                )

                combined_signal_refs.append(SignalRunRef(
                    generator_id=spec.combine_id,
                    transform_id="combined",
                    signal_id=out_sig_id,
                    signal_run_id=out_run_id,
                ))

                for lcfg in config.labels:
                    result = evaluator.evaluate(
                        signal_id=out_sig_id,
                        signal_run_id=out_run_id,
                        label_id=lcfg["label_id"],
                        score_column=config.signal.get("score_column", "score"),
                        overwrite=overwrite_eval,
                        require_pit_lineage=bool(
                            lcfg.get("require_pit_lineage", False)
                        ),
                        research_config_sha256=_research_config_sha256(config),
                    )
                    combined_eval_refs.append(SignalEvalRef(
                        signal_id=out_sig_id,
                        signal_run_id=out_run_id,
                        label_id=lcfg["label_id"],
                        eval_id=str(result.output_dir) if result.output_dir else None,
                    ))

            build_cross_signal_index(
                combine_specs,
                combined_output_ids,
                combined_output_run_ids,
                self._paths,
                config.experiment_id,
            )

        # ── 6. Write matrix_jobs.csv ──
        job_cols = ["generator_id", "transform_id", "signal_id", "signal_run_id",
                     "head_signal_id", "status"]
        job_rows: list[dict[str, Any]] = []
        for ref in signal_refs:
            job_rows.append({
                "generator_id": ref.generator_id,
                "transform_id": ref.transform_id,
                "signal_id": ref.signal_id,
                "signal_run_id": ref.signal_run_id,
                "head_signal_id": ref.head_signal_id or "",
                "status": "completed",
            })
        for ref in combined_signal_refs:
            job_rows.append({
                "generator_id": ref.generator_id,
                "transform_id": ref.transform_id,
                "signal_id": ref.signal_id,
                "signal_run_id": ref.signal_run_id,
                "head_signal_id": ref.head_signal_id or "",
                "status": "completed",
            })
        job_df = (
            pd.DataFrame(job_rows, columns=job_cols)
            if job_rows else pd.DataFrame(columns=job_cols)
        )
        job_df.to_csv(exp_dir / "matrix_jobs.csv", index=False)

        # ── 7. Manifest ──
        manifest = with_standard_metadata({
            "artifact_type": "signal_research",
            "mode": "matrix",
            "experiment_id": config.experiment_id,
            "generator_count": len(effective_generators),
            "transform_count": len(config.transforms),
            "combination_count": len(config.signal_combinations),
            "job_count": len(jobs),
            "window_count": len(windows),
            "signal_runs": [
                {"generator_id": r.generator_id, "transform_id": r.transform_id,
                 "signal_id": r.signal_id, "signal_run_id": r.signal_run_id}
                for r in signal_refs
            ],
            "combined_signal_runs": [
                {"combine_id": r.generator_id, "signal_id": r.signal_id,
                 "signal_run_id": r.signal_run_id}
                for r in combined_signal_refs
            ],
            "labels": config.labels,
            "date_range": {
                "start": config.calendar.get("start_date"),
                "end": config.calendar.get("end_date"),
            },
        })
        manifest_path = exp_dir / "signal_research_manifest.json"
        write_manifest(manifest_path, manifest)

        return SignalResearchResult(
            experiment_id=config.experiment_id,
            signal_runs=signal_refs + combined_signal_refs,
            eval_refs=eval_refs + combined_eval_refs,
            manifest_path=manifest_path,
        )
