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


# ── IC distribution stats ───────────────────────────────────────────────────


def _ic_distribution_stats(ic_series: pd.Series) -> dict[str, Any]:
    """Compute IC positive ratio, quantiles, and extreme ratio.

    Parameters
    ----------
    ic_series:
        Daily IC series (may contain NaN).

    Returns
    -------
    dict
        Keys: ``positive_ratio``, ``quantiles`` (5/25/50/75/95),
        ``extreme_ratio`` (fraction of days with |IC| > 2 sigma).
    """
    valid = ic_series.dropna()
    if len(valid) < 2:
        return {
            "positive_ratio": None,
            "quantiles": None,
            "extreme_ratio": None,
        }
    pos_ratio = float((valid > 0).sum() / len(valid))
    quantiles = {
        str(p): float(valid.quantile(q))
        for p, q in [("5%", 0.05), ("25%", 0.25), ("50%", 0.50),
                     ("75%", 0.75), ("95%", 0.95)]
    }
    std = float(valid.std(ddof=1))
    extreme_ratio = float((valid.abs() > 2 * std).sum() / len(valid)) if std > 1e-12 else 0.0
    return {
        "positive_ratio": pos_ratio,
        "quantiles": quantiles,
        "extreme_ratio": extreme_ratio,
    }


# ── IC decay ────────────────────────────────────────────────────────────────


def compute_ic_decay(
    ic_series: pd.Series,
    n_segments: int = 5,
) -> pd.DataFrame:
    """Compute ICIR per time segment to measure signal decay.

    Splits the IC series chronologically into ``n_segments`` equal-length
    segments and computes ICIR for each.  A declining ICIR across segments
    indicates the signal's predictive power fades over time.

    Parameters
    ----------
    ic_series:
        Daily IC series, index should be chronological (e.g. sorted by date).
    n_segments:
        Number of equal-length segments (default 5).

    Returns
    -------
    pd.DataFrame
        Columns: ``segment`` (1-indexed), ``n_days``, ``ic_mean``,
        ``ic_std``, ``icir``.
    """
    valid = ic_series.dropna().reset_index(drop=True)
    if len(valid) < 2:
        return pd.DataFrame(columns=["segment", "n_days", "ic_mean", "ic_std", "icir"])

    rows = []
    total = len(valid)
    for seg in range(n_segments):
        start = int(seg * total / n_segments)
        end = int((seg + 1) * total / n_segments)
        chunk = valid.iloc[start:end]
        stats = _ic_stats(chunk)
        rows.append({
            "segment": seg + 1,
            "n_days": len(chunk),
            "ic_mean": stats["mean"],
            "ic_std": stats["std"],
            "icir": stats["ir"],
        })
    return pd.DataFrame(rows)


# ── Regime-aware IC ─────────────────────────────────────────────────────────


