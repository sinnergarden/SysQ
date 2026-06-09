"""RollingResearchRunner — rolling research pipeline orchestrator.

v1: single-path rolling research (signal → eval → backtest → index).
v2: matrix experiment (generators × signal_transforms × strategies).

Both modes share rolling windows, SignalStore, SignalEvaluator,
BacktestRunner, and ExperimentIndex.

Backward-compatible re-exports
-------------------------------
This module re-exports the following for existing importers:

- ``RollingWindow``, ``build_rolling_windows`` → ``rolling_window``
- ``MatrixJob``, ``RollingResearchConfig``, ``build_matrix_jobs``,
  ``expand_multi_label_generators``, ``_create_generator_from_config``,
  ``apply_signal_transform``, ``LabelConfig``, ``BacktestConfig``,
  ``SignalTransformConfig``, ``_slugify_id`` → ``matrix_job``
- ``FixtureSignalGenerator``, ``MultiHeadFixtureGenerator`` → ``generators.fixture``
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.research.experiment import ExperimentIndex, ExperimentSpec
from qsys.research.generators.base import RollingSignalGenerator
from qsys.research.generators.fixture import (
    FixtureSignalGenerator,
    MultiHeadFixtureGenerator,
)
from qsys.research.manifest import write_manifest, with_standard_metadata
from qsys.research.matrix_job import (
    BacktestConfig,
    LabelConfig,
    MatrixJob,
    RollingResearchConfig,
    SignalTransformConfig,
    _create_generator_from_config,
    _slugify_id,
    apply_signal_transform,
    build_matrix_jobs,
    expand_multi_label_generators,
)
from qsys.research.paths import ResearchPaths
from qsys.research.rolling_window import RollingWindow, build_rolling_windows
from qsys.research.signal_combine import build_cross_signal_index
from qsys.signal.store import SignalStore

# ── Backward-compatible re-exports ────────────────────────────────────
# Everything above is importable from this module as before.

__all__ = [
    # re-exported classes/functions
    "RollingWindow",
    "build_rolling_windows",
    "MatrixJob",
    "RollingResearchConfig",
    "BacktestConfig",
    "LabelConfig",
    "SignalTransformConfig",
    "_slugify_id",
    "_create_generator_from_config",
    "apply_signal_transform",
    "build_matrix_jobs",
    "expand_multi_label_generators",
    "FixtureSignalGenerator",
    "MultiHeadFixtureGenerator",
    # own
    "RollingResearchRunner",
    "run_signal_backtests",
]

# ── Backtest triggering (extracted from _run_matrix) ──────────────────


def run_signal_backtests(
    signal_store: SignalStore,
    jobs: list[MatrixJob],
    config: RollingResearchConfig,
    *,
    overwrite: bool = False,
    research_root: str | Path = "data/research",
) -> tuple[list[dict[str, Any]], list[tuple[str, str, str, str]]]:
    """Run backtest for each job's strategy configs.

    This is a standalone function — it only runs BacktestRunner and returns
    results.  Callers are responsible for experiment index registration.

    Returns
    -------
    tuple (job_rows, bt_refs)
        job_rows: list of dicts for matrix_jobs.csv
        bt_refs: list of (signal_id, signal_run_id, strategy_run_id, backtest_id)
    """
    from qsys.backtest.strategy_runner import BacktestRunner

    bt_runner = BacktestRunner()
    all_bt_refs: list[tuple[str, str, str, str]] = []
    job_rows: list[dict[str, Any]] = []

    for job in jobs:
        for scfg in job.strategy_configs:
            bt_result = bt_runner.run_from_signal_cache(
                signal_id=job.signal_id,
                signal_run_id=job.signal_run_id,
                start_date=scfg.get("start_date", config.calendar.get("start_date")),
                end_date=scfg.get("end_date", config.calendar.get("end_date")),
                initial_capital=scfg.get("initial_capital", 1_000_000.0),
                top_n=scfg.get("top_n", 20),
                max_weight=scfg.get("max_weight"),
                strategy_template_id=scfg.get("strategy_template_id", "rank_weight_top20"),
                allocation_method=scfg.get("allocation_method", "rank_weight"),
                rebalance_freq=scfg.get("rebalance_freq", "weekly"),
                artifact_mode=scfg.get("artifact_mode", "summary"),
                overwrite=overwrite,
                research_root=str(research_root),
            )

            if not hasattr(bt_result, "artifacts") or not bt_result.artifacts:
                raise RuntimeError("BacktestRunResult missing artifacts dict")
            _mf_path = bt_result.artifacts.get("manifest")
            if not _mf_path or not Path(_mf_path).exists():
                raise RuntimeError(f"Backtest manifest not found: {_mf_path}")
            _mf = json.loads(Path(_mf_path).read_text())
            _sid = _mf.get("strategy_run_id")
            _bid = _mf.get("backtest_id")
            if not _sid or not _bid:
                raise RuntimeError(
                    f"Backtest manifest missing strategy_run_id or backtest_id in {_mf_path}"
                )
            all_bt_refs.append((job.signal_id, job.signal_run_id, _sid, _bid))

            job_rows.append({
                "generator_id": job.generator_id,
                "transform_id": job.transform_id,
                "strategy_id": scfg.get("strategy_id", scfg.get("strategy_template_id", "")),
                "signal_id": job.signal_id,
                "signal_run_id": job.signal_run_id,
                "head_signal_id": job.head_signal_id or "",
                "strategy_template_id": scfg.get("strategy_template_id", ""),
                "top_n": scfg.get("top_n", ""),
                "backtest_id": _bid,
                "strategy_run_id": _sid,
                "status": "completed",
            })

    return job_rows, all_bt_refs


# ── RollingResearchRunner ──────────────────────────────────────────────


class RollingResearchRunner:
    """Rolling research pipeline orchestrator.

    Parameters
    ----------
    root:
        Research root path (default ``data/research``).
    """

    def __init__(self, root: str | Path = "data/research") -> None:
        self.root = Path(root).resolve()
        self._paths = ResearchPaths(str(self.root))
        self._signal_store = SignalStore(str(self.root))
        self._experiment_index = ExperimentIndex(str(self.root))

    def run(
        self,
        config: RollingResearchConfig | dict[str, Any] | str | Path,
        *,
        signal_generator: RollingSignalGenerator | None = None,
        overwrite_signal: bool = False,
        overwrite_eval: bool = False,
        overwrite_backtest: bool = False,
        overwrite_experiment: bool = False,
    ) -> dict[str, Any]:
        """Execute a rolling research run.

        Parameters
        ----------
        config:
            Rolling research configuration.
        signal_generator:
            Callable for per-window signal generation.  When ``None``,
            uses ``FixtureSignalGenerator`` (v1) or config-driven
            generators (v2 matrix).
        overwrite_signal:
            Allow overwriting existing SignalRun.
        overwrite_eval:
            Allow overwriting existing SignalEvaluations.
        overwrite_backtest:
            Allow overwriting existing BacktestRuns.
        overwrite_experiment:
            Allow overwriting existing Experiment index.

        Returns
        -------
        dict
            Summary of the run.
        """
        if isinstance(config, (str, Path)):
            config = RollingResearchConfig.from_file(Path(config))
        elif isinstance(config, dict):
            config = RollingResearchConfig.from_dict(config)

        exp_dir = self._paths.experiment_dir(config.experiment_id)

        # -- 0. Pre-flight: validate label artifacts --
        if config.labels:
            from qsys.label.store import LabelStore
            _ls = LabelStore(str(self.root))
            _start = config.calendar.get("start_date")
            _end = config.calendar.get("end_date")
            for lcfg in config.labels:
                lid = lcfg["label_id"]
                _kwargs: dict[str, Any] = {"start": _start, "end": _end}
                if "universe" in lcfg:
                    _kwargs["universe"] = lcfg["universe"]
                elif config.generators:
                    _first_gen = config.generators[0]
                    _univ = _first_gen.get("params", {}).get("universe")
                    if _univ:
                        _kwargs["universe"] = _univ
                mc = lcfg.get("min_coverage")
                if mc is not None:
                    _kwargs["min_coverage"] = mc
                _ls.validate_label(lid, **_kwargs)
                print(f"  Label {lid}: pre-flight OK")

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

        # ── Dispatch: v2 matrix vs v1 single ──
        if config.generators:
            return self._run_matrix(
                config, windows,
                signal_generator=signal_generator,
                overwrite_signal=overwrite_signal,
                overwrite_eval=overwrite_eval,
                overwrite_backtest=overwrite_backtest,
                overwrite_experiment=overwrite_experiment,
            )

        # ── v1 single-signal path ──
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

        # ── Evaluate ──
        from qsys.research.evaluation import SignalEvaluator

        evaluator = SignalEvaluator(str(self.root))
        eval_count = 0
        for lcfg in config.labels:
            evaluator.evaluate(
                signal_id=signal_id,
                signal_run_id=signal_run_id,
                label_id=lcfg["label_id"],
                score_column=config.signal.get("score_column", "score"),
                overwrite=overwrite_eval,
            )
            eval_count += 1

        # ── Backtest (delegated) ──
        bt_count = 0
        bt_manifest_refs: list[tuple[str, str]] = []
        if config.backtests:
            jobs = [MatrixJob(
                generator_id="single",
                transform_id="raw",
                strategy_configs=config.backtests,
                signal_id=signal_id,
                signal_run_id=signal_run_id,
            )]
            bt_job_rows, bt_all_refs = run_signal_backtests(
                self._signal_store, jobs, config,
                overwrite=overwrite_backtest,
                research_root=str(self.root),
            )
            bt_count = len(bt_all_refs)
            # bt_all_refs format: [(signal_id, signal_run_id, strategy_run_id, backtest_id)]
            bt_manifest_refs = [(sr, bt) for _, _, sr, bt in bt_all_refs]

        # ── Experiment index ──
        self._experiment_index.create(
            ExperimentSpec(
                experiment_id=config.experiment_id,
                title=config.title,
                description=config.description,
            ),
            overwrite=overwrite_experiment,
        )

        self._experiment_index.add_signal_run(
            config.experiment_id, signal_id=signal_id, signal_run_id=signal_run_id,
        )
        for lcfg in config.labels:
            self._experiment_index.add_signal_eval(
                config.experiment_id,
                signal_id=signal_id, signal_run_id=signal_run_id,
                label_id=lcfg["label_id"],
            )
        for _sid, _bid in bt_manifest_refs:
            self._experiment_index.add_backtest_run(config.experiment_id, strategy_run_id=_sid, backtest_id=_bid)

        self._experiment_index.rebuild_indexes(config.experiment_id)

        # ── Rolling research manifest ──
        manifest = with_standard_metadata({
            "artifact_type": "rolling_research",
            "experiment_id": config.experiment_id,
            "signal_id": signal_id,
            "signal_run_id": signal_run_id,
            "window_count": len(windows),
            "date_range": {
                "start": config.calendar.get("start_date"),
                "end": config.calendar.get("end_date"),
            },
            "labels": config.labels,
            "backtests": config.backtests,
            "output_signal_path": str(self._paths.signal_dir(signal_id, signal_run_id)),
        })
        write_manifest(exp_dir / "rolling_research_manifest.json", manifest)

        return {
            "status": "passed",
            "experiment_id": config.experiment_id,
            "signal_id": signal_id,
            "signal_run_id": signal_run_id,
            "window_count": len(windows),
            "prediction_row_count": len(predictions),
            "signal_eval_count": eval_count,
            "backtest_count": bt_count,
            "output_dir": str(exp_dir),
        }

    # ── v2: matrix experiment ──────────────────────────────────────────

    def _run_matrix(
        self,
        config: RollingResearchConfig,
        windows: list[RollingWindow],
        *,
        signal_generator: RollingSignalGenerator | None = None,
        overwrite_signal: bool = False,
        overwrite_eval: bool = False,
        overwrite_backtest: bool = False,
        overwrite_experiment: bool = False,
    ) -> dict[str, Any]:
        """Matrix experiment: generators × transforms × strategies."""
        from qsys.research.evaluation import SignalEvaluator

        exp_dir = self._paths.experiment_dir(config.experiment_id)

        # ── 1. Create experiment index ──
        self._experiment_index.create(
            ExperimentSpec(
                experiment_id=config.experiment_id,
                title=config.title,
                description=config.description,
            ),
            overwrite=overwrite_experiment,
        )

        # ── 2. Expand multi-label generators into per-label entries ──
        effective_generators = expand_multi_label_generators(config.generators)

        # ── 3. Build matrix jobs ──
        jobs = build_matrix_jobs(config, effective_generators=effective_generators)
        explicit_generator = signal_generator is not None

        # ── 4. Generate raw predictions once per generator ──
        evaluator = SignalEvaluator(str(self.root))

        raw_predictions: dict[str, pd.DataFrame] = {}
        eval_count = 0

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

        # ── 5. For each job: transform → save → eval → index ──
        job_rows: list[dict[str, Any]] = []
        bt_refs_for_experiment: list[tuple[str, str, str, str]] = []

        for job in jobs:
            raw = raw_predictions[job.generator_id]

            # Multi-head: filter to rows belonging to this head's signal_id
            if job.head_signal_id:
                raw = raw[raw["signal_id"] == job.head_signal_id].copy()

            # Find transform config
            tf_cfg = next(
                (t for t in config.transforms if t["transform_id"] == job.transform_id),
                None,
            )
            if tf_cfg is None:
                raise ValueError(f"Transform config not found: {job.transform_id}")

            # Apply transform
            transformed = apply_signal_transform(raw, tf_cfg)
            transformed["signal_id"] = job.signal_id
            transformed["signal_run_id"] = job.signal_run_id

            # Save SignalRun
            self._signal_store.save_signal_run(
                job.signal_id, job.signal_run_id, transformed,
                manifest={
                    "model_mode": "rolling_matrix",
                    "window_count": len(windows),
                    "generator_id": job.generator_id,
                    "transform_id": job.transform_id,
                    "train_window_days": config.calendar.get("train_window_days"),
                },
                overwrite=overwrite_signal,
            )

            # Evaluate all labels
            for lcfg in config.labels:
                evaluator.evaluate(
                    signal_id=job.signal_id,
                    signal_run_id=job.signal_run_id,
                    label_id=lcfg["label_id"],
                    score_column=config.signal.get("score_column", "score"),
                    overwrite=overwrite_eval,
                )
                eval_count += 1

            # Register signal run and evals in experiment index
            self._experiment_index.add_signal_run(
                config.experiment_id,
                signal_id=job.signal_id,
                signal_run_id=job.signal_run_id,
            )
            for lcfg in config.labels:
                self._experiment_index.add_signal_eval(
                    config.experiment_id,
                    signal_id=job.signal_id,
                    signal_run_id=job.signal_run_id,
                    label_id=lcfg["label_id"],
                )

        # ── Backtest (delegated) ──
        bt_count = 0
        all_bt_refs: list[tuple[str, str, str, str]] = []
        if config.strategies and any(j.strategy_configs for j in jobs):
            bt_job_rows, all_bt_refs = run_signal_backtests(
                self._signal_store, jobs, config,
                overwrite=overwrite_backtest,
                research_root=str(self.root),
            )
            job_rows.extend(bt_job_rows)
            bt_count = len(all_bt_refs)
            for _, _, _sid, _bid in all_bt_refs:
                self._experiment_index.add_backtest_run(
                    config.experiment_id, strategy_run_id=_sid, backtest_id=_bid,
                )

        # ── 6 (cont). Signal combinations ──────────────────────────────
        combine_count = 0
        combined_signal_run_ids: list[str] = []
        if config.signal_combinations:
            from qsys.research.signal_combine import (
                CombineSpec,
                build_combine_spec_from_config,
                combine_signals,
            )

            signal_id_map: dict[str, str] = {}
            signal_run_id_map: dict[str, str] = {}
            for job in jobs:
                key = f"{job.generator_id}__{job.transform_id}"
                signal_id_map[key] = job.signal_id
                signal_run_id_map[key] = job.signal_run_id

            combine_specs: list[CombineSpec] = []
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

                combined_df = combine_signals(
                    spec,
                    output_signal_id=out_sig_id,
                    output_signal_run_id=out_run_id,
                    signal_store=self._signal_store,
                    research_paths=self._paths,
                    overwrite=overwrite_signal,
                )

                # Evaluate combined signal
                for lcfg in config.labels:
                    evaluator.evaluate(
                        signal_id=out_sig_id,
                        signal_run_id=out_run_id,
                        label_id=lcfg["label_id"],
                        score_column=config.signal.get("score_column", "score"),
                        overwrite=overwrite_eval,
                    )
                    eval_count += 1

                # Backtest combined signals (delegated)
                if config.strategies:
                    cmb_jobs = [MatrixJob(
                        generator_id=spec.combine_id,
                        transform_id="combined",
                        strategy_configs=config.strategies,
                        signal_id=out_sig_id,
                        signal_run_id=out_run_id,
                    )]
                    cmb_cfg = RollingResearchConfig(
                        experiment_id=config.experiment_id,
                        calendar=config.calendar,
                    )
                    cmb_job_rows, cmb_bt_refs = run_signal_backtests(
                        self._signal_store, cmb_jobs, cmb_cfg,
                        overwrite=overwrite_backtest,
                        research_root=str(self.root),
                    )
                    job_rows.extend(cmb_job_rows)
                    bt_count += len(cmb_bt_refs)
                    all_bt_refs.extend(cmb_bt_refs)
                    for _, _, _sid, _bid in cmb_bt_refs:
                        self._experiment_index.add_backtest_run(
                            config.experiment_id, strategy_run_id=_sid, backtest_id=_bid,
                        )

                # Register combined signal in experiment index
                self._experiment_index.add_signal_run(
                    config.experiment_id,
                    signal_id=out_sig_id,
                    signal_run_id=out_run_id,
                )
                for lcfg in config.labels:
                    self._experiment_index.add_signal_eval(
                        config.experiment_id,
                        signal_id=out_sig_id,
                        signal_run_id=out_run_id,
                        label_id=lcfg["label_id"],
                    )

                combine_count += 1
                combined_signal_run_ids.append(out_run_id)

            # Write cross_signal_index.csv
            build_cross_signal_index(
                combine_specs,
                combined_output_ids,
                combined_output_run_ids,
                self._paths,
                config.experiment_id,
            )

        # ── 7. Rebuild indexes ──
        self._experiment_index.rebuild_indexes(config.experiment_id)

        # ── 8. Write matrix_jobs.csv ──
        job_cols = [
            "generator_id", "transform_id", "strategy_id",
            "signal_id", "signal_run_id",
            "head_signal_id",
            "strategy_template_id", "top_n",
            "backtest_id", "strategy_run_id", "status",
        ]
        job_df = pd.DataFrame(job_rows, columns=job_cols) if job_rows else pd.DataFrame(columns=job_cols)
        job_df.to_csv(exp_dir / "matrix_jobs.csv", index=False)

        # ── 9. Rolling research manifest ──
        signal_runs_summary = [
            {
                "generator_id": j.generator_id,
                "transform_id": j.transform_id,
                "signal_id": j.signal_id,
                "signal_run_id": j.signal_run_id,
            }
            for j in jobs
        ]
        manifest = with_standard_metadata({
            "artifact_type": "rolling_research",
            "mode": "matrix",
            "matrix_purpose": "framework_boundary_smoke",
            "experiment_id": config.experiment_id,
            "generator_count": len(effective_generators),
            "transform_count": len(config.transforms),
            "combination_count": len(config.signal_combinations),
            "strategy_count": len(config.strategies),
            "job_count": len(jobs),
            "window_count": len(windows),
            "signal_runs": signal_runs_summary,
            "backtest_refs": [
                {"signal_id": sid, "signal_run_id": srid,
                 "strategy_run_id": srid2, "backtest_id": bid}
                for sid, srid, srid2, bid in all_bt_refs
            ],
            "labels": config.labels,
            "date_range": {
                "start": config.calendar.get("start_date"),
                "end": config.calendar.get("end_date"),
            },
        })
        write_manifest(exp_dir / "rolling_research_manifest.json", manifest)

        return {
            "status": "passed",
            "experiment_id": config.experiment_id,
            "mode": "matrix",
            "window_count": len(windows),
            "generator_count": len(effective_generators),
            "transform_count": len(config.transforms),
            "combination_count": len(config.signal_combinations),
            "strategy_count": len(config.strategies),
            "job_count": len(jobs),
            "signal_run_count": len(jobs) + combine_count,
            "combined_signal_run_count": combine_count,
            "signal_eval_count": eval_count,
            "backtest_count": bt_count,
            "output_dir": str(exp_dir),
        }
