"""Signal evaluation — IC / RankIC / ICIR / Group Return computation.

Core functions are module-level for testability.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from qsys.label.store import LabelStore
from qsys.research.paths import ResearchPaths
from qsys.research.manifest import write_manifest, with_standard_metadata
from qsys.signal.store import SignalStore


# ── Core join ────────────────────────────────────────────────────────────────


def join_signal_label(
    signal: pd.DataFrame,
    labels: pd.DataFrame,
    score_column: str = "score",
) -> pd.DataFrame:
    """Inner-join signal and label on ``(trade_date, instrument)``.

    Filters
    -------
    - Signal rows with ``is_valid == False`` are excluded (when column exists).
    - Label rows with ``is_valid == False`` are excluded (when column exists).
    - Rows with null ``score_column`` or null ``label_value`` are excluded.
    """
    sig = signal.copy()
    lbl = labels.copy()

    if score_column not in sig.columns:
        raise ValueError(
            f"Score column {score_column!r} not found in signal columns: "
            f"{list(sig.columns)}"
        )
    if "label_value" not in lbl.columns:
        raise ValueError(
            f"label_value column not found in label columns: {list(lbl.columns)}"
        )

    if "is_valid" in sig.columns:
        sig = sig[sig["is_valid"] != False]  # noqa: E712
    if "is_valid" in lbl.columns:
        lbl = lbl[lbl["is_valid"] != False]  # noqa: E712

    sig = sig.dropna(subset=[score_column])
    lbl = lbl.dropna(subset=["label_value"])

    joined = pd.merge(
        sig, lbl,
        on=["trade_date", "instrument"],
        how="inner",
        suffixes=("_signal", "_label"),
    )

    if "label_id_signal" in joined.columns:
        joined = joined.drop(columns=["label_id_signal"])

    return joined.reset_index(drop=True)


# ── Daily IC ─────────────────────────────────────────────────────────────────


def compute_daily_ic(
    joined: pd.DataFrame,
    score_column: str = "score",
    min_count: int = 5,
) -> pd.DataFrame:
    """Compute daily Pearson IC.

    Returns a DataFrame with columns ``date``, ``ic``, ``n``.
    """
    dates = joined["trade_date"].unique()

    rows = []
    for d in sorted(dates):
        day = joined[joined["trade_date"] == d]
        if len(day) < min_count:
            rows.append({"date": d, "ic": None, "n": len(day)})
            continue
        corr = day[score_column].corr(day["label_value"])
        rows.append({"date": d, "ic": corr, "n": len(day)})

    return pd.DataFrame(rows)


def compute_daily_rank_ic(
    joined: pd.DataFrame,
    score_column: str = "score",
    min_count: int = 5,
) -> pd.DataFrame:
    """Compute daily Spearman Rank IC.

    Uses pandas rank then Pearson correlation of ranks.
    Returns a DataFrame with columns ``date``, ``rank_ic``, ``n``.
    """
    dates = joined["trade_date"].unique()

    rows = []
    for d in sorted(dates):
        day = joined[joined["trade_date"] == d]
        if len(day) < min_count:
            rows.append({"date": d, "rank_ic": None, "n": len(day)})
            continue
        score_rank = day[score_column].rank()
        label_rank = day["label_value"].rank()
        corr = score_rank.corr(label_rank)
        rows.append({"date": d, "rank_ic": corr, "n": len(day)})

    return pd.DataFrame(rows)


# ── ICIR helpers ─────────────────────────────────────────────────────────────


def _ic_stats(ic_series: pd.Series) -> dict[str, float | None]:
    """Compute IC mean, std, and ICIR from a daily IC series."""
    valid = ic_series.dropna()
    if len(valid) < 2:
        return {"mean": None, "std": None, "ir": None}
    mean = float(valid.mean())
    std = float(valid.std(ddof=1))
    ir = mean / std if std > 1e-12 else None
    return {"mean": mean, "std": std, "ir": ir}


# ── Group returns ────────────────────────────────────────────────────────────


def compute_group_returns(
    joined: pd.DataFrame,
    score_column: str = "score",
    n_groups: int = 5,
) -> pd.DataFrame:
    """Compute equal-weighted quantile portfolio returns per date.

    Groups are numbered 1 (lowest score) to *n_groups* (highest score).

    Returns a DataFrame with columns ``trade_date``, ``group_id``,
    ``mean_return``, ``count``.
    """
    groups: list[pd.DataFrame] = []

    for d in sorted(joined["trade_date"].unique()):
        day = joined[joined["trade_date"] == d].copy()
        if day.empty:
            continue

        day["group_id"] = pd.qcut(
            day[score_column].rank(method="first"),
            q=n_groups,
            duplicates="drop",
        )
        day["group_id"] = day["group_id"].cat.codes + 1
        grp = day.groupby("group_id", observed=True).agg(
            mean_return=("label_value", "mean"),
            count=("label_value", "count"),
        ).reset_index()
        grp["trade_date"] = d
        groups.append(grp)

    if not groups:
        return pd.DataFrame(columns=["trade_date", "group_id", "mean_return", "count"])

    result = pd.concat(groups, ignore_index=True)
    result["group_id"] = result["group_id"].astype(int)
    return result


# ── Coverage ─────────────────────────────────────────────────────────────────


def compute_coverage(
    signal: pd.DataFrame, joined: pd.DataFrame,
) -> pd.DataFrame:
    """Per-date coverage ratio of joined rows over signal rows.

    Returns a DataFrame with columns ``date``, ``signal_count``,
    ``joined_count``, ``coverage``.
    """
    sig_counts = signal.groupby("trade_date").size().reset_index(name="signal_count")
    jn_counts = joined.groupby("trade_date").size().reset_index(name="joined_count")

    merged = pd.merge(sig_counts, jn_counts, on="trade_date", how="left")
    merged["joined_count"] = merged["joined_count"].fillna(0).astype(int)
    merged["coverage"] = np.where(
        merged["signal_count"] > 0,
        merged["joined_count"] / merged["signal_count"],
        None,
    )
    return merged.rename(columns={"trade_date": "date"})


# ── Result dataclass ─────────────────────────────────────────────────────────


@dataclass
class SignalEvaluationResult:
    """Structured result from a signal evaluation run."""

    signal_id: str
    signal_run_id: str
    label_id: str
    score_column: str
    n_obs: int
    n_days: int
    ic_mean: float | None = None
    ic_std: float | None = None
    icir: float | None = None
    rank_ic_mean: float | None = None
    rank_ic_std: float | None = None
    rank_icir: float | None = None
    coverage_mean: float | None = None
    output_dir: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if d["output_dir"]:
            d["output_dir"] = str(d["output_dir"])
        return d


# ── Output helpers ───────────────────────────────────────────────────────────


def _write_parquet_or_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import pyarrow  # noqa: F401
        df.to_parquet(path.with_suffix(".parquet"), index=False)
    except ImportError:
        df.to_csv(path.with_suffix(".csv"), index=False)


# ── Evaluator ────────────────────────────────────────────────────────────────


class SignalEvaluator:
    """Evaluate a saved SignalRun against a saved Label artifact.

    Parameters
    ----------
    root:
        Research root path (default ``data/research``).
    """

    def __init__(self, root: str | Path = "data/research") -> None:
        self.root = Path(root).resolve()
        self._signal_store = SignalStore(str(self.root))
        self._label_store = LabelStore(str(self.root))
        self._paths = ResearchPaths(str(self.root))

    def evaluate(
        self,
        *,
        signal_id: str,
        signal_run_id: str,
        label_id: str,
        score_column: str = "score",
        n_groups: int = 5,
        start_date: str | None = None,
        end_date: str | None = None,
        min_count: int = 5,
        output_dir: Path | None = None,
        overwrite: bool = False,
    ) -> SignalEvaluationResult:
        """Run full evaluation and write artifacts."""
        # 1. Load data
        signal = self._signal_store.load_signal_run(
            signal_id, signal_run_id,
            start_date=start_date, end_date=end_date,
        )
        labels = self._label_store.load_labels(
            label_id,
            start_date=start_date, end_date=end_date,
        )

        # 2. Join
        joined = join_signal_label(signal, labels, score_column=score_column)
        if joined.empty:
            return SignalEvaluationResult(
                signal_id=signal_id, signal_run_id=signal_run_id,
                label_id=label_id, score_column=score_column,
                n_obs=0, n_days=0,
            )

        # 3. Daily IC / RankIC
        ic_df = compute_daily_ic(joined, score_column, min_count)
        rank_ic_df = compute_daily_rank_ic(joined, score_column, min_count)

        ic_stats = _ic_stats(ic_df["ic"])
        rank_ic_stats = _ic_stats(rank_ic_df["rank_ic"])

        # 4. Group returns
        grp_df = compute_group_returns(joined, score_column, n_groups)

        # 5. Coverage
        cov_df = compute_coverage(signal, joined)
        cov_mean = float(cov_df["coverage"].mean()) if "coverage" in cov_df.columns and len(cov_df) > 0 else None

        # 6. Resolve output dir
        if output_dir is None:
            output_dir = self._paths.signal_eval_dir(signal_id, signal_run_id, label_id)

        # 7. Write artifacts
        if output_dir.exists() and not overwrite:
            raise FileExistsError(
                f"Eval output dir already exists: {output_dir} (use overwrite=True)"
            )

        output_dir.mkdir(parents=True, exist_ok=True)

        summary = {
            "signal_id": signal_id,
            "signal_run_id": signal_run_id,
            "label_id": label_id,
            "score_column": score_column,
            "n_groups": n_groups,
            "n_obs": len(joined),
            "n_days": len(ic_df),
            "ic_mean": ic_stats["mean"],
            "ic_std": ic_stats["std"],
            "icir": ic_stats["ir"],
            "rank_ic_mean": rank_ic_stats["mean"],
            "rank_ic_std": rank_ic_stats["std"],
            "rank_icir": rank_ic_stats["ir"],
            "coverage_mean": cov_mean,
            "start_date": str(joined["trade_date"].min()) if len(joined) > 0 else None,
            "end_date": str(joined["trade_date"].max()) if len(joined) > 0 else None,
        }
        summary = with_standard_metadata(summary)
        write_manifest(output_dir / "summary.json", summary)

        _write_parquet_or_csv(ic_df, output_dir / "ic_daily.parquet")
        _write_parquet_or_csv(rank_ic_df, output_dir / "rank_ic_daily.parquet")
        _write_parquet_or_csv(grp_df, output_dir / "group_returns.parquet")
        _write_parquet_or_csv(cov_df, output_dir / "coverage.parquet")

        manifest = {
            "artifact_type": "signal_evaluation",
            "inputs": {
                "signal_id": signal_id,
                "signal_run_id": signal_run_id,
                "label_id": label_id,
                "score_column": score_column,
            },
        }
        write_manifest(output_dir / "manifest.json", with_standard_metadata(manifest))

        return SignalEvaluationResult(
            signal_id=signal_id,
            signal_run_id=signal_run_id,
            label_id=label_id,
            score_column=score_column,
            n_obs=len(joined),
            n_days=len(ic_df),
            ic_mean=ic_stats["mean"],
            ic_std=ic_stats["std"],
            icir=ic_stats["ir"],
            rank_ic_mean=rank_ic_stats["mean"],
            rank_ic_std=rank_ic_stats["std"],
            rank_icir=rank_ic_stats["ir"],
            coverage_mean=cov_mean,
            output_dir=output_dir,
        )
