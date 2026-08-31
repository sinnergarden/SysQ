"""RollingResearchRunner — DEPRECATED rolling research pipeline orchestrator.

.. deprecated::
   Use :class:`~qsys.research.signal_pipeline.SignalResearchPipeline`
   for new signal research.  ``RollingResearchRunner`` is a backward-
   compatible wrapper that delegates signal generation + IC evaluation
   to ``SignalResearchPipeline``, then appends backtest + experiment
   index on top.

v1: single-path rolling research (signal → eval → backtest → index).
v2: matrix experiment (generators × signal_transforms × strategies).

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
from qsys.research.signal_pipeline import SignalResearchPipeline
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


# ── RollingResearchRunner (deprecated wrapper) ────────────────────────


class RollingResearchRunner:
    """DEPRECATED rolling research pipeline orchestrator.

    .. deprecated::
       Use ``SignalResearchPipeline`` for new signal research + evaluation.
       This class is retained for backward compatibility.

    Delegates signal generation + IC evaluation to
    :class:`~qsys.research.signal_pipeline.SignalResearchPipeline`, then
    runs backtests and registers results in ``ExperimentIndex``.
    """

    def __init__(self, root: str | Path = "data/research") -> None:
        self.root = Path(root).resolve()
        self._paths = ResearchPaths(str(self.root))
        self._signal_store = SignalStore(str(self.root))
        self._experiment_index = ExperimentIndex(str(self.root))
        # Internal pipeline for signal generation + evaluation
        self._signal_pipeline = SignalResearchPipeline(root)

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
        """Execute a rolling research run (deprecated).

        For new code, use ``SignalResearchPipeline.run()`` for signal + IC
        evaluation, then run backtests separately.
        """
        if isinstance(config, (str, Path)):
            config = RollingResearchConfig.from_file(Path(config))
        elif isinstance(config, dict):
            config = RollingResearchConfig.from_dict(config)

        # ── Delegate signal generation + evaluation to SignalResearchPipeline ──
        # We call the pipeline even when backtests are present, since the
        # pipeline only refuses if config.strategies or config.backtests
        # are set.  We temporarily strip them, then add back after.
        _saved_strategies = config.strategies
        _saved_backtests = config.backtests
        config.strategies = []
        config.backtests = []

        try:
            pipeline_result = self._signal_pipeline.run(
                config,
                signal_generator=signal_generator,
                overwrite_signal=overwrite_signal,
                overwrite_eval=overwrite_eval,
                use_feature_cache=config.use_feature_cache,
                materialize_on_miss=config.materialize_on_miss,
                feature_cache_root=config.feature_cache_root,
                source_manifest_hash=config.source_manifest_hash,
            )
        finally:
            config.strategies = _saved_strategies
            config.backtests = _saved_backtests

        exp_dir = self._paths.experiment_dir(config.experiment_id)
        eval_count = len(pipeline_result.eval_refs)

        # ── Experiment index ──
        self._experiment_index.create(
            ExperimentSpec(
                experiment_id=config.experiment_id,
                title=config.title,
                description=config.description,
            ),
            overwrite=overwrite_experiment,
        )
        for sref in pipeline_result.signal_runs:
            self._experiment_index.add_signal_run(
                config.experiment_id,
                signal_id=sref.signal_id,
                signal_run_id=sref.signal_run_id,
            )
        for eref in pipeline_result.eval_refs:
            self._experiment_index.add_signal_eval(
                config.experiment_id,
                signal_id=eref.signal_id,
                signal_run_id=eref.signal_run_id,
                label_id=eref.label_id,
            )

        # ── Backtests (from pipeline_result.signal_runs, not re-derived) ──
        # SignalResearchPipeline produces SignalRunRefs.  Backtest consumes them.
        bt_count = 0
        all_bt_refs: list[tuple[str, str, str, str]] = []
        combined_bt_refs: list[tuple[str, str, str, str]] = []
        bt_job_rows_v2: list[dict[str, Any]] = []

        if _saved_strategies:
            bt_jobs = [
                MatrixJob(
                    generator_id=sref.generator_id,
                    transform_id=sref.transform_id,
                    strategy_configs=_saved_strategies,
                    signal_id=sref.signal_id,
                    signal_run_id=sref.signal_run_id,
                    head_signal_id=sref.head_signal_id,
                )
                for sref in pipeline_result.signal_runs
            ]
            if bt_jobs:
                bt_job_rows, all_bt_refs = run_signal_backtests(
                    self._signal_store, bt_jobs, config,
                    overwrite=overwrite_backtest,
                    research_root=str(self.root),
                )
                bt_job_rows_v2.extend(bt_job_rows)
                bt_count = len(all_bt_refs)
                for _, _, _sid, _bid in all_bt_refs:
                    self._experiment_index.add_backtest_run(
                        config.experiment_id, strategy_run_id=_sid, backtest_id=_bid,
                    )

        if _saved_backtests:
            # v1 single-signal backtests
            signal_id = config.signal.get("signal_id", "rolling_signal")
            signal_run_id = config.signal.get("signal_run_id", "rolling_run")
            jobs = [MatrixJob(
                generator_id="single",
                transform_id="raw",
                strategy_configs=_saved_backtests,
                signal_id=signal_id,
                signal_run_id=signal_run_id,
            )]
            v1_job_rows, v1_bt_refs = run_signal_backtests(
                self._signal_store, jobs, config,
                overwrite=overwrite_backtest,
                research_root=str(self.root),
            )
            bt_job_rows_v2.extend(v1_job_rows)
            all_bt_refs.extend(v1_bt_refs)
            bt_count += len(v1_bt_refs)
            for _, _, _sid, _bid in v1_bt_refs:
                self._experiment_index.add_backtest_run(
                    config.experiment_id, strategy_run_id=_sid, backtest_id=_bid,
                )

        # Combined signal refs are already part of pipeline_result.signal_runs,
        # included in the backtest pass above (transform_id != "combined" check removed).

        # ── Rebuild indexes ──
        self._experiment_index.rebuild_indexes(config.experiment_id)

        # ── Write matrix_jobs.csv ──
        job_cols = [
            "generator_id", "transform_id", "strategy_id",
            "signal_id", "signal_run_id",
            "head_signal_id",
            "strategy_template_id", "top_n",
            "backtest_id", "strategy_run_id", "status",
        ]
        job_df = (
            pd.DataFrame(bt_job_rows_v2, columns=job_cols)
            if bt_job_rows_v2 else pd.DataFrame(columns=job_cols)
        )
        job_df.to_csv(exp_dir / "matrix_jobs.csv", index=False)

        # ── Rolling research manifest ──
        refs_key = "backtest_refs"
        is_matrix = bool(config.generators)
        combined_signal_run_count = sum(
            ref.transform_id == "combined"
            for ref in pipeline_result.signal_runs
        )
        manifest = with_standard_metadata({
            "artifact_type": "rolling_research",
            "experiment_id": config.experiment_id,
            "mode": "matrix" if is_matrix else "single",
            "generator_count": len(config.generators),
            "transform_count": len(config.transforms),
            "strategy_count": len(_saved_strategies or _saved_backtests),
            "combination_count": len(config.signal_combinations),
            "combined_signal_run_count": combined_signal_run_count,
            "job_count": len([
                ref for ref in pipeline_result.signal_runs
                if ref.transform_id != "combined"
            ]),
            "signal_runs": [
                {"generator_id": s.generator_id, "transform_id": s.transform_id,
                 "signal_id": s.signal_id, "signal_run_id": s.signal_run_id}
                for s in pipeline_result.signal_runs
            ],
            "window_count": len(
                build_rolling_windows(
                    config.calendar.get("start_date", ""),
                    config.calendar.get("end_date", ""),
                    train_window_days=config.calendar.get("train_window_days", 252),
                    step_days=config.calendar.get("step_days", 5),
                )
            ) if config.calendar else 0,
            "backtest_count": bt_count,
            refs_key: [
                {"signal_id": sid, "signal_run_id": srid,
                 "strategy_run_id": srid2, "backtest_id": bid}
                for sid, srid, srid2, bid in all_bt_refs + combined_bt_refs
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
            "mode": "matrix" if is_matrix else "single",
            "window_count": manifest.get("window_count", 0),
            "signal_run_count": len(pipeline_result.signal_runs),
            "signal_eval_count": eval_count,
            "backtest_count": bt_count,
            "combination_count": len(config.signal_combinations),
            "combined_signal_run_count": combined_signal_run_count,
            **(
                {
                    "signal_id": config.signal.get("signal_id", "rolling_signal"),
                    "signal_run_id": config.signal.get(
                        "signal_run_id", "rolling_run"
                    ),
                }
                if not is_matrix
                else {}
            ),
            "output_dir": str(exp_dir),
        }


