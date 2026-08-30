"""Signal evaluation — IC / RankIC / ICIR / Group Return computation.

Core functions are module-level for testability.
"""

from __future__ import annotations

import json
import hashlib
import math
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


def _finite_float(value: Any) -> float | None:
    """Return a JSON-safe finite float, or ``None``."""
    if value is None or not pd.notna(value):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


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
    mean = float(valid.mean())
    extreme_ratio = float((valid - mean).abs().gt(2 * std).sum() / len(valid)) if std > 1e-12 else 0.0
    return {
        "positive_ratio": pos_ratio,
        "quantiles": quantiles,
        "extreme_ratio": extreme_ratio,
    }


# ── IC decay ────────────────────────────────────────────────────────────────


def compute_ic_decay(
    signal: pd.DataFrame,
    labels: pd.DataFrame,
    *,
    score_column: str = "score",
    lags: tuple[int, ...] = (0, 1, 2, 3, 5, 10, 20),
    min_count: int = 5,
) -> pd.DataFrame:
    """Measure prediction decay by aligning one signal date to later labels.

    ``lag_sessions=L`` compares the score known on date ``T`` with the label
    whose own trade date is the ``L``-th later date in the label trading
    calendar.  This is a real forecast-lag test; it never substitutes
    chronological sample segments for decay.

    Parameters
    ----------
    signal, labels:
        Signal and label frames keyed by ``(trade_date, instrument)``.
    lags:
        Non-negative trading-session lags to evaluate.

    Returns
    -------
    pd.DataFrame
        Columns: ``lag_sessions``, ``n_obs``, ``n_days``, ``ic_mean``,
        ``ic_std``, ``icir``, ``rank_ic_mean``, and ``rank_icir``.
    """
    columns = [
        "lag_sessions", "n_obs", "n_days", "ic_mean", "ic_std", "icir",
        "rank_ic_mean", "rank_ic_std", "rank_icir",
    ]
    if signal.empty or labels.empty:
        return pd.DataFrame(columns=columns)
    if score_column not in signal.columns:
        raise ValueError(f"Score column {score_column!r} not found")
    calendar = sorted(labels["trade_date"].astype(str).str[:10].unique())
    if not calendar:
        return pd.DataFrame(columns=columns)
    positions = {date: idx for idx, date in enumerate(calendar)}
    label_values = labels[["trade_date", "instrument", "label_value"]].copy()
    label_values["trade_date"] = label_values["trade_date"].astype(str).str[:10]
    base = signal[["trade_date", "instrument", score_column]].copy()
    base["trade_date"] = base["trade_date"].astype(str).str[:10]

    rows: list[dict[str, Any]] = []
    for lag in sorted(set(lags)):
        if lag < 0:
            raise ValueError("IC decay lags must be non-negative")
        aligned = base.copy()
        aligned["label_trade_date"] = aligned["trade_date"].map(
            lambda date: (
                calendar[positions[date] + lag]
                if date in positions and positions[date] + lag < len(calendar)
                else None
            )
        )
        aligned = aligned.dropna(subset=["label_trade_date"])
        aligned = aligned.merge(
            label_values.rename(columns={"trade_date": "label_trade_date"}),
            on=["label_trade_date", "instrument"],
            how="inner",
        ).dropna(subset=[score_column, "label_value"])
        if aligned.empty:
            rows.append({"lag_sessions": lag, "n_obs": 0, "n_days": 0})
            continue
        daily = aligned.groupby("trade_date").apply(
            lambda group: pd.Series({
                "ic": group[score_column].corr(group["label_value"])
                if len(group) >= min_count else np.nan,
                "rank_ic": group[score_column].corr(
                    group["label_value"], method="spearman"
                ) if len(group) >= min_count else np.nan,
            }),
            include_groups=False,
        )
        ic_stats = _ic_stats(daily["ic"])
        rank_stats = _ic_stats(daily["rank_ic"])
        rows.append({
            "lag_sessions": lag,
            "n_obs": int(len(aligned)),
            "n_days": int(daily["ic"].notna().sum()),
            "ic_mean": ic_stats["mean"],
            "ic_std": ic_stats["std"],
            "icir": ic_stats["ir"],
            "rank_ic_mean": rank_stats["mean"],
            "rank_ic_std": rank_stats["std"],
            "rank_icir": rank_stats["ir"],
        })
    return pd.DataFrame(rows, columns=columns)


# ── Regime-aware IC ─────────────────────────────────────────────────────────


