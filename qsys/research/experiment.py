"""Experiment index — collect SignalEval and BacktestRun results.

Experiment is a lightweight reference layer that groups existing research
artifacts under one research question.  It creates index tables and a
summary markdown file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.research.manifest import write_manifest, with_standard_metadata
from qsys.research.paths import ResearchPaths

# ── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class ExperimentSpec:
    """Specification for creating an experiment index.

    Parameters
    ----------
    experiment_id:
        Unique identifier (used as directory name).
    title:
        Optional human-readable title.
    description:
        Optional description of the research question.
    tags:
        Optional list of tags for categorization.
    """
    experiment_id: str
    title: str | None = None
    description: str | None = None
    tags: list[str] | None = None


@dataclass
class ExperimentIndexResult:
    """Result of an experiment index rebuild."""
    experiment_id: str
    root: Path
    signal_run_count: int = 0
    signal_eval_count: int = 0
    backtest_count: int = 0
    output_dir: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if d.get("output_dir"):
            d["output_dir"] = str(d["output_dir"])
        if "root" in d:
            d["root"] = str(d["root"])
        return d


# ── CSV column definitions ───────────────────────────────────────────────────

_SIGNAL_RUN_REF_COLS = ["alias", "signal_id", "signal_run_id", "path", "note"]
_SIGNAL_EVAL_REF_COLS = ["alias", "signal_id", "signal_run_id", "label_id", "path", "note"]
_BACKTEST_REF_COLS = ["alias", "strategy_run_id", "backtest_id", "path", "note"]

_SIGNAL_RUN_INDEX_COLS = [
    "alias", "signal_id", "signal_run_id", "signal_kind",
    "prediction_start", "prediction_end", "row_count",
    "model_id", "feature_set_id", "label_id", "universe", "path",
]

_SIGNAL_EVAL_INDEX_COLS = [
    "alias", "signal_id", "signal_run_id", "label_id",
    "n_obs", "n_days", "ic_mean", "ic_std", "icir",
    "rank_ic_mean", "rank_ic_std", "rank_icir", "coverage_mean", "path",
]

_BACKTEST_INDEX_COLS = [
    "alias", "strategy_run_id", "backtest_id", "strategy_template_id",
    "signal_id", "signal_run_id", "model_mode", "rolling_train",
    "execution_timing", "start_date", "end_date", "trading_day_count",
    "initial_capital", "final_value", "total_return",
    "order_count_total", "filled_count_total", "rejected_count_total",
    "turnover_total", "avg_turnover", "path",
]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _maybe_json_load(path: Path) -> dict | None:
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    return None


def _read_signal_manifest(path: Path) -> dict:
    return _maybe_json_load(path) or {}


def _read_eval_summary(path: Path) -> dict:
    return _maybe_json_load(path) or {}


def _read_backtest_manifest(path: Path) -> dict:
    return _maybe_json_load(path) or {}


def _read_backtest_metrics(path: Path) -> dict:
    return _maybe_json_load(path) or {}


def _write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if df.empty:
        df = pd.DataFrame(columns=path.stem.split("_") if "_" in path.stem else [])
    df.to_csv(path, index=False)


def _safe_float(v: Any, default: float | None = None) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _safe_str(v: Any, default: str = "") -> str:
    return str(v) if v is not None else default


def _fmt_row(*values: Any) -> str:
    return " | ".join(_safe_str(v) for v in values)


# ── ExperimentIndex ──────────────────────────────────────────────────────────


class ExperimentIndex:
    """Create and manage experiment indexes.

    Parameters
    ----------
    root:
        Research root path (default ``data/research``).
    """

    def __init__(self, root: str | Path = "data/research") -> None:
        self._paths = ResearchPaths(root)

    # ── Create ─────────────────────────────────────────────────────────────

    def create(
        self,
        spec: ExperimentSpec | dict[str, Any],
        *,
        overwrite: bool = False,
    ) -> Path:
        """Create an experiment directory and write manifest.

        Parameters
        ----------
        spec:
            Experiment specification.
        overwrite:
            When ``True``, overwrite existing experiment.

        Returns
        -------
        Path
            Path to the experiment directory.
        """
        if isinstance(spec, dict):
            spec = ExperimentSpec(**{k: v for k, v in spec.items() if k in ExperimentSpec.__dataclass_fields__})

        exp_dir = self._paths.experiment_dir(spec.experiment_id)
        if exp_dir.exists() and not overwrite:
            raise FileExistsError(
                f"Experiment directory exists: {exp_dir} (use overwrite=True)"
            )
        exp_dir.mkdir(parents=True, exist_ok=True)

        manifest = with_standard_metadata({
            "artifact_type": "experiment_index",
            "experiment_id": spec.experiment_id,
            "title": spec.title,
            "description": spec.description,
            "tags": spec.tags or [],
        })
        write_manifest(exp_dir / "manifest.json", manifest)

        return exp_dir

    # ── Add references ─────────────────────────────────────────────────────

    def add_signal_run(
        self,
        experiment_id: str,
        *,
        signal_id: str,
        signal_run_id: str,
        alias: str | None = None,
        note: str | None = None,
    ) -> None:
        """Add a signal run reference to the experiment."""
        exp_dir = self._paths.experiment_dir(experiment_id)
        path = str(self._paths.signal_dir(signal_id, signal_run_id))
        row = {
            "alias": alias or f"{signal_id}:{signal_run_id}",
            "signal_id": signal_id,
            "signal_run_id": signal_run_id,
            "path": path,
            "note": note or "",
        }
        refs_path = exp_dir / "signal_run_refs.csv"
        if refs_path.exists():
            df = pd.read_csv(refs_path)
            df = pd.concat([df, pd.DataFrame([row])], ignore_index=True).drop_duplicates(
                subset=["signal_id", "signal_run_id"], keep="last"
            )
        else:
            df = pd.DataFrame([row], columns=_SIGNAL_RUN_REF_COLS)
        _write_csv(df, refs_path)

    def add_signal_eval(
        self,
        experiment_id: str,
        *,
        signal_id: str,
        signal_run_id: str,
        label_id: str,
        alias: str | None = None,
        note: str | None = None,
    ) -> None:
        """Add a signal evaluation reference to the experiment."""
        exp_dir = self._paths.experiment_dir(experiment_id)
        path = str(self._paths.signal_eval_dir(signal_id, signal_run_id, label_id))
        row = {
            "alias": alias or f"{signal_id}:{signal_run_id}:{label_id}",
            "signal_id": signal_id,
            "signal_run_id": signal_run_id,
            "label_id": label_id,
            "path": path,
            "note": note or "",
        }
        refs_path = exp_dir / "signal_eval_refs.csv"
        if refs_path.exists():
            df = pd.read_csv(refs_path)
            df = pd.concat([df, pd.DataFrame([row])], ignore_index=True).drop_duplicates(
                subset=["signal_id", "signal_run_id", "label_id"], keep="last"
            )
        else:
            df = pd.DataFrame([row], columns=_SIGNAL_EVAL_REF_COLS)
        _write_csv(df, refs_path)

    def add_backtest_run(
        self,
        experiment_id: str,
        *,
        strategy_run_id: str,
        backtest_id: str,
        alias: str | None = None,
        note: str | None = None,
    ) -> None:
        """Add a backtest run reference to the experiment."""
        exp_dir = self._paths.experiment_dir(experiment_id)
        path = str(self._paths.backtest_dir(strategy_run_id, backtest_id))
        row = {
            "alias": alias or f"{strategy_run_id}:{backtest_id}",
            "strategy_run_id": strategy_run_id,
            "backtest_id": backtest_id,
            "path": path,
            "note": note or "",
        }
        refs_path = exp_dir / "backtest_refs.csv"
        if refs_path.exists():
            df = pd.read_csv(refs_path)
            df = pd.concat([df, pd.DataFrame([row])], ignore_index=True).drop_duplicates(
                subset=["strategy_run_id", "backtest_id"], keep="last"
            )
        else:
            df = pd.DataFrame([row], columns=_BACKTEST_REF_COLS)
        _write_csv(df, refs_path)

    # ── Rebuild indexes ────────────────────────────────────────────────────

    def rebuild_indexes(self, experiment_id: str) -> ExperimentIndexResult:
        """Rebuild index CSV files from reference files."""
        exp_dir = self._paths.experiment_dir(experiment_id)
        rows: dict[str, list[dict[str, Any]]] = {
            "signal_run": [], "signal_eval": [], "backtest": [],
        }

        # Signal runs
        refs_path = exp_dir / "signal_run_refs.csv"
        if refs_path.exists():
            refs = pd.read_csv(refs_path)
            for _, r in refs.iterrows():
                mf = _read_signal_manifest(
                    Path(str(r["path"])) / "manifest.json"
                ) if pd.notna(r.get("path")) else {}
                status = "present" if mf else "missing"
                rows["signal_run"].append({
                    "alias": r.get("alias", ""),
                    "signal_id": r.get("signal_id", ""),
                    "signal_run_id": r.get("signal_run_id", ""),
                    "signal_kind": mf.get("signal_kind", ""),
                    "prediction_start": mf.get("prediction_start", ""),
                    "prediction_end": mf.get("prediction_end", ""),
                    "row_count": mf.get("row_count", ""),
                    "model_id": mf.get("model_id", ""),
                    "feature_set_id": mf.get("feature_set_id", ""),
                    "label_id": mf.get("label_id", ""),
                    "universe": mf.get("universe", ""),
                    "path": r.get("path", ""),
                    "status": status,
                    "missing_path": "" if status == "present" else r.get("path", ""),
                })

        # Signal evals
        if (exp_dir / "signal_eval_refs.csv").exists():
            refs = pd.read_csv(exp_dir / "signal_eval_refs.csv")
            for _, r in refs.iterrows():
                summary_path = Path(str(r["path"])) / "summary.json" if pd.notna(r.get("path")) else None
                summary = _read_eval_summary(summary_path) if summary_path else {}
                status = "present" if summary_path and summary_path.exists() and summary else "missing"
                rows["signal_eval"].append({
                    "alias": r.get("alias", ""),
                    "signal_id": r.get("signal_id", ""),
                    "signal_run_id": r.get("signal_run_id", ""),
                    "label_id": r.get("label_id", ""),
                    "n_obs": summary.get("n_obs"),
                    "n_days": summary.get("n_days"),
                    "ic_mean": summary.get("ic_mean"),
                    "ic_std": summary.get("ic_std"),
                    "icir": summary.get("icir"),
                    "rank_ic_mean": summary.get("rank_ic_mean"),
                    "rank_ic_std": summary.get("rank_ic_std"),
                    "rank_icir": summary.get("rank_icir"),
                    "coverage_mean": summary.get("coverage_mean"),
                    "path": r.get("path", ""),
                    "status": status,
                    "missing_path": "" if status == "present" else r.get("path", ""),
                })

        # Backtests
        if (exp_dir / "backtest_refs.csv").exists():
            refs = pd.read_csv(exp_dir / "backtest_refs.csv")
            for _, r in refs.iterrows():
                base = Path(str(r["path"])) if pd.notna(r.get("path")) else None
                mf = _read_backtest_manifest(base / "manifest.json") if base else {}
                metrics = _read_backtest_metrics(base / "metrics.json") if base else {}
                status = "present" if mf.get("backtest_id") else "missing"
                rows["backtest"].append({
                    "alias": r.get("alias", ""),
                    "strategy_run_id": r.get("strategy_run_id", ""),
                    "backtest_id": r.get("backtest_id", ""),
                    "strategy_template_id": mf.get("strategy_template_id", ""),
                    "signal_id": mf.get("signal_id", ""),
                    "signal_run_id": mf.get("signal_run_id", ""),
                    "model_mode": mf.get("model_mode", ""),
                    "rolling_train": mf.get("rolling_train", ""),
                    "execution_timing": mf.get("execution_timing", ""),
                    "start_date": mf.get("start_date", ""),
                    "end_date": mf.get("end_date", ""),
                    "trading_day_count": mf.get("trading_day_count", ""),
                    "initial_capital": metrics.get("initial_capital", mf.get("initial_capital")),
                    "final_value": metrics.get("final_value", mf.get("final_value")),
                    "total_return": metrics.get("total_return", mf.get("total_return")),
                    "order_count_total": metrics.get("order_count_total"),
                    "filled_count_total": metrics.get("filled_count_total"),
                    "rejected_count_total": metrics.get("rejected_count_total"),
                    "turnover_total": metrics.get("turnover_total"),
                    "avg_turnover": metrics.get("avg_turnover"),
                    "path": r.get("path", ""),
                    "status": status,
                    "missing_path": "" if status == "present" else r.get("path", ""),
                })

        # Write index CSVs
        signal_run_df = pd.DataFrame(rows["signal_run"], columns=_SIGNAL_RUN_INDEX_COLS + ["status", "missing_path"])
        signal_eval_df = pd.DataFrame(rows["signal_eval"], columns=_SIGNAL_EVAL_INDEX_COLS + ["status", "missing_path"])
        backtest_df = pd.DataFrame(rows["backtest"], columns=_BACKTEST_INDEX_COLS + ["status", "missing_path"])

        _write_csv(signal_run_df, exp_dir / "signal_run_index.csv")
        _write_csv(signal_eval_df, exp_dir / "signal_eval_index.csv")
        _write_csv(backtest_df, exp_dir / "backtest_index.csv")

        # Write summary.md
        self._write_summary(exp_dir, signal_run_df, signal_eval_df, backtest_df)

        return ExperimentIndexResult(
            experiment_id=experiment_id,
            root=exp_dir,
            signal_run_count=len(signal_run_df),
            signal_eval_count=len(signal_eval_df),
            backtest_count=len(backtest_df),
            output_dir=exp_dir,
        )

    # ── Load summary tables ────────────────────────────────────────────────

    def load_summary_tables(self, experiment_id: str) -> dict[str, pd.DataFrame]:
        """Load index CSV files as DataFrames."""
        exp_dir = self._paths.experiment_dir(experiment_id)
        result: dict[str, pd.DataFrame] = {}
        for key, fname in [
            ("signal_run", "signal_run_index.csv"),
            ("signal_eval", "signal_eval_index.csv"),
            ("backtest", "backtest_index.csv"),
        ]:
            p = exp_dir / fname
            if p.exists():
                result[key] = pd.read_csv(p)
            else:
                result[key] = pd.DataFrame()
        return result

    # ── Summary generation ─────────────────────────────────────────────────

    def _write_summary(
        self,
        exp_dir: Path,
        signal_run_df: pd.DataFrame,
        signal_eval_df: pd.DataFrame,
        backtest_df: pd.DataFrame,
    ) -> None:
        lines: list[str] = []
        manifest = _maybe_json_load(exp_dir / "manifest.json") or {}

        lines.append(f"# {manifest.get('title', exp_dir.name)}")
        lines.append("")
        if manifest.get("description"):
            lines.append(f"{manifest['description']}")
            lines.append("")

        # Signal runs
        lines.append("## Signal Runs")
        lines.append("")
        if not signal_run_df.empty:
            present = signal_run_df[signal_run_df["status"] == "present"]
            if not present.empty:
                lines.append(f"| {' | '.join(_SIGNAL_RUN_INDEX_COLS)} |")
                lines.append(f"|{'|'.join('---' for _ in _SIGNAL_RUN_INDEX_COLS)}|")
                for _, r in present.iterrows():
                    lines.append(f"| {_fmt_row(*[r.get(c, '') for c in _SIGNAL_RUN_INDEX_COLS])} |")
            missing_count = len(signal_run_df[signal_run_df["status"] == "missing"])
            if missing_count > 0:
                lines.append(f"\n*{missing_count} signal run(s) missing*")
        else:
            lines.append("*No signal runs referenced.*")
        lines.append("")

        # Signal evals (sorted by rank_icir desc)
        lines.append("## Signal Evaluations")
        lines.append("")
        if not signal_eval_df.empty:
            present = signal_eval_df[signal_eval_df["status"] == "present"].copy()
            if not present.empty and "rank_icir" in present.columns:
                present["_sort_icir"] = pd.to_numeric(present["rank_icir"], errors="coerce")
                present = present.sort_values("_sort_icir", ascending=False, na_position="last").drop(columns=["_sort_icir"])
            disp_cols = ["alias", "label_id", "n_obs", "n_days", "icir", "rank_icir", "coverage_mean"]
            lines.append(f"| {' | '.join(disp_cols)} |")
            lines.append(f"|{'|'.join('---' for _ in disp_cols)}|")
            for _, r in present.iterrows():
                lines.append(f"| {_fmt_row(*[r.get(c, '') for c in disp_cols])} |")
            missing_count = len(signal_eval_df[signal_eval_df["status"] == "missing"])
            if missing_count > 0:
                lines.append(f"\n*{missing_count} evaluation(s) missing*")
        else:
            lines.append("*No signal evaluations referenced.*")
        lines.append("")

        # Backtests (sorted by total_return desc)
        lines.append("## Backtests")
        lines.append("")
        if not backtest_df.empty:
            present = backtest_df[backtest_df["status"] == "present"].copy()
            if not present.empty and "total_return" in present.columns:
                present["_sort_ret"] = pd.to_numeric(present["total_return"], errors="coerce")
                present = present.sort_values("_sort_ret", ascending=False, na_position="last").drop(columns=["_sort_ret"])
            disp_cols = ["alias", "signal_id", "total_return", "final_value", "turnover_total", "trading_day_count"]
            lines.append(f"| {' | '.join(disp_cols)} |")
            lines.append(f"|{'|'.join('---' for _ in disp_cols)}|")
            for _, r in present.iterrows():
                lines.append(f"| {_fmt_row(*[r.get(c, '') for c in disp_cols])} |")
            missing_count = len(backtest_df[backtest_df["status"] == "missing"])
            if missing_count > 0:
                lines.append(f"\n*{missing_count} backtest(s) missing*")
        else:
            lines.append("*No backtests referenced.*")
        lines.append("")

        # Notes
        lines.append("## Notes")
        lines.append("")
        lines.append("_Generated by ExperimentIndex._")

        (exp_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")