def compute_regime_ic(
    ic_df: pd.DataFrame,
    index_code: str = "000300.SH",
    bull_threshold: float = 0.01,
    bear_threshold: float = -0.01,
) -> pd.DataFrame:
    """Compute IC per market regime (bull / neutral / bear).

    Loads the index daily data for regime classification, merges with
    daily IC, and aggregates IC statistics per regime.

    Parameters
    ----------
    ic_df:
        DataFrame with columns ``date`` and ``ic`` (from ``compute_daily_ic``).
    index_code:
        Tushare index code for regime classification (default 000300.SH).
    bull_threshold:
        Minimum index daily return to classify as bull (default 0.01 = 1%).
    bear_threshold:
        Maximum index daily return to classify as bear (default -0.01 = -1%).

    Returns
    -------
    pd.DataFrame
        Columns: ``regime``, ``n_days``, ``ic_mean``, ``ic_std``, ``icir``,
        ``positive_ratio``.
    """
    if ic_df.empty:
        return pd.DataFrame(columns=["regime", "n_days", "ic_mean", "ic_std", "icir", "positive_ratio"])

    try:
        from qsys.feature.groups.index_context import load_index_daily
        idx = load_index_daily(index_code)
    except Exception:
        return pd.DataFrame(columns=["regime", "n_days", "ic_mean", "ic_std", "icir", "positive_ratio"])

    # Daily return
    idx["return"] = idx["close"].pct_change()
    idx_map = dict(zip(idx["trade_date"].dt.strftime("%Y-%m-%d"), idx["return"]))

    # Merge regime with IC
    merged = ic_df.copy()
    merged["_td"] = merged["date"].astype(str).str[:10]
    merged["index_return"] = merged["_td"].map(idx_map)

    def _classify(r: float | None) -> str:
        if r is None:
            return "unknown"
        if r > bull_threshold:
            return "bull"
        if r < bear_threshold:
            return "bear"
        return "neutral"

    merged["regime"] = merged["index_return"].apply(_classify)

    rows = []
    for regime in ["bull", "neutral", "bear", "unknown"]:
        sub = merged[merged["regime"] == regime]["ic"]
        if sub.empty:
            continue
        stats = _ic_stats(sub)
        dist = _ic_distribution_stats(sub)
        rows.append({
            "regime": regime,
            "n_days": len(sub),
            "ic_mean": stats["mean"],
            "ic_std": stats["std"],
            "icir": stats["ir"],
            "positive_ratio": dist["positive_ratio"],
        })

    if not rows:
        return pd.DataFrame(columns=["regime", "n_days", "ic_mean", "ic_std", "icir", "positive_ratio"])
    return pd.DataFrame(rows)


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

    # ── IC distribution (new) ───────────────────────────────────────
    ic_positive_ratio: float | None = None
    ic_extreme_ratio: float | None = None

    # ── IC decay (new) ──────────────────────────────────────────────
    decay_icirs: list[float | None] | None = None

    # ── Regime IC (new) ─────────────────────────────────────────────
    regime_ic: dict[str, Any] | None = None

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

        # 6. IC distribution stats
        ic_dist = _ic_distribution_stats(ic_df["ic"])

        # 7. IC decay
        decay_df = compute_ic_decay(ic_df["ic"], n_segments=5)
        decay_icirs = [float(r["icir"]) if r["icir"] is not None else None
                       for _, r in decay_df.iterrows()] if not decay_df.empty else None

        # 8. Regime-aware IC
        regime_df = compute_regime_ic(ic_df)
        regime_ic: dict[str, Any] | None = None
        if not regime_df.empty:
            regime_ic = {}
            for _, r in regime_df.iterrows():
                regime_ic[str(r["regime"])] = {
                    "n_days": int(r["n_days"]),
                    "ic_mean": float(r["ic_mean"]) if r["ic_mean"] is not None else None,
                    "icir": float(r["icir"]) if r["icir"] is not None else None,
                    "positive_ratio": float(r["positive_ratio"]) if r["positive_ratio"] is not None else None,
                }

        # 9. Resolve output dir
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
            # IC distribution
            "ic_positive_ratio": ic_dist["positive_ratio"],
            "ic_quantiles": ic_dist["quantiles"],
            "ic_extreme_ratio": ic_dist["extreme_ratio"],
            # IC decay
            "decay_icirs": decay_icirs,
            # Regime-aware IC
            "regime_ic": regime_ic,
        }
        summary = with_standard_metadata(summary)
        write_manifest(output_dir / "summary.json", summary)

        _write_parquet_or_csv(ic_df, output_dir / "ic_daily.parquet")
        _write_parquet_or_csv(rank_ic_df, output_dir / "rank_ic_daily.parquet")
        _write_parquet_or_csv(grp_df, output_dir / "group_returns.parquet")
        _write_parquet_or_csv(cov_df, output_dir / "coverage.parquet")
        _write_parquet_or_csv(decay_df, output_dir / "decay.parquet")
        _write_parquet_or_csv(regime_df, output_dir / "regime_ic.parquet")

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
            ic_positive_ratio=ic_dist["positive_ratio"],
            ic_extreme_ratio=ic_dist["extreme_ratio"],
            decay_icirs=decay_icirs,
            regime_ic=regime_ic,
        )