def compute_regime_ic(
    ic_df: pd.DataFrame,
    index_code: str = "000300.SH",
    trend_lookback_sessions: int = 60,
    uptrend_threshold: float = 0.05,
    downtrend_threshold: float = -0.05,
    information_lag_sessions: int = 1,
) -> pd.DataFrame:
    """Compute IC by a predefined, lagged market-trend regime.

    Regimes use only index closes available before the evaluated signal date:
    trailing ``trend_lookback_sessions`` return is shifted by
    ``information_lag_sessions``.  Same-day index returns are never used.

    Parameters
    ----------
    ic_df:
        DataFrame with columns ``date`` and ``ic`` (from ``compute_daily_ic``).
    index_code:
        Tushare index code for regime classification (default 000300.SH).
        Accepts ``000300.SH``, ``hs300``, or any key from ``INDEX_CODE_MAP``.
    trend_lookback_sessions:
        Trailing index-return lookback.
    uptrend_threshold, downtrend_threshold:
        Fixed thresholds for uptrend/range/downtrend classification.
    information_lag_sessions:
        Required lag before index information becomes eligible.

    Returns
    -------
    pd.DataFrame
        Columns: ``regime``, ``n_days``, ``ic_mean``, ``ic_std``, ``icir``,
        ``positive_ratio``.
    """
    if ic_df.empty:
        return pd.DataFrame(columns=["regime", "n_days", "ic_mean", "ic_std", "icir", "positive_ratio"])

    # Map Tushare code to index_context alias if needed
    _CODE_TO_ALIAS = {
        "000300.SH": "hs300",
        "000906.SH": "csi800",
        "000905.SH": "zz500",
        "000852.SH": "zz1000",
        "000001.SH": "sse",
        "000688.SH": "kc50",
        "399006.SZ": "cyb",
    }
    lookup_key = _CODE_TO_ALIAS.get(index_code, index_code)

    try:
        from qsys.feature.groups.index_context import load_index_daily
        idx = load_index_daily(lookup_key)
    except Exception:
        return pd.DataFrame(columns=["regime", "n_days", "ic_mean", "ic_std", "icir", "positive_ratio"])

    if trend_lookback_sessions <= 0 or information_lag_sessions <= 0:
        raise ValueError("regime lookback and information lag must be positive")
    idx = idx.sort_values("trade_date").copy()
    idx["lagged_trend_return"] = idx["close"].pct_change(
        trend_lookback_sessions
    ).shift(information_lag_sessions)
    idx_map = dict(zip(
        idx["trade_date"].dt.strftime("%Y-%m-%d"),
        idx["lagged_trend_return"],
    ))

    # Merge regime with IC
    merged = ic_df.copy()
    merged["_td"] = merged["date"].astype(str).str[:10]
    merged["lagged_trend_return"] = merged["_td"].map(idx_map)

    def _classify(r: float | None) -> str:
        if r is None or (isinstance(r, float) and pd.isna(r)):
            return "unknown"
        if r > uptrend_threshold:
            return "uptrend"
        if r < downtrend_threshold:
            return "downtrend"
        return "range"

    merged["regime"] = merged["lagged_trend_return"].apply(_classify)

    rows = []
    for regime in ["uptrend", "range", "downtrend", "unknown"]:
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


