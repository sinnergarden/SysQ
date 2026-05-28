"""Backtest metrics computation — equity curve + portfolio contribution analysis.

Usage
-----
    from qsys.analysis.backtest_metrics import compute_backtest_metrics
    import pandas as pd

    daily = pd.read_csv("experiments/backtest/alpha_v1/daily_summary.csv")
    metrics = compute_backtest_metrics(daily)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


# ── Helpers ────────────────────────────────────────────────────────────────


def _annual_factor(daily_count: int) -> float:
    """Annualisation factor given *daily_count* trading days of data."""
    return 252.0 / max(daily_count, 1)


def _to_equity_series(daily_summary: pd.DataFrame) -> pd.Series:
    """Extract the equity curve from a BacktestRunner daily_summary.

    Uses ``total_value_after`` when available, falling back to
    ``total_value_before``.
    """
    if "total_value_after" in daily_summary.columns:
        equity = daily_summary["total_value_after"].astype(float)
    elif "total_value_before" in daily_summary.columns:
        equity = daily_summary["total_value_before"].astype(float)
    else:
        raise ValueError("daily_summary has neither total_value_after nor total_value_before")
    return equity


def _daily_returns(equity: pd.Series) -> pd.Series:
    """Daily log returns from an equity curve.

    Uses simple returns (pct_change).  First NaN is dropped.
    """
    return equity.pct_change().dropna()


# ── Core equity-curve metrics ──────────────────────────────────────────────


def total_return(equity: pd.Series) -> float:
    """Total return over the full period."""
    if len(equity) < 2:
        return 0.0
    return float(equity.iloc[-1] / equity.iloc[0] - 1.0)


def annual_return(equity: pd.Series) -> float:
    """Annualised return (252-day compounding)."""
    if len(equity) < 2:
        return 0.0
    tr = total_return(equity)
    n = len(equity) - 1  # trading days
    return (1.0 + tr) ** _annual_factor(n) - 1.0


def annual_volatility(returns: pd.Series) -> float:
    """Annualised volatility."""
    if len(returns) < 2:
        return 0.0
    return float(returns.std() * np.sqrt(252.0))


def sharpe(returns: pd.Series) -> float:
    """Sharpe ratio (risk-free = 0)."""
    if len(returns) < 2:
        return 0.0
    std = float(returns.std())
    if std < 1e-12:
        return 0.0
    return float(returns.mean() / std * np.sqrt(252.0))


def max_drawdown(equity: pd.Series) -> float:
    """Maximum drawdown as a negative decimal (e.g. -0.25 for -25%)."""
    if len(equity) < 2:
        return 0.0
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(dd.min())


def calmar(ann_ret: float, mdd: float) -> float:
    """Calmar ratio = annual_return / abs(max_drawdown)."""
    if abs(mdd) < 1e-12:
        return 0.0
    return ann_ret / abs(mdd)


def win_rate(returns: pd.Series) -> float:
    """Fraction of days with positive return."""
    if len(returns) < 1:
        return 0.0
    return float((returns > 0).sum() / len(returns))


def annual_turnover(daily_summary: pd.DataFrame) -> float:
    """Annualised turnover ratio.

    Turnover is sum of filled_amount × deal_price over the period,
    divided by average total value and annualised.
    """
    if "turnover" not in daily_summary.columns:
        return 0.0
    turnover = daily_summary["turnover"].astype(float)
    total_t = turnover.sum()
    if total_t < 1e-6:
        return 0.0
    equity = _to_equity_series(daily_summary)
    avg_value = float(equity.mean())
    if avg_value < 1e-6:
        return 0.0
    n = len(daily_summary)
    return total_t / avg_value * _annual_factor(n)


def avg_holding_days(ann_to: float) -> float:
    """Approximate average holding period from annualised turnover.

    holding_days ≈ 252 / annual_turnover
    """
    if ann_to < 0.01:
        return float("inf")
    return 252.0 / ann_to


def cost_after_return(
    daily_summary: pd.DataFrame,
    *,
    commission_rate: float = 0.0003,
    stamp_duty_rate: float = 0.001,
    estimated_slippage: float = 0.001,
) -> float:
    """Estimated total cost as fraction of total return.

    Returns the cost drag: total costs / initial capital.
    This is a rough estimate using parametric rates when the
    BacktestRunner runs with zero-cost matching.
    """
    if "turnover" not in daily_summary.columns:
        return 0.0
    total_t = daily_summary["turnover"].astype(float).sum()
    if total_t < 1e-6:
        return 0.0
    # Round-trip cost: buy commission + sell commission + stamp_duty + 2×slippage
    round_trip_rate = 2 * commission_rate + stamp_duty_rate + 2 * estimated_slippage
    equity = _to_equity_series(daily_summary)
    init_capital = float(equity.iloc[0])
    if init_capital < 1e-6:
        return 0.0
    cost_drag = total_t * round_trip_rate / init_capital
    return cost_drag


def worst_n_days(returns: pd.Series, n: int = 5) -> list[dict[str, Any]]:
    """Worst *n* single-day returns with dates."""
    if returns.empty:
        return []
    worst = returns.sort_values().head(n)
    return [
        {"date": str(idx), "return": float(val)}
        for idx, val in worst.items()
    ]


def worst_n_drawdowns(equity: pd.Series, n: int = 5) -> list[dict[str, Any]]:
    """Worst *n* distinct drawdown periods by depth.

    Each drawdown is a period from peak to trough to recovery.
    Returns periods sorted by depth ascending (worst first).
    """
    if len(equity) < 2:
        return []

    peak = equity.iloc[0]
    peak_idx = 0
    in_dd = False
    dd_start = 0
    periods: list[dict[str, Any]] = []

    for i in range(1, len(equity)):
        if equity.iloc[i] >= peak:
            if in_dd:
                # Recovery — record the drawdown
                dd_depth = float(equity.iloc[dd_start : i + 1].min() / peak - 1.0)
                trough_idx = int(equity.iloc[dd_start : i + 1].idxmin() if hasattr(equity.iloc[dd_start : i + 1], 'idxmin') else dd_start + equity.iloc[dd_start : i + 1].values.argmin())
                periods.append({
                    "start_date": str(equity.index[dd_start]),
                    "trough_date": str(equity.index[trough_idx]),
                    "end_date": str(equity.index[i]),
                    "depth": dd_depth,
                    "duration_days": i - dd_start,
                })
                in_dd = False
            peak = equity.iloc[i]
            peak_idx = i
        else:
            if not in_dd:
                in_dd = True
                dd_start = peak_idx

    # Handle unfinished drawdown at end
    if in_dd:
        dd_depth = float(equity.iloc[dd_start:].min() / peak - 1.0)
        trough_idx = int(equity.iloc[dd_start:].idxmin() if hasattr(equity.iloc[dd_start:], 'idxmin') else dd_start + equity.iloc[dd_start:].values.argmin())
        periods.append({
            "start_date": str(equity.index[dd_start]),
            "trough_date": str(equity.index[trough_idx]),
            "end_date": None,
            "depth": dd_depth,
            "duration_days": len(equity) - dd_start,
        })

    periods.sort(key=lambda p: p["depth"])
    return periods[:n]


def excess_return(
    equity: pd.Series,
    benchmark_returns: pd.Series,
) -> dict[str, float]:
    """Annualised excess return vs a benchmark.

    Parameters
    ----------
    equity : pd.Series
        Strategy equity curve (index = date labels).
    benchmark_returns : pd.Series
        Benchmark daily returns (index = date labels).

    Returns
    -------
    dict with keys ``excess_return`` and ``excess_sharpe``.
    """
    rets = _daily_returns(equity)
    aligned = pd.concat([rets, benchmark_returns], axis=1, join="inner").dropna()
    if len(aligned) < 5:
        return {"excess_return": 0.0, "excess_sharpe": 0.0}

    ex = aligned.iloc[:, 0] - aligned.iloc[:, 1]
    n = len(ex)
    ex_ann = (1.0 + float(ex.mean())) ** (252.0 / n) - 1.0
    ex_std = float(ex.std())
    ex_sp = (
        float(ex.mean() / ex_std * np.sqrt(252.0))
        if ex_std > 1e-12
        else 0.0
    )
    return {"excess_return": ex_ann, "excess_sharpe": ex_sp}


# ── Portfolio contribution analysis ────────────────────────────────────────


def _load_debug_predictions(artifacts_dir: Path) -> dict[str, pd.DataFrame]:
    """Load predictions.csv per day from debug artifacts."""
    daily_dirs = sorted(artifacts_dir.iterdir()) if artifacts_dir.exists() else []
    preds: dict[str, pd.DataFrame] = {}
    for d in daily_dirs:
        if not d.is_dir():
            continue
        pred_file = d / "predictions.csv"
        if pred_file.exists():
            df = pd.read_csv(pred_file)
            if not df.empty:
                preds[d.name] = df
    return preds


def _load_debug_target_weights(artifacts_dir: Path) -> dict[str, pd.DataFrame]:
    """Load plan/target_weights.csv per day from debug artifacts."""
    daily_dirs = sorted(artifacts_dir.iterdir()) if artifacts_dir.exists() else []
    tw: dict[str, pd.DataFrame] = {}
    for d in daily_dirs:
        if not d.is_dir():
            continue
        tw_file = d / "plan" / "target_weights.csv"
        if tw_file.exists():
            df = pd.read_csv(tw_file)
            if not df.empty:
                tw[d.name] = df
    return tw


def bucket_contribution(
    predictions_per_day: dict[str, pd.DataFrame],
    target_weights_per_day: dict[str, pd.DataFrame],
    price_data: pd.DataFrame,
    *,
    top_n: int = 20,
    buffer_hold: int = 60,
) -> dict[str, float]:
    """Compute return contribution of rank-based buckets.

    Parameters
    ----------
    predictions_per_day : dict of {trade_date: predictions_df}
        Each DataFrame has columns ``instrument``, ``score``.
    target_weights_per_day : dict of {trade_date: target_weights_df}
        Each DataFrame has columns ``instrument``, ``target_weight``, ``rank``.
    price_data : pd.DataFrame
        Instrument daily prices with columns ``trade_date``, ``instrument``,
        ``$close``.  Used to compute forward returns between rebalance dates.
    top_n : int
        Top-N threshold for the "top" bucket.
    buffer_hold : int
        Buffer threshold (ranks above top_n but ≤ buffer_hold).

    Returns
    -------
    dict with keys ``top_contribution``, ``buffer_contribution``,
    ``other_contribution``.
    """
    if not target_weights_per_day:
        return {}

    # Build rebalance date list from target_weights keys
    rb_dates = sorted(target_weights_per_day.keys())
    if len(rb_dates) < 2:
        return {}

    contributions: dict[str, list[float]] = {"top": [], "buffer": [], "other": []}
    prices = price_data.set_index(["trade_date", "instrument"])["$close"]

    for i in range(len(rb_dates) - 1):
        start_date = rb_dates[i]
        end_date = rb_dates[i + 1]
        tw = target_weights_per_day[start_date]

        # Classify each instrument into a bucket
        bucketed: dict[str, list[tuple[str, float]]] = {"top": [], "buffer": [], "other": []}
        for _, row in tw.iterrows():
            inst = str(row["instrument"])
            rank = int(row.get("rank", 999))
            weight = float(row.get("target_weight", 0.0))
            if weight <= 0:
                continue
            if rank <= top_n:
                bucketed["top"].append((inst, weight))
            elif rank <= buffer_hold:
                bucketed["buffer"].append((inst, weight))
            else:
                bucketed["other"].append((inst, weight))

        # Compute forward return for each bucket
        for bucket_name, items in bucketed.items():
            if not items:
                continue
            weighted_ret = 0.0
            total_w = sum(w for _, w in items)
            if total_w <= 0:
                continue
            for inst, w in items:
                try:
                    p_start = prices.loc[(start_date, inst)]
                    p_end = prices.loc[(end_date, inst)]
                    ret = float(p_end / p_start - 1.0) if p_start > 0 else 0.0
                except (KeyError, ValueError):
                    ret = 0.0
                weighted_ret += ret * (w / total_w)
            contributions[bucket_name].append(weighted_ret)

    result = {}
    for bucket_name, rets in contributions.items():
        if rets:
            result[f"{bucket_name}_contribution"] = float(np.mean(rets))
            result[f"{bucket_name}_std"] = float(np.std(rets)) if len(rets) > 1 else 0.0
        else:
            result[f"{bucket_name}_contribution"] = 0.0
            result[f"{bucket_name}_std"] = 0.0
    return result


# ── Composite entry point ──────────────────────────────────────────────────


def compute_backtest_metrics(
    daily_summary: pd.DataFrame,
    *,
    debug_artifacts_dir: Path | None = None,
    benchmark_returns: pd.Series | None = None,
    price_data: pd.DataFrame | None = None,
    top_n: int = 20,
    buffer_hold: int = 60,
) -> dict[str, Any]:
    """Compute all backtest metrics from a BacktestRunner daily_summary.

    Parameters
    ----------
    daily_summary : pd.DataFrame
        From ``BacktestRunResult.daily_summary`` (or loaded from
        ``daily_summary.csv``).  Must contain ``trade_date`` and
        ``total_value_after`` columns.
    debug_artifacts_dir : Path or None
        Path to the ``daily/`` subdirectory of a BacktestRunner debug run.
        If provided, enables portfolio contribution analysis.
    benchmark_returns : pd.Series or None
        Benchmark daily returns indexed by date.  If provided, enables
        excess return computation.
    price_data : pd.DataFrame or None
        Daily close prices with columns ``trade_date``, ``instrument``,
        ``$close``.  Required for bucket contribution analysis.
    top_n : int
        Top-N threshold (default 20).
    buffer_hold : int
        Buffer threshold (default 60).

    Returns
    -------
    dict of metric name → value.  Includes a ``details`` key with
    supporting data (daily returns series, drawdown periods, etc.).
    """
    # Parse dates and sort
    df = daily_summary.copy()
    if "trade_date" in df.columns:
        df = df.sort_values("trade_date").reset_index(drop=True)

    equity = _to_equity_series(df)
    rets = _daily_returns(equity)

    ann_ret = annual_return(equity)
    ann_vol = annual_volatility(rets)
    sp = sharpe(rets)
    mdd = max_drawdown(equity)
    cal = calmar(ann_ret, mdd)
    wr = win_rate(rets)
    ann_to = annual_turnover(df)
    ahd = avg_holding_days(ann_to)

    metrics: dict[str, Any] = {
        "total_return": total_return(equity),
        "annual_return": ann_ret,
        "annual_volatility": ann_vol,
        "sharpe": sp,
        "max_drawdown": mdd,
        "calmar": cal,
        "annual_turnover": ann_to,
        "avg_holding_days": ahd if ahd != float("inf") else None,
        "win_rate": wr,
        "n_trading_days": len(rets),
    }

    # Worst days / drawdowns
    metrics["worst_5_days"] = worst_n_days(rets, 5)
    metrics["worst_5_drawdowns"] = worst_n_drawdowns(equity, 5)

    # Excess return vs benchmark
    if benchmark_returns is not None:
        ex = excess_return(equity, benchmark_returns)
        metrics.update(ex)

    # Portfolio contribution
    if (debug_artifacts_dir is not None and price_data is not None
            and isinstance(price_data, pd.DataFrame) and not price_data.empty):
        preds = _load_debug_predictions(debug_artifacts_dir)
        tw = _load_debug_target_weights(debug_artifacts_dir)
        if tw:
            contrib = bucket_contribution(
                preds, tw, price_data,
                top_n=top_n, buffer_hold=buffer_hold,
            )
            metrics.update(contrib)

    # Details for further analysis
    metrics["details"] = {
        "daily_returns": rets,
        "equity_curve": equity,
        "benchmark_returns": benchmark_returns,
    }

    return metrics


# ── CLI ────────────────────────────────────────────────────────────────────


def _parse_args() -> Any:
    import argparse

    parser = argparse.ArgumentParser(description="Compute backtest metrics from daily_summary.csv")
    parser.add_argument(
        "--daily-summary",
        required=True,
        help="Path to daily_summary.csv from BacktestRunner",
    )
    parser.add_argument(
        "--debug-artifacts",
        default=None,
        help="Path to the daily/ debug artifacts directory (optional)",
    )
    parser.add_argument(
        "--benchmark",
        default=None,
        help="Path to benchmark returns CSV with columns trade_date,return (optional)",
    )
    parser.add_argument(
        "--price-data",
        default=None,
        help="Path to price data CSV (trade_date,instrument,$close) for bucket analysis",
    )
    parser.add_argument(
        "--top-n", type=int, default=20, help="Top-N threshold (default 20)"
    )
    parser.add_argument(
        "--buffer-hold", type=int, default=60, help="Buffer threshold (default 60)"
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path to write metrics JSON",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    daily = pd.read_csv(args.daily_summary)
    benchmark = (
        pd.read_csv(args.benchmark, index_col="trade_date", squeeze=True)
        if args.benchmark
        else None
    )
    price_data = pd.read_csv(args.price_data) if args.price_data else None
    debug_dir = Path(args.debug_artifacts) if args.debug_artifacts else None

    metrics = compute_backtest_metrics(
        daily,
        debug_artifacts_dir=debug_dir,
        benchmark_returns=benchmark,
        price_data=price_data,
        top_n=args.top_n,
        buffer_hold=args.buffer_hold,
    )

    # Print summary
    print("\n" + "=" * 50)
    print("Backtest Performance Summary")
    print("=" * 50)
    print(f"  Total Return:       {metrics['total_return']:>8.2%}")
    print(f"  Annual Return:      {metrics['annual_return']:>8.2%}")
    print(f"  Annual Vol:         {metrics['annual_volatility']:>8.2%}")
    print(f"  Sharpe:             {metrics['sharpe']:>8.4f}")
    print(f"  Max Drawdown:       {metrics['max_drawdown']:>8.2%}")
    print(f"  Calmar:             {metrics['calmar']:>8.4f}")
    print(f"  Annual Turnover:    {metrics['annual_turnover']:>8.2f}x")
    print(f"  Avg Holding Days:   {metrics['avg_holding_days']:>8.1f}" if metrics['avg_holding_days'] is not None else "  Avg Holding Days:      inf")
    print(f"  Win Rate (daily):   {metrics['win_rate']:>8.2%}")
    if "excess_return" in metrics:
        print(f"  Excess Return:      {metrics['excess_return']:>8.2%}")
        print(f"  Excess Sharpe:      {metrics['excess_sharpe']:>8.4f}")
    print(f"  Trading Days:       {metrics['n_trading_days']:>8d}")
    print()

    if metrics.get("worst_5_days"):
        print("  Worst 5 Days:")
        for d in metrics["worst_5_days"]:
            print(f"    {d['date']}: {d['return']:.2%}")

    if metrics.get("worst_5_drawdowns"):
        print("\n  Worst 5 Drawdowns:")
        for dd in metrics["worst_5_drawdowns"]:
            end = dd.get("end_date", "ongoing")
            print(f"    {dd['start_date']} → {end}: {dd['depth']:.2%} ({dd['duration_days']}d)")

    if "top_contribution" in metrics:
        print(f"\n  Bucket Contribution:")
        print(f"    Top {args.top_n}:          {metrics['top_contribution']:>8.4f} avg period return")
        print(f"    Buffer {args.top_n}-{args.buffer_hold}:  {metrics['buffer_contribution']:>8.4f} avg period return")
        print(f"    Other:          {metrics['other_contribution']:>8.4f} avg period return")

    # Write JSON
    if args.output:
        out = {k: v for k, v in metrics.items() if k != "details"}
        # Convert non-serializable values
        import json

        class _Encoder(json.JSONEncoder):
            def default(self, o):
                if isinstance(o, (np.integer,)): return int(o)
                if isinstance(o, (np.floating,)): return float(o)
                if isinstance(o, (np.ndarray, pd.Series)): return o.tolist()
                return super().default(o)

        Path(args.output).write_text(
            json.dumps(out, indent=2, cls=_Encoder, ensure_ascii=False)
        )
        print(f"\n  → {args.output}")


if __name__ == "__main__":
    main()
