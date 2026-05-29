"""RollingResearchRunner v2 — rolling research pipeline with matrix experiment support.

v1: single-path rolling research (signal → eval → backtest → index).
v2: matrix experiment (generators × signal_transforms × strategies).

Both modes share rolling windows, SignalStore, SignalEvaluator,
BacktestRunner, and ExperimentIndex.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from qsys.research.experiment import ExperimentIndex, ExperimentSpec
from qsys.research.manifest import write_manifest, with_standard_metadata
from qsys.research.paths import ResearchPaths
from qsys.signal.store import SignalStore


# ── Rolling window builder ─────────────────────────────────────────────


@dataclass
class RollingWindow:
    window_id: str
    train_start: str
    train_end: str
    predict_start: str
    predict_end: str


def _calendar_backdate(start_date: str, n_days: int, buffer: int = 10) -> str:
    from datetime import datetime, timedelta
    dt = datetime.strptime(start_date, "%Y-%m-%d")
    estimated = dt - timedelta(days=int(n_days * 1.4) + buffer + 5)
    return estimated.strftime("%Y-%m-%d")


def build_rolling_windows(
    start_date: str,
    end_date: str,
    *,
    train_window_days: int = 252,
    predict_window_days: int = 5,
    step_days: int = 5,
) -> list[RollingWindow]:
    from qsys.data.calendar import get_trading_calendar

    _extended_start = _calendar_backdate(start_date, train_window_days)
    full_cal = get_trading_calendar(_extended_start, end_date)
    if not full_cal:
        raise ValueError(f"No trading dates in [{_extended_start}, {end_date}]")

    pred_cal = [d for d in full_cal if start_date <= d <= end_date]
    if not pred_cal:
        raise ValueError(f"No trading dates in [{start_date}, {end_date}]")

    first_pred_idx = full_cal.index(pred_cal[0])
    windows: list[RollingWindow] = []

    for offset in range(0, len(pred_cal), step_days):
        i = first_pred_idx + offset
        pred_end_offset = offset + predict_window_days - 1
        if pred_end_offset >= len(pred_cal):
            break

        predict_start = pred_cal[offset]
        predict_end = pred_cal[pred_end_offset]
        train_end_idx = i - 1
        train_start_idx = i - train_window_days

        if train_start_idx < 0:
            continue

        train_start = full_cal[train_start_idx]
        train_end = full_cal[train_end_idx] if train_end_idx >= 0 else full_cal[0]

        windows.append(RollingWindow(
            window_id=f"w{i:04d}",
            train_start=train_start,
            train_end=train_end,
            predict_start=predict_start,
            predict_end=predict_end,
        ))

    return windows


# ── Signal generator protocol ──────────────────────────────────────────


class RollingSignalGenerator(Protocol):
    """Protocol for per-window signal generation.

    Implementations must return a SignalStore-compatible DataFrame with
    columns: trade_date, data_date, instrument, signal_id, signal_run_id, score.
    """

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
        ...


class FixtureSignalGenerator:
    """Deterministic fixture generator for testing / CI.

    Returns random-shaped signals that are valid for SignalStore.
    """

    def __init__(self, n_instruments: int = 100, seed: int = 42) -> None:
        self._n_inst = n_instruments
        self._seed = seed

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
        import numpy as np

        cal = [predict_start, predict_end]
        try:
            from qsys.data.calendar import get_trading_calendar
            cal = get_trading_calendar(predict_start, predict_end)
        except Exception:
            pass

        from datetime import datetime, timedelta
        rng = np.random.default_rng(self._seed)
        rows = []
        for d in sorted(cal):
            prev = (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d")
            for ii in range(self._n_inst):
                rows.append({
                    "trade_date": d,
                    "data_date": prev,
                    "instrument": f"000{ii:04d}.SZ",
                    "signal_id": signal_id,
                    "signal_run_id": signal_run_id,
                    "score": float(rng.normal(0, 1)),
                })
        return pd.DataFrame(rows)


# ── Config dataclasses ─────────────────────────────────────────────────


@dataclass
class LabelConfig:
    label_id: str


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


@dataclass
class MatrixJob:
    """One cell in the matrix: a (generator, transform) pair with strategy configs.

    Each job produces one SignalRun (shared across all strategies).
    """
    generator_id: str
    transform_id: str
    strategy_configs: list[dict[str, Any]]
    signal_id: str
    signal_run_id: str


@dataclass
class RollingResearchConfig:
    """Full configuration for a rolling research run.

    v1 single-signal mode: set ``signal`` (and optionally ``backtests``).
    v2 matrix mode: set ``generators``, ``signal_transforms``, and ``strategies``.
    """

    experiment_id: str
    title: str | None = None
    description: str | None = None

    calendar: dict[str, Any] = field(default_factory=dict)
    # calendar keys: start_date, end_date, train_window_days, predict_window_days, step_days

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

    @classmethod
    def from_file(cls, path: Path) -> RollingResearchConfig:
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
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: dict) -> RollingResearchConfig:
        cal = payload.get("calendar", {})
        sig = payload.get("signal", {})
        labels = payload.get("labels", [])
        backtests = payload.get("backtests", [])
        generators = payload.get("generators", [])
        transforms = payload.get("signal_transforms", [])
        strategies = payload.get("strategies", [])
        return cls(
            experiment_id=payload.get("experiment_id", "rolling_run"),
            title=payload.get("title"),
            description=payload.get("description"),
            calendar=cal,
            signal=sig,
            labels=labels,
            backtests=backtests,
            generators=generators,
            transforms=transforms,
            strategies=strategies,
        )


# ── Generator factory ──────────────────────────────────────────────────


def _create_generator_from_config(gen_config: dict) -> RollingSignalGenerator:
    """Create a generator instance from a config dict.

    Supports ``type: fixture`` only in this PR.
    """
    gen_type = gen_config.get("type", "fixture")
    params = gen_config.get("params", {})
    if gen_type == "fixture":
        return FixtureSignalGenerator(
            n_instruments=params.get("n_instruments", 100),
            seed=params.get("seed", 42),
        )
    raise ValueError(f"Unknown generator type: {gen_type!r}")


# ── Signal transforms ─────────────────────────────────────────────────


def apply_signal_transform(
    frame: pd.DataFrame,
    transform_config: SignalTransformConfig | dict[str, Any],
) -> pd.DataFrame:
    """Apply a signal transform to a predictions DataFrame.

    Parameters
    ----------
    frame:
        DataFrame with at least columns ``trade_date``, ``score``.
    transform_config:
        Transform specification with ``transform_id`` and ``type``.

    Returns
    -------
    pd.DataFrame
        Frame with the same columns plus ``score_raw`` and ``transform_id``.
        Column ``score`` is replaced with the transformed values.
    """
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
    else:
        raise ValueError(f"Unknown signal transform type: {transform_config.type!r}")

    return result


# ── Matrix job builder ────────────────────────────────────────────────


def build_matrix_jobs(config: RollingResearchConfig) -> list[MatrixJob]:
    """Expand a matrix config into individual (generator, transform) jobs.

    Each job carries the full list of strategy configs so that generation
    and transform are performed once and backtests are run per strategy.
    """
    base_signal_id = config.signal.get("signal_id", "matrix_signal")
    experiment_id = config.experiment_id
    cal = config.calendar
    start = cal.get("start_date", "")
    end = cal.get("end_date", "")

    jobs: list[MatrixJob] = []
    for gen_cfg in config.generators:
        gen_id = gen_cfg["generator_id"]
        for tf_cfg in config.transforms:
            tf_id = tf_cfg["transform_id"]
            signal_id = f"{base_signal_id}__{gen_id}__{tf_id}"
            signal_run_id = (
                f"rolling__{experiment_id}__{gen_id}__{tf_id}__{start}_{end}"
            )
            jobs.append(MatrixJob(
                generator_id=gen_id,
                transform_id=tf_id,
                strategy_configs=config.strategies,
                signal_id=signal_id,
                signal_run_id=signal_run_id,
            ))
    return jobs


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

        # ── 1. Build rolling windows ──
        windows = build_rolling_windows(
            config.calendar.get("start_date", ""),
            config.calendar.get("end_date", ""),
            train_window_days=config.calendar.get("train_window_days", 252),
            predict_window_days=config.calendar.get("predict_window_days", 5),
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

        # ── v1 single-signal path (unchanged) ──
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
                "predict_window_days": config.calendar.get("predict_window_days"),
            },
            overwrite=overwrite_signal,
        )

        # ── 3. Evaluate ──
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

        # ── 4. Backtest ──
        from qsys.backtest.strategy_runner import BacktestRunner

        runner = BacktestRunner()
        bt_count = 0
        bt_manifest_refs: list[tuple[str, str]] = []
        for btc in config.backtests:
            bt_result = runner.run_from_signal_cache(
                signal_id=signal_id,
                signal_run_id=signal_run_id,
                start_date=btc.get("start_date", config.calendar.get("start_date")),
                end_date=btc.get("end_date", config.calendar.get("end_date")),
                initial_capital=btc.get("initial_capital", 1_000_000.0),
                top_n=btc.get("top_n", 20),
                max_weight=btc.get("max_weight"),
                strategy_template_id=btc.get("strategy_template_id", "rank_weight_top20"),
                allocation_method=btc.get("allocation_method", "rank_weight"),
                rebalance_freq=btc.get("rebalance_freq", "weekly"),
                artifact_mode=btc.get("artifact_mode", "summary"),
                overwrite=overwrite_backtest,
            )
            if not hasattr(bt_result, "artifacts") or not bt_result.artifacts:
                raise RuntimeError("BacktestRunResult missing artifacts dict")
            _mf_path = bt_result.artifacts.get("manifest")
            if not _mf_path or not Path(_mf_path).exists():
                raise RuntimeError(f"Backtest manifest not found: {_mf_path}")
            import json as _j
            _mf = _j.loads(Path(_mf_path).read_text())
            _sid = _mf.get("strategy_run_id")
            _bid = _mf.get("backtest_id")
            if not _sid or not _bid:
                raise RuntimeError(f"Backtest manifest missing strategy_run_id or backtest_id in {_mf_path}")
            bt_manifest_refs.append((_sid, _bid))
            bt_count += 1

        # ── 5. Experiment index ──
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

        index_result = self._experiment_index.rebuild_indexes(config.experiment_id)

        # ── 6. Rolling research manifest ──
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
        from qsys.backtest.strategy_runner import BacktestRunner

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

        # ── 2. Build matrix jobs ──
        jobs = build_matrix_jobs(config)
        explicit_generator = signal_generator is not None

        # ── 3. Generate raw predictions once per generator ──
        evaluator = SignalEvaluator(str(self.root))
        bt_runner = BacktestRunner()

        raw_predictions: dict[str, pd.DataFrame] = {}
        eval_count = 0
        bt_count = 0
        all_bt_refs: list[tuple[str, str, str, str]] = []

        for gen_cfg in config.generators:
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

        # ── 4. For each job: transform → save → eval → backtest → index ──
        job_rows: list[dict[str, Any]] = []

        for job in jobs:
            raw = raw_predictions[job.generator_id]

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
                    "predict_window_days": config.calendar.get("predict_window_days"),
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

            # Run backtest for each strategy
            bt_refs_for_job: list[tuple[str, str]] = []
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
                    overwrite=overwrite_backtest,
                    research_root=str(self.root),
                )

                # Extract backtest IDs from manifest
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
                bt_refs_for_job.append((_sid, _bid))
                all_bt_refs.append((job.signal_id, job.signal_run_id, _sid, _bid))
                bt_count += 1

                job_rows.append({
                    "generator_id": job.generator_id,
                    "transform_id": job.transform_id,
                    "strategy_id": scfg.get("strategy_id", scfg.get("strategy_template_id", "")),
                    "signal_id": job.signal_id,
                    "signal_run_id": job.signal_run_id,
                    "strategy_template_id": scfg.get("strategy_template_id", ""),
                    "top_n": scfg.get("top_n", ""),
                    "backtest_id": _bid,
                    "strategy_run_id": _sid,
                    "status": "completed",
                })

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
            for _sid, _bid in bt_refs_for_job:
                self._experiment_index.add_backtest_run(
                    config.experiment_id,
                    strategy_run_id=_sid,
                    backtest_id=_bid,
                )

        # ── 5. Rebuild indexes ──
        self._experiment_index.rebuild_indexes(config.experiment_id)

        # ── 6. Write matrix_jobs.csv ──
        job_cols = [
            "generator_id", "transform_id", "strategy_id",
            "signal_id", "signal_run_id",
            "strategy_template_id", "top_n",
            "backtest_id", "strategy_run_id", "status",
        ]
        job_df = pd.DataFrame(job_rows, columns=job_cols)
        job_df.to_csv(exp_dir / "matrix_jobs.csv", index=False)

        # ── 7. Rolling research manifest ──
        signal_runs_summary = [
            {
                "generator_id": j.generator_id,
                "transform_id": j.transform_id,
                "signal_id": j.signal_id,
                "signal_run_id": j.signal_run_id,
            }
            for j in jobs
        ]
        bt_refs_summary = [
            {
                "signal_id": sid,
                "signal_run_id": srid,
                "strategy_run_id": srid2,
                "backtest_id": bid,
            }
            for sid, srid, srid2, bid in all_bt_refs
        ]
        manifest = with_standard_metadata({
            "artifact_type": "rolling_research",
            "mode": "matrix",
            "experiment_id": config.experiment_id,
            "generator_count": len(config.generators),
            "transform_count": len(config.transforms),
            "strategy_count": len(config.strategies),
            "job_count": len(jobs),
            "window_count": len(windows),
            "signal_runs": signal_runs_summary,
            "backtest_refs": bt_refs_summary,
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
            "generator_count": len(config.generators),
            "transform_count": len(config.transforms),
            "strategy_count": len(config.strategies),
            "job_count": len(jobs),
            "signal_run_count": len(jobs),
            "signal_eval_count": eval_count,
            "backtest_count": bt_count,
            "output_dir": str(exp_dir),
        }