def summarize_group_returns(
    group_returns: pd.DataFrame,
    n_groups: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Return daily top-minus-bottom spreads and monotonicity evidence."""
    columns = ["trade_date", "top_minus_bottom", "monotonicity_score"]
    if group_returns.empty:
        return pd.DataFrame(columns=columns), {
            "n_dates": 0, "top_minus_bottom_mean": None,
            "monotonicity_mean": None,
        }
    rows: list[dict[str, Any]] = []
    for trade_date, day in group_returns.groupby("trade_date"):
        values = day.set_index("group_id")["mean_return"]
        if 1 not in values or n_groups not in values:
            continue
        ordered = [float(values.get(group, np.nan)) for group in range(1, n_groups + 1)]
        comparisons = [
            ordered[idx + 1] > ordered[idx]
            for idx in range(n_groups - 1)
            if np.isfinite(ordered[idx]) and np.isfinite(ordered[idx + 1])
        ]
        rows.append({
            "trade_date": trade_date,
            "top_minus_bottom": ordered[-1] - ordered[0],
            "monotonicity_score": (
                float(np.mean(comparisons)) if comparisons else None
            ),
        })
    daily = pd.DataFrame(rows, columns=columns)
    return daily, {
        "n_dates": int(len(daily)),
        "top_minus_bottom_mean": (
            _finite_float(daily["top_minus_bottom"].mean())
            if not daily.empty else None
        ),
        "monotonicity_mean": (
            _finite_float(daily["monotonicity_score"].mean())
            if not daily.empty else None
        ),
    }


def _circular_block_bootstrap_mean(
    values: pd.Series,
    *,
    block_length: int,
    reps: int = 2_000,
    seed: int = 42,
) -> dict[str, Any]:
    clean = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    if len(clean) == 0:
        return {
            "n": 0, "mean": None, "ci95": [None, None],
            "block_length": block_length, "reps": reps, "seed": seed,
        }
    block_length = max(1, min(int(block_length), len(clean)))
    if len(clean) == 1:
        value = float(clean[0])
        return {
            "n": 1, "mean": value, "ci95": [value, value],
            "block_length": block_length, "reps": reps, "seed": seed,
        }
    rng = np.random.default_rng(seed)
    blocks = math.ceil(len(clean) / block_length)
    starts = rng.integers(0, len(clean), size=(reps, blocks))
    offsets = np.arange(block_length)
    indices = (starts[:, :, None] + offsets) % len(clean)
    samples = clean[indices.reshape(reps, -1)[:, : len(clean)]]
    means = samples.mean(axis=1)
    return {
        "n": int(len(clean)),
        "mean": float(clean.mean()),
        "ci95": [float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))],
        "block_length": block_length,
        "reps": reps,
        "seed": seed,
    }


def _newey_west_mean_test(values: pd.Series, max_lag: int) -> dict[str, Any]:
    clean = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    n = len(clean)
    if n < 2:
        return {"n": n, "mean": float(clean[0]) if n else None, "max_lag": 0,
                "standard_error": None, "t_stat": None}
    max_lag = max(0, min(int(max_lag), n - 1))
    residual = clean - clean.mean()
    long_run_variance = float(np.dot(residual, residual) / n)
    for lag in range(1, max_lag + 1):
        covariance = float(np.dot(residual[lag:], residual[:-lag]) / n)
        weight = 1.0 - lag / (max_lag + 1.0)
        long_run_variance += 2.0 * weight * covariance
    standard_error = math.sqrt(max(long_run_variance, 0.0) / n)
    return {
        "n": n,
        "mean": float(clean.mean()),
        "max_lag": max_lag,
        "standard_error": standard_error,
        "t_stat": float(clean.mean() / standard_error) if standard_error > 0 else None,
    }


def compute_overlap_robustness(
    values: pd.Series,
    *,
    horizon: int,
) -> dict[str, Any]:
    """Report horizon-aware bootstrap, HAC, and every non-overlap offset."""
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    clean = pd.to_numeric(values, errors="coerce").dropna().reset_index(drop=True)
    offsets = []
    for offset in range(horizon):
        subset = clean.iloc[offset::horizon]
        offsets.append({
            "offset": offset,
            "n": int(len(subset)),
            "mean": float(subset.mean()) if len(subset) else None,
            "std": float(subset.std(ddof=1)) if len(subset) > 1 else None,
        })
    return {
        "contract": "overlapping_forward_label_robustness_v1",
        "horizon_sessions": horizon,
        "block_bootstrap": _circular_block_bootstrap_mean(
            clean, block_length=horizon
        ),
        "newey_west": _newey_west_mean_test(clean, max_lag=horizon - 1),
        "non_overlapping_offsets": offsets,
    }


def compute_topk_metrics(
    joined: pd.DataFrame,
    *,
    score_column: str = "score",
    top_ks: tuple[int, ...] = (5, 20, 50),
    random_reps: int = 200,
    seed: int = 42,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Compute fixed eligible-universe Top-K hit and economic capture metrics."""
    rng = np.random.default_rng(seed)
    benchmark_column = next(
        (
            column for column in (
                "benchmark_return", "benchmark_label_value",
                "benchmark_return_label", "benchmark_label_value_label",
            ) if column in joined.columns
        ),
        None,
    )
    rows: list[dict[str, Any]] = []
    for trade_date, day in joined.groupby("trade_date", sort=True):
        day = day.dropna(subset=[score_column, "label_value"]).copy()
        if day.empty:
            continue
        prediction_order = day.sort_values(
            [score_column, "instrument"], ascending=[False, True], kind="mergesort"
        )
        label_order = day.sort_values(
            ["label_value", "instrument"], ascending=[False, True], kind="mergesort"
        )
        universe_mean = float(day["label_value"].mean())
        for top_k in sorted(set(top_ks)):
            if top_k <= 0 or len(day) < top_k:
                continue
            predicted = prediction_order.head(top_k)
            actual = label_order.head(top_k)
            predicted_set = set(predicted["instrument"])
            actual_set = set(actual["instrument"])
            predicted_mean = float(predicted["label_value"].mean())
            random_means = np.asarray([
                float(day["label_value"].iloc[
                    rng.choice(len(day), size=top_k, replace=False)
                ].mean())
                for _ in range(random_reps)
            ])
            benchmark = (
                _finite_float(
                    pd.to_numeric(day[benchmark_column], errors="coerce").mean()
                )
                if benchmark_column else None
            )
            rows.append({
                "trade_date": trade_date,
                "top_k": top_k,
                "n_eligible": int(len(day)),
                "hit_recall_at_k": len(predicted_set & actual_set) / top_k,
                "predicted_topk_return": predicted_mean,
                "universe_return": universe_mean,
                "excess_vs_universe": predicted_mean - universe_mean,
                "benchmark_return": benchmark,
                "excess_vs_benchmark": (
                    predicted_mean - benchmark if benchmark is not None else None
                ),
                "random_topk_mean": float(random_means.mean()),
                "random_topk_std": float(random_means.std(ddof=1)),
                "excess_vs_random": predicted_mean - float(random_means.mean()),
            })
    per_date = pd.DataFrame(rows)
    summary: dict[str, Any] = {
        "contract": "eligible_executable_label_topk_v1",
        "top_ks": list(sorted(set(top_ks))),
        "random_reps": random_reps,
        "random_seed": seed,
        "benchmark_column": benchmark_column,
        "by_k": {},
        "yearly": {},
    }
    if per_date.empty:
        return per_date, summary
    metrics = [
        "hit_recall_at_k", "predicted_topk_return", "excess_vs_universe",
        "excess_vs_benchmark", "excess_vs_random",
    ]
    per_date["year"] = pd.to_datetime(per_date["trade_date"]).dt.year
    for top_k, group in per_date.groupby("top_k"):
        summary["by_k"][str(int(top_k))] = {
            "n_dates": int(len(group)),
            **{
                metric: (
                    float(pd.to_numeric(group[metric], errors="coerce").mean())
                    if pd.to_numeric(group[metric], errors="coerce").notna().any()
                    else None
                )
                for metric in metrics
            },
        }
        summary["yearly"][str(int(top_k))] = {
            str(int(year)): {
                metric: (
                    float(pd.to_numeric(year_group[metric], errors="coerce").mean())
                    if pd.to_numeric(year_group[metric], errors="coerce").notna().any()
                    else None
                )
                for metric in metrics
            }
            for year, year_group in group.groupby("year")
        }
    return per_date.drop(columns="year"), summary


def compute_rank_stability(
    joined: pd.DataFrame,
    *,
    score_column: str = "score",
    top_ks: tuple[int, ...] = (5, 20, 50),
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Compute consecutive-date rank autocorrelation and Top-K churn."""
    days = {
        date: frame[["instrument", score_column]].dropna().copy()
        for date, frame in joined.groupby("trade_date", sort=True)
    }
    ordered_dates = sorted(days)
    rows: list[dict[str, Any]] = []
    for previous_date, trade_date in zip(ordered_dates, ordered_dates[1:]):
        previous = days[previous_date]
        current = days[trade_date]
        overlap = previous.merge(current, on="instrument", suffixes=("_prev", "_curr"))
        autocorrelation = (
            overlap[f"{score_column}_prev"].corr(
                overlap[f"{score_column}_curr"], method="spearman"
            ) if len(overlap) >= 2 else None
        )
        for top_k in sorted(set(top_ks)):
            if len(previous) < top_k or len(current) < top_k:
                continue
            previous_top = set(previous.nlargest(top_k, score_column)["instrument"])
            current_top = set(current.nlargest(top_k, score_column)["instrument"])
            intersection = len(previous_top & current_top)
            union = len(previous_top | current_top)
            rows.append({
                "previous_date": previous_date,
                "trade_date": trade_date,
                "top_k": top_k,
                "rank_autocorrelation": (
                    float(autocorrelation) if pd.notna(autocorrelation) else None
                ),
                "topk_jaccard": intersection / union if union else None,
                "ranking_turnover": 1.0 - intersection / top_k,
            })
    per_date = pd.DataFrame(rows)
    summary = {"by_k": {}}
    if not per_date.empty:
        for top_k, group in per_date.groupby("top_k"):
            summary["by_k"][str(int(top_k))] = {
                "n_transitions": int(len(group)),
                "rank_autocorrelation": _finite_float(
                    group["rank_autocorrelation"].mean()
                ),
                "topk_jaccard": _finite_float(group["topk_jaccard"].mean()),
                "ranking_turnover": _finite_float(
                    group["ranking_turnover"].mean()
                ),
            }
    return per_date, summary


def compute_neutralized_rank_ic(
    joined: pd.DataFrame,
    *,
    score_column: str = "score",
    min_count: int = 5,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Compute industry- and size-neutral RankIC when support fields exist."""
    industry_field = next(
        (
            column for column in (
                "industry", "industry_code", "sw_l1",
                "industry_signal", "industry_label",
            ) if column in joined.columns
        ),
        None,
    )
    size_field = next(
        (
            column for column in (
                "circ_mv", "total_mv", "circ_mv_signal", "total_mv_signal",
                "circ_mv_label", "total_mv_label",
            ) if column in joined.columns
        ),
        None,
    )
    rows: list[dict[str, Any]] = []
    for trade_date, day in joined.groupby("trade_date", sort=True):
        base = day.dropna(subset=[score_column, "label_value"]).copy()
        if industry_field:
            industry = base.dropna(subset=[industry_field]).copy()
            industry["neutral_score"] = industry[score_column] - industry.groupby(
                industry_field
            )[score_column].transform("mean")
            value = (
                industry["neutral_score"].corr(
                    industry["label_value"], method="spearman"
                ) if len(industry) >= min_count else None
            )
            rows.append({
                "trade_date": trade_date,
                "method": "industry_neutral",
                "rank_ic": float(value) if pd.notna(value) else None,
                "n": int(len(industry)),
            })
        if size_field:
            size = base.dropna(subset=[size_field]).copy()
            size["_size"] = pd.to_numeric(size[size_field], errors="coerce")
            size = size[size["_size"] > 0]
            if len(size) >= min_count and size["_size"].nunique() > 1:
                x = np.log(size["_size"].to_numpy(dtype=float))
                y = size[score_column].to_numpy(dtype=float)
                design = np.column_stack([np.ones(len(x)), x])
                coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
                size["neutral_score"] = y - design @ coefficients
                value = size["neutral_score"].corr(
                    size["label_value"], method="spearman"
                )
            else:
                value = None
            rows.append({
                "trade_date": trade_date,
                "method": "size_neutral",
                "rank_ic": float(value) if pd.notna(value) else None,
                "n": int(len(size)),
            })
    daily = pd.DataFrame(rows)
    summary: dict[str, Any] = {
        "industry_field": industry_field,
        "size_field": size_field,
        "status": "available" if industry_field or size_field else "unavailable",
        "methods": {},
    }
    if not daily.empty:
        for method, group in daily.groupby("method"):
            stats = _ic_stats(group["rank_ic"])
            summary["methods"][str(method)] = {
                "n_days": int(group["rank_ic"].notna().sum()),
                "rank_ic_mean": stats["mean"],
                "rank_ic_std": stats["std"],
                "rank_icir": stats["ir"],
            }
    return daily, summary


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


# ── Binary classification: daily AUC ────────────────────────────────────────


def compute_daily_auc(
    joined: pd.DataFrame,
    score_column: str = "score",
    min_count: int = 5,
) -> pd.DataFrame:
    """Compute daily AUC for binary classification.

    Rows where ``label_value`` is NaN are excluded.  At least one sample
    of each class (0/1) is required per date.
    All columns are cast to float via ``.astype({{str}})`` to resolve dtype
    mismatches that can arise from the lightgbm predict path.

    Returns a DataFrame with columns ``date``, ``auc``, ``n``, ``n_pos``, ``n_neg``.
    """
    from sklearn.metrics import roc_auc_score

    dates = joined["trade_date"].unique()
    rows = []
    for d in sorted(dates):
        day = joined[joined["trade_date"] == d].dropna(subset=[score_column, "label_value"])
        if len(day) < min_count:
            rows.append({"date": d, "auc": None, "n": len(day), "n_pos": 0, "n_neg": 0})
            continue
        # Cast to float to resolve dtype mismatches from lightgbm predict
        y_true = day["label_value"].astype(float).astype(int)
        y_score = day[score_column].astype(float)
        n_pos = int((y_true == 1).sum())
        n_neg = int((y_true == 0).sum())
        if n_pos < 1 or n_neg < 1:
            rows.append({"date": d, "auc": None, "n": len(day), "n_pos": n_pos, "n_neg": n_neg})
            continue
        try:
            auc = float(roc_auc_score(y_true, y_score))
        except Exception:
            auc = None
        rows.append({"date": d, "auc": auc, "n": len(day), "n_pos": n_pos, "n_neg": n_neg})
    return pd.DataFrame(rows)


def _auc_stats(auc_series: pd.Series) -> dict[str, float | None]:
    """Compute AUC mean, std, min, max from a daily AUC series."""
    valid = auc_series.dropna()
    if len(valid) < 2:
        return {"mean": None, "std": None, "min": None, "max": None}
    return {
        "mean": float(valid.mean()),
        "std": float(valid.std(ddof=1)),
        "min": float(valid.min()),
        "max": float(valid.max()),
    }


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

    # ── Binary AUC (new) ────────────────────────────────────────────
    auc_mean: float | None = None
    auc_std: float | None = None
    auc_min: float | None = None
    auc_max: float | None = None

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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _frame_sha256(frame: pd.DataFrame) -> str:
    """Hash an in-memory fixture deterministically for legacy test pipelines."""
    ordered = frame.sort_values(
        [column for column in ("trade_date", "instrument") if column in frame.columns]
    ).reset_index(drop=True)
    payload = ordered.to_csv(index=False, lineterminator="\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _existing_table_path(path: Path) -> Path:
    parquet = path.with_suffix(".parquet")
    if parquet.is_file():
        return parquet
    csv = path.with_suffix(".csv")
    if csv.is_file():
        return csv
    raise FileNotFoundError(f"evaluation table was not written: {path}")


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
        require_pit_lineage: bool = False,
        research_config_sha256: str | None = None,
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
        signal_manifest_path = self._paths.signal_manifest(signal_id, signal_run_id)
        label_manifest_path = self._paths.label_manifest(label_id)
        signal_manifest = json.loads(signal_manifest_path.read_text(encoding="utf-8"))
        label_manifest = (
            json.loads(label_manifest_path.read_text(encoding="utf-8"))
            if label_manifest_path.is_file() else {}
        )
        signal_data_path = next(
            path for path in (
                self._paths.signal_file(signal_id, signal_run_id, "parquet"),
                self._paths.signal_file(signal_id, signal_run_id, "csv"),
            ) if path.is_file()
        )
        label_data_path = next(
            (
                path for path in (
                    self._paths.label_file(label_id, "parquet"),
                    self._paths.label_file(label_id, "csv"),
                ) if path.is_file()
            ),
            None,
        )
        signal_data_sha256 = _sha256_file(signal_data_path)
        label_data_sha256 = (
            _sha256_file(label_data_path)
            if label_data_path is not None else _frame_sha256(labels)
        )
        if signal_manifest.get("predictions_sha256") != signal_data_sha256:
            raise ValueError("Signal data hash does not match its manifest")
        if label_manifest and label_manifest.get("labels_sha256") != label_data_sha256:
            raise ValueError("Label data hash does not match its manifest")
        if require_pit_lineage and (not label_manifest or label_data_path is None):
            raise ValueError("Formal evaluation requires a materialized label artifact")
        pit_fields = (
            "pit_universe_artifact", "universe_manifest_sha256",
            "universe_membership_sha256",
        )
        missing_pit = [field for field in pit_fields if not label_manifest.get(field)]
        if require_pit_lineage and missing_pit:
            raise ValueError(
                "Formal evaluation requires PIT label lineage fields: "
                + ", ".join(missing_pit)
            )
        if require_pit_lineage and not research_config_sha256:
            raise ValueError("Formal evaluation requires research config identity")
        if "horizon" not in labels.columns:
            if require_pit_lineage:
                raise ValueError("Formal evaluation requires label horizon metadata")
            horizon = 1
        else:
            horizons = pd.to_numeric(
                labels["horizon"], errors="coerce"
            ).dropna().unique()
            if len(horizons) != 1 or int(horizons[0]) <= 0:
                raise ValueError("Evaluation requires exactly one positive label horizon")
            horizon = int(horizons[0])

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
        quantile_daily, quantile_summary = summarize_group_returns(
            grp_df, n_groups
        )
        decile_df = compute_group_returns(joined, score_column, 10)
        decile_daily, decile_summary = summarize_group_returns(decile_df, 10)

        # 4b. Fixed Top-K evidence and ranking stability
        topk_df, topk_summary = compute_topk_metrics(
            joined, score_column=score_column
        )
        stability_df, stability_summary = compute_rank_stability(
            joined, score_column=score_column
        )
        neutral_df, neutral_summary = compute_neutralized_rank_ic(
            joined, score_column=score_column, min_count=min_count
        )

        # 5. Coverage
        cov_df = compute_coverage(signal, joined)
        cov_mean = float(cov_df["coverage"].mean()) if "coverage" in cov_df.columns and len(cov_df) > 0 else None

        # 6. IC distribution stats
        ic_dist = _ic_distribution_stats(ic_df["ic"])

        # 7. True forecast-lag IC decay
        decay_lags = tuple(sorted({
            0, 1, 2, 3, min(5, horizon), min(10, horizon),
            min(20, horizon), horizon,
        }))
        decay_df = compute_ic_decay(
            signal,
            labels,
            score_column=score_column,
            lags=decay_lags,
            min_count=min_count,
        )
        decay_icirs = [float(r["icir"]) if pd.notna(r["icir"]) else None
                       for _, r in decay_df.iterrows()] if not decay_df.empty else None

        # 7b. Overlap-aware inference for all reasonable offsets
        overlap_stats = {
            "daily_ic": compute_overlap_robustness(
                ic_df["ic"], horizon=horizon
            ),
            "daily_rank_ic": compute_overlap_robustness(
                rank_ic_df["rank_ic"], horizon=horizon
            ),
            "decile_top_minus_bottom": compute_overlap_robustness(
                decile_daily["top_minus_bottom"]
                if not decile_daily.empty else pd.Series(dtype=float),
                horizon=horizon,
            ),
        }

        # 8. Regime-aware IC
        regime_df = compute_regime_ic(ic_df)
        regime_ic: dict[str, Any] | None = None
        if not regime_df.empty:
            regime_ic = {}
            for _, r in regime_df.iterrows():
                regime_ic[str(r["regime"])] = {
                    "n_days": int(r["n_days"]),
                    "ic_mean": _finite_float(r["ic_mean"]),
                    "icir": _finite_float(r["icir"]),
                    "positive_ratio": _finite_float(r["positive_ratio"]),
                }

        # 8b. AUC — only meaningful for binary labels {0, 1}
        unique_labels = set(joined["label_value"].dropna().unique())
        is_binary = unique_labels.issubset({0, 1, 0.0, 1.0}) and len(unique_labels) == 2
        if is_binary:
            auc_df = compute_daily_auc(joined, score_column, min_count)
            auc_stats = _auc_stats(auc_df["auc"])
        else:
            auc_df = pd.DataFrame(columns=["date", "auc", "n", "n_pos", "n_neg"])
            auc_stats = {"mean": None, "std": None, "min": None, "max": None}

        # 8c. Resolve output dir
        if output_dir is None:
            output_dir = self._paths.signal_eval_dir(signal_id, signal_run_id, label_id)

        # 7. Write artifacts
        if output_dir.exists() and not overwrite:
            raise FileExistsError(
                f"Eval output dir already exists: {output_dir} (use overwrite=True)"
            )

        output_dir.mkdir(parents=True, exist_ok=True)

        methodology = {
            "contract": "signal_evaluation_methodology_v2",
            "score_column": score_column,
            "min_count": min_count,
            "quantile_groups": n_groups,
            "decile_groups": 10,
            "top_ks": [5, 20, 50],
            "label_horizon_sessions": horizon,
            "research_config_sha256": research_config_sha256,
            "ic_decay_lags": list(decay_lags),
            "overlap_inference": (
                "horizon_block_bootstrap_newey_west_all_offsets_v1"
            ),
            "regime": {
                "contract": "lagged_index_trend_regime_v1",
                "index_code": "000300.SH",
                "trend_lookback_sessions": 60,
                "information_lag_sessions": 1,
                "uptrend_threshold": 0.05,
                "downtrend_threshold": -0.05,
            },
            "start_date": start_date,
            "end_date": end_date,
        }
        methodology_bytes = json.dumps(
            methodology, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        methodology_sha256 = hashlib.sha256(methodology_bytes).hexdigest()
        code_sha256 = _sha256_file(Path(__file__))
        input_lineage = {
            "signal": {
                "signal_id": signal_id,
                "signal_run_id": signal_run_id,
                "data_path": str(signal_data_path),
                "data_sha256": signal_data_sha256,
                "manifest_path": str(signal_manifest_path),
                "manifest_sha256": _sha256_file(signal_manifest_path),
            },
            "label": {
                "label_id": label_id,
                "data_path": str(label_data_path) if label_data_path else None,
                "data_sha256": label_data_sha256,
                "manifest_path": (
                    str(label_manifest_path) if label_manifest_path.is_file() else None
                ),
                "manifest_sha256": (
                    _sha256_file(label_manifest_path)
                    if label_manifest_path.is_file() else None
                ),
                "lineage_status": (
                    "materialized" if label_data_path is not None
                    else "in_memory_legacy_fixture"
                ),
                "pit_universe_artifact": label_manifest.get(
                    "pit_universe_artifact"
                ),
                "universe_manifest_sha256": label_manifest.get(
                    "universe_manifest_sha256"
                ),
                "universe_membership_sha256": label_manifest.get(
                    "universe_membership_sha256"
                ),
            },
        }
        evaluation_identity_payload = {
            "inputs": input_lineage,
            "methodology_sha256": methodology_sha256,
            "evaluation_code_sha256": code_sha256,
        }
        evaluation_identity_sha256 = hashlib.sha256(
            json.dumps(
                evaluation_identity_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        summary = {
            "evaluation_identity_sha256": evaluation_identity_sha256,
            "methodology_sha256": methodology_sha256,
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
            "quantile_summary": quantile_summary,
            "decile_summary": decile_summary,
            "topk_summary": topk_summary,
            "ranking_stability": stability_summary,
            "neutralized_rank_ic": neutral_summary,
            "overlap_robustness": overlap_stats,
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
            # Binary AUC
            "auc_mean": auc_stats["mean"],
            "auc_std": auc_stats["std"],
            "auc_min": auc_stats["min"],
            "auc_max": auc_stats["max"],
        }
        summary = _json_safe(with_standard_metadata(summary))
        write_manifest(output_dir / "summary.json", summary)

        _write_parquet_or_csv(ic_df, output_dir / "ic_daily.parquet")
        _write_parquet_or_csv(rank_ic_df, output_dir / "rank_ic_daily.parquet")
        _write_parquet_or_csv(grp_df, output_dir / "group_returns.parquet")
        _write_parquet_or_csv(quantile_daily, output_dir / "quantile_daily.parquet")
        _write_parquet_or_csv(decile_df, output_dir / "decile_returns.parquet")
        _write_parquet_or_csv(decile_daily, output_dir / "decile_daily.parquet")
        _write_parquet_or_csv(topk_df, output_dir / "topk_daily.parquet")
        _write_parquet_or_csv(stability_df, output_dir / "ranking_stability.parquet")
        _write_parquet_or_csv(neutral_df, output_dir / "neutralized_rank_ic.parquet")
        _write_parquet_or_csv(cov_df, output_dir / "coverage.parquet")
        _write_parquet_or_csv(decay_df, output_dir / "decay.parquet")
        _write_parquet_or_csv(regime_df, output_dir / "regime_ic.parquet")
        if is_binary and not auc_df.empty:
            _write_parquet_or_csv(auc_df, output_dir / "auc_daily.parquet")

        output_tables = {}
        for artifact_name in (
            "ic_daily", "rank_ic_daily", "group_returns", "quantile_daily",
            "decile_returns", "decile_daily", "topk_daily",
            "ranking_stability", "neutralized_rank_ic", "coverage", "decay",
            "regime_ic",
        ):
            artifact_path = _existing_table_path(
                output_dir / f"{artifact_name}.parquet"
            )
            output_tables[artifact_name] = {
                "path": artifact_path.name,
                "sha256": _sha256_file(artifact_path),
                "row_count": int({
                    "ic_daily": len(ic_df),
                    "rank_ic_daily": len(rank_ic_df),
                    "group_returns": len(grp_df),
                    "quantile_daily": len(quantile_daily),
                    "decile_returns": len(decile_df),
                    "decile_daily": len(decile_daily),
                    "topk_daily": len(topk_df),
                    "ranking_stability": len(stability_df),
                    "neutralized_rank_ic": len(neutral_df),
                    "coverage": len(cov_df),
                    "decay": len(decay_df),
                    "regime_ic": len(regime_df),
                }[artifact_name]),
            }
        if is_binary and not auc_df.empty:
            artifact_path = _existing_table_path(output_dir / "auc_daily.parquet")
            output_tables["auc_daily"] = {
                "path": artifact_path.name,
                "sha256": _sha256_file(artifact_path),
                "row_count": int(len(auc_df)),
            }

        manifest = {
            "artifact_type": "signal_evaluation",
            "schema_version": 2,
            "evaluation_identity_sha256": evaluation_identity_sha256,
            "inputs": input_lineage,
            "methodology": methodology,
            "methodology_sha256": methodology_sha256,
            "evaluation_code_sha256": code_sha256,
            "outputs": {
                **output_tables,
                "summary": {
                    "path": "summary.json",
                    "sha256": _sha256_file(output_dir / "summary.json"),
                },
            },
        }
        write_manifest(
            output_dir / "manifest.json",
            _json_safe(with_standard_metadata(manifest)),
        )

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
            auc_mean=auc_stats["mean"],
            auc_std=auc_stats["std"],
            auc_min=auc_stats["min"],
            auc_max=auc_stats["max"],
        )
