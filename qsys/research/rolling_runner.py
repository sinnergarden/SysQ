"""RollingResearchRunner v1 — rolling research pipeline for Framework Stable 2.0.

Orchestrates:
  rolling windows → signal generator → SignalStore → SignalEvaluator
  → BacktestRunner.run_from_signal_cache → ExperimentIndex
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

    def __init__(self, n_instruments: int = 100) -> None:
        self._n_inst = n_instruments

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
        rng = np.random.default_rng(42)
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


# ── RollingResearchRunner ──────────────────────────────────────────────


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
class RollingResearchConfig:
    """Full configuration for a rolling research run."""

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
    # each backtest: strategy_template_id, top_n, max_weight, ...

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
        return cls(
            experiment_id=payload.get("experiment_id", "rolling_run"),
            title=payload.get("title"),
            description=payload.get("description"),
            calendar=cal,
            signal=sig,
            labels=labels,
            backtests=backtests,
        )


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
            uses ``FixtureSignalGenerator``.
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

        # ── 2. Generate predictions per window ──
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
