"""SignalResearchPipeline — signal generation + IC evaluation only.

Enforces the boundary: signal research produces SignalRun + IC evidence.
Strategy backtest is a separate concern (see ``BacktestRunner``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

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
from qsys.signal.store import SignalStore


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
        """
        if isinstance(config, (str, Path)):
            config = RollingResearchConfig.from_file(Path(config))
        elif isinstance(config, dict):
            config = RollingResearchConfig.from_dict(config)

        self._validate_config(config)

        exp_dir = self._paths.experiment_dir(config.experiment_id)

        # ── 1. Build rolling windows ──
        windows = build_rolling_windows(
            config.calendar.get("start_date", ""),
            config.calendar.get("end_date", ""),
            train_window_days=config.calendar.get("train_window_days", 252),
            step_days=config.calendar.get("step_days", 5),
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
        self._signal_store.save_signal_run(
            signal_id, signal_run_id, predictions,
            manifest={
                "model_mode": "rolling_train",
                "window_count": len(windows),
                "train_window_days": config.calendar.get("train_window_days"),
            },
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

        # ── 1. Expand multi-label → per-label entries ──
        effective_generators = expand_multi_label_generators(config.generators)

        # ── 2. Build matrix jobs ──
        jobs = build_matrix_jobs(config, effective_generators=effective_generators)
        explicit_generator = signal_generator is not None

        # ── 3. Generate raw predictions once per generator ──
        raw_predictions: dict[str, pd.DataFrame] = {}
        for gen_cfg in effective_generators:
            gen_id = gen_cfg["generator_id"]
            gen = signal_generator if explicit_generator else _create_generator_from_config(gen_cfg)

            all_preds: list[pd.DataFrame] = []
            for w in windows:
                pred = gen.generate(
                    train_start=w.train_start,
                    train_end=w.train_end,
                    predict_start=w.predict_start,
                    predict_end=w.predict_end,
                    signal_id="__internal__",
                    signal_run_id="__internal__",
                )
                all_preds.append(pred)
            raw_predictions[gen_id] = pd.concat(all_preds, ignore_index=True)

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

            self._signal_store.save_signal_run(
                job.signal_id, job.signal_run_id, transformed,
                manifest={
                    "model_mode": "signal_research_matrix",
                    "window_count": len(windows),
                    "generator_id": job.generator_id,
                    "transform_id": job.transform_id,
                    "train_window_days": config.calendar.get("train_window_days"),
                },
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
