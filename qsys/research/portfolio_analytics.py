"""Auditable portfolio analytics for canonical cached-signal backtests."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from qsys.research.evaluation import compute_rank_stability
from qsys.signal.store import SignalStore


SCHEMA_VERSION = "portfolio_analytics_v2"
CONFIG_SCHEMA_VERSION = "portfolio_analytics_config_v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _finite(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    return value


def _series_summary(values: pd.Series) -> dict[str, Any]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return {"observations": 0, "mean": None, "median": None, "min": None, "max": None}
    return {
        "observations": int(len(clean)),
        "mean": float(clean.mean()),
        "median": float(clean.median()),
        "min": float(clean.min()),
        "max": float(clean.max()),
    }


def _return_metrics(
    daily_returns: pd.Series,
    *,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
) -> dict[str, Any]:
    returns = pd.to_numeric(daily_returns, errors="coerce").fillna(0.0).astype(float)
    compounded = (1.0 + returns).cumprod()
    total_return = (
        float(compounded.iloc[-1] - 1.0) if not compounded.empty else 0.0
    )
    elapsed_days = max(1, int((end_date - start_date).days))
    years = elapsed_days / 365.25
    cagr = float((1.0 + total_return) ** (1.0 / years) - 1.0) if total_return > -1.0 else -1.0
    volatility = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    sharpe = float(returns.mean() / volatility * math.sqrt(252.0)) if volatility > 0 else 0.0
    wealth = pd.Series(
        np.concatenate(([1.0], compounded.to_numpy(dtype=float)))
    )
    max_drawdown = float((wealth / wealth.cummax() - 1.0).min())
    calmar = float(cagr / abs(max_drawdown)) if max_drawdown < 0 else None
    var_95 = float(returns.quantile(0.05)) if len(returns) else None
    tail = returns[returns <= var_95] if var_95 is not None else returns.iloc[0:0]
    cvar_95 = float(tail.mean()) if len(tail) else None
    return {
        "total_return": total_return,
        "cagr": cagr,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "calmar": calmar,
        "daily_volatility_annualized": volatility * math.sqrt(252.0),
        "historical_var_95_daily": var_95,
        "historical_cvar_95_daily": cvar_95,
        "positive_day_ratio": float((returns > 0).mean()) if len(returns) else None,
        "trading_day_count": int(len(returns)),
    }


def _capm_metrics(
    portfolio_returns: pd.Series,
    benchmark_returns: pd.Series,
) -> dict[str, Any]:
    """Single-factor daily CAPM regression with a zero risk-free rate."""
    frame = pd.concat(
        [portfolio_returns.rename("portfolio"), benchmark_returns.rename("benchmark")],
        axis=1,
    ).dropna()
    if len(frame) < 2:
        return {
            "observations": int(len(frame)),
            "beta": None,
            "alpha_daily": None,
            "alpha_annualized": None,
            "residual_volatility_annualized": None,
            "r_squared": None,
            "risk_free_rate_daily": 0.0,
        }
    benchmark_variance = float(frame["benchmark"].var(ddof=0))
    if benchmark_variance <= 0:
        beta = 0.0
    else:
        beta = float(
            np.cov(
                frame["portfolio"], frame["benchmark"], ddof=0
            )[0, 1] / benchmark_variance
        )
    alpha_daily = float(
        frame["portfolio"].mean() - beta * frame["benchmark"].mean()
    )
    fitted = alpha_daily + beta * frame["benchmark"]
    residual = frame["portfolio"] - fitted
    total_ss = float(
        np.square(frame["portfolio"] - frame["portfolio"].mean()).sum()
    )
    residual_ss = float(np.square(residual).sum())
    alpha_annualized = (
        float((1.0 + alpha_daily) ** 252.0 - 1.0)
        if alpha_daily > -1.0 else -1.0
    )
    return {
        "observations": int(len(frame)),
        "beta": beta,
        "alpha_daily": alpha_daily,
        "alpha_annualized": alpha_annualized,
        "residual_volatility_annualized": (
            float(residual.std(ddof=1) * math.sqrt(252.0))
            if len(residual) > 1 else 0.0
        ),
        "r_squared": (
            1.0 - residual_ss / total_ss if total_ss > 0 else 0.0
        ),
        "risk_free_rate_daily": 0.0,
    }


def _annual_returns(returns: pd.Series) -> pd.DataFrame:
    frame = pd.DataFrame({"trade_date": returns.index, "daily_return": returns.values})
    frame["year"] = pd.to_datetime(frame["trade_date"]).dt.year
    annual = frame.groupby("year", sort=True)["daily_return"].apply(
        lambda values: float((1.0 + values.astype(float)).prod() - 1.0)
    )
    return annual.rename("annual_return").reset_index()


def _window_max_drawdown(values: np.ndarray) -> float:
    wealth = np.concatenate(([1.0], np.cumprod(1.0 + values)))
    return float(np.min(wealth / np.maximum.accumulate(wealth) - 1.0))


def _rolling_metrics(returns: pd.Series, windows: tuple[int, ...]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    clean = returns.astype(float)
    for window in windows:
        if window <= 1:
            raise ValueError("rolling windows must be greater than one")
        rolling_return = (1.0 + clean).rolling(window).apply(np.prod, raw=True) - 1.0
        rolling_drawdown = clean.rolling(window).apply(_window_max_drawdown, raw=True)
        for date in clean.index:
            if pd.isna(rolling_return.loc[date]):
                continue
            rows.append({
                "trade_date": pd.Timestamp(date).strftime("%Y-%m-%d"),
                "window_sessions": int(window),
                "rolling_return": float(rolling_return.loc[date]),
                "rolling_max_drawdown": float(rolling_drawdown.loc[date]),
            })
    return pd.DataFrame(
        rows,
        columns=[
            "trade_date", "window_sessions", "rolling_return",
            "rolling_max_drawdown",
        ],
    )


def _load_benchmark_returns(
    benchmark_csv: Path,
    trade_dates: pd.DatetimeIndex,
) -> tuple[pd.DataFrame, pd.Series]:
    benchmark = pd.read_csv(benchmark_csv)
    required = {"trade_date", "open", "close"}
    missing = sorted(required - set(benchmark.columns))
    if missing:
        raise ValueError(f"benchmark CSV missing columns: {missing}")
    benchmark["trade_date"] = pd.to_datetime(
        benchmark["trade_date"].astype(str), errors="coerce"
    )
    benchmark["open"] = pd.to_numeric(benchmark["open"], errors="coerce")
    benchmark["close"] = pd.to_numeric(benchmark["close"], errors="coerce")
    benchmark = benchmark.dropna(subset=["trade_date", "close"]).sort_values("trade_date")
    if benchmark["trade_date"].duplicated().any():
        raise ValueError("benchmark CSV contains duplicate trade dates")
    indexed = benchmark.set_index("trade_date")
    missing_dates = trade_dates.difference(indexed.index)
    if len(missing_dates):
        raise ValueError(
            "benchmark CSV does not cover backtest dates: "
            f"{[value.strftime('%Y-%m-%d') for value in missing_dates[:5]]}"
        )
    selected = indexed.loc[trade_dates].copy()
    returns = selected["close"].pct_change()
    first_open = float(selected["open"].iloc[0])
    first_close = float(selected["close"].iloc[0])
    if not math.isfinite(first_open) or first_open <= 0:
        raise ValueError("benchmark first-day open must be positive")
    returns.iloc[0] = first_close / first_open - 1.0
    trend = indexed["close"].pct_change(60).shift(1)
    return benchmark, returns.astype(float).rename("benchmark_return")


def _regime_metrics(
    portfolio_returns: pd.Series,
    benchmark_returns: pd.Series,
    benchmark_history: pd.DataFrame,
) -> pd.DataFrame:
    history = benchmark_history.set_index("trade_date").sort_index()
    trend = history["close"].pct_change(60).shift(1)
    regime = pd.Series("range", index=portfolio_returns.index, dtype="object")
    aligned_trend = trend.reindex(portfolio_returns.index)
    regime.loc[aligned_trend > 0.05] = "uptrend"
    regime.loc[aligned_trend < -0.05] = "downtrend"
    regime.loc[aligned_trend.isna()] = "unknown"
    rows: list[dict[str, Any]] = []
    for name in ("uptrend", "range", "downtrend", "unknown"):
        mask = regime == name
        if not mask.any():
            continue
        portfolio = portfolio_returns.loc[mask]
        benchmark = benchmark_returns.loc[mask]
        active = portfolio - benchmark
        active_std = float(active.std(ddof=1)) if len(active) > 1 else 0.0
        rows.append({
            "regime": name,
            "day_count": int(mask.sum()),
            "portfolio_total_return": float((1.0 + portfolio).prod() - 1.0),
            "portfolio_mean_daily_return": float(portfolio.mean()),
            "portfolio_sharpe": (
                float(portfolio.mean() / portfolio.std(ddof=1) * math.sqrt(252.0))
                if len(portfolio) > 1 and float(portfolio.std(ddof=1)) > 0 else 0.0
            ),
            "benchmark_total_return": float((1.0 + benchmark).prod() - 1.0),
            "active_mean_daily_return": float(active.mean()),
            "active_information_ratio": (
                float(active.mean() / active_std * math.sqrt(252.0))
                if active_std > 0 else 0.0
            ),
        })
    return pd.DataFrame(rows)


def _exposure_metrics(
    daily: pd.DataFrame,
    valuation: pd.DataFrame,
) -> pd.DataFrame:
    totals = daily.set_index("trade_date")["total_value_after"].astype(float)
    base = pd.DataFrame(index=totals.index)
    base["gross_exposure"] = (
        daily.set_index("trade_date")["market_value_after"].astype(float)
        / totals.replace(0.0, np.nan)
    )
    base["cash_ratio"] = (
        daily.set_index("trade_date")["cash_after"].astype(float)
        / totals.replace(0.0, np.nan)
    )
    base["position_count"] = daily.set_index("trade_date")["position_count"].astype(float)
    base["top1_equity_weight"] = 0.0
    base["position_weight_hhi"] = 0.0
    base["effective_position_count"] = 0.0
    if not valuation.empty:
        frame = valuation.copy()
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
        frame["market_value"] = pd.to_numeric(frame["market_value"], errors="coerce").fillna(0.0)
        frame = frame[frame["market_value"] > 0]
        for date, group in frame.groupby("trade_date", sort=True):
            if date not in base.index:
                continue
            total_value = float(totals.loc[date])
            position_value = float(group["market_value"].sum())
            equity_weights = group["market_value"] / max(total_value, 1e-12)
            normalized = group["market_value"] / max(position_value, 1e-12)
            hhi = float(np.square(normalized).sum())
            base.loc[date, "top1_equity_weight"] = float(equity_weights.max())
            base.loc[date, "position_weight_hhi"] = hhi
            base.loc[date, "effective_position_count"] = 1.0 / hhi if hhi > 0 else 0.0
    return base.reset_index().rename(columns={"index": "trade_date"})


def write_portfolio_analytics(
    *,
    backtest_dir: str | Path,
    research_root: str | Path,
    benchmark_id: str,
    benchmark_csv: str | Path,
    holdout_start: str,
    rolling_windows: tuple[int, ...] = (60, 120),
    output_name: str | None = None,
    terminal_authorization_ref: str | None = None,
) -> dict[str, Any]:
    """Compute and persist transparent analytics for one frozen backtest."""
    source_directory = Path(backtest_dir).resolve()
    if output_name is None:
        directory = source_directory
    else:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", output_name):
            raise ValueError("portfolio analytics output_name is not a safe path segment")
        directory = source_directory / "portfolio_analytics" / output_name
        if directory.is_symlink():
            raise ValueError("portfolio analytics output directory cannot be a symlink")
    root = Path(research_root).resolve()
    benchmark_path = Path(benchmark_csv).resolve()
    manifest_path = source_directory / "manifest.json"
    metrics_path = source_directory / "metrics.json"
    daily_path = source_directory / "daily_summary.csv"
    executions_path = source_directory / "executions.csv"
    valuation_path = source_directory / "valuation_ledger.csv"
    for path in (
        manifest_path, metrics_path, daily_path, executions_path, valuation_path,
        benchmark_path,
    ):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"portfolio analytics input is not a regular file: {path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    if manifest.get("artifact_type") != "backtest_run":
        raise ValueError("invalid backtest manifest")
    if manifest.get("accounting", {}).get("schema_version") != "accounting_v1":
        raise ValueError("portfolio analytics requires complete accounting v1")
    holdout_consumed = str(manifest.get("end_date")) >= holdout_start
    if holdout_consumed and not str(terminal_authorization_ref or "").strip():
        raise ValueError(
            "portfolio analytics backtest overlaps declared holdout without "
            "terminal authorization"
        )

    daily = pd.read_csv(daily_path)
    required_daily = {
        "trade_date", "cash_after", "market_value_after", "total_value_after",
        "position_count", "turnover",
    }
    missing_daily = sorted(required_daily - set(daily.columns))
    if missing_daily:
        raise ValueError(f"daily summary missing columns: {missing_daily}")
    daily["trade_date"] = pd.to_datetime(daily["trade_date"], errors="coerce")
    daily = daily.sort_values("trade_date").reset_index(drop=True)
    if daily["trade_date"].isna().any() or daily["trade_date"].duplicated().any():
        raise ValueError("daily summary dates are invalid or duplicated")
    if daily.empty:
        raise ValueError("daily summary is empty")
    daily_consumes_holdout = daily["trade_date"].max() >= pd.Timestamp(holdout_start)
    if daily_consumes_holdout != holdout_consumed:
        raise ValueError("backtest manifest and daily summary disagree on holdout use")
    values = pd.to_numeric(daily["total_value_after"], errors="coerce")
    if values.isna().any() or (values <= 0).any():
        raise ValueError("daily total values must be positive and finite")
    initial_capital = float(manifest["initial_capital"])
    daily_returns = values.set_axis(daily["trade_date"]).pct_change()
    daily_returns.iloc[0] = float(values.iloc[0] / initial_capital - 1.0)
    daily_returns = daily_returns.astype(float).rename("portfolio_return")

    benchmark_history, benchmark_returns = _load_benchmark_returns(
        benchmark_path, pd.DatetimeIndex(daily["trade_date"])
    )
    performance = _return_metrics(
        daily_returns,
        start_date=daily["trade_date"].iloc[0],
        end_date=daily["trade_date"].iloc[-1],
    )
    benchmark_performance = _return_metrics(
        benchmark_returns,
        start_date=daily["trade_date"].iloc[0],
        end_date=daily["trade_date"].iloc[-1],
    )
    active = daily_returns - benchmark_returns
    active_std = float(active.std(ddof=1)) if len(active) > 1 else 0.0
    relative_wealth = (1.0 + daily_returns).cumprod() / (1.0 + benchmark_returns).cumprod()
    relative = {
        "relative_total_return": float(relative_wealth.iloc[-1] - 1.0),
        "total_return_difference": float(
            performance["total_return"] - benchmark_performance["total_return"]
        ),
        "active_mean_daily_return": float(active.mean()),
        "tracking_error_annualized": active_std * math.sqrt(252.0),
        "information_ratio": (
            float(active.mean() / active_std * math.sqrt(252.0))
            if active_std > 0 else 0.0
        ),
    }
    capm = _capm_metrics(daily_returns, benchmark_returns)

    annual_portfolio = _annual_returns(daily_returns).rename(
        columns={"annual_return": "portfolio_return"}
    )
    annual_benchmark = _annual_returns(benchmark_returns).rename(
        columns={"annual_return": "benchmark_return"}
    )
    annual = annual_portfolio.merge(annual_benchmark, on="year", how="outer")
    annual["active_return_difference"] = annual["portfolio_return"] - annual["benchmark_return"]
    rolling = _rolling_metrics(daily_returns, rolling_windows)
    regime = _regime_metrics(daily_returns, benchmark_returns, benchmark_history)
    valuation = pd.read_csv(valuation_path)
    exposure = _exposure_metrics(daily, valuation)

    signal_id = str(manifest["signal_id"])
    signal_run_id = str(manifest["signal_run_id"])
    signal_store = SignalStore(root)
    signal_identity = signal_store.validate_backtest_source(signal_id, signal_run_id)
    signal = signal_store.load_signal_run(
        signal_id,
        signal_run_id,
        start_date=str(manifest["start_date"]),
        end_date=str(manifest["end_date"]),
    )
    if signal.empty or not (
        signal["data_date"].astype(str) < signal["trade_date"].astype(str)
    ).all():
        raise ValueError("signal stability input is empty or violates preopen visibility")
    stability, stability_summary = compute_rank_stability(
        signal, score_column=str(manifest["score_column"]), top_ks=(5, 20, 50)
    )

    executions = pd.read_csv(executions_path)
    filled = executions[executions["status"] == "filled"] if not executions.empty else executions
    rejected = (
        executions[executions["status"] == "rejected"]
        if not executions.empty
        else executions
    )

    def _filled_sum(column: str) -> float:
        if filled.empty:
            return 0.0
        return float(
            pd.to_numeric(filled.get(column), errors="coerce").fillna(0).sum()
        )

    execution_summary = {
        "order_count": int(len(executions)),
        "filled_count": int(len(filled)),
        "rejected_count": int(len(rejected)),
        "gross_amount": _filled_sum("gross_amount"),
        "commission": _filled_sum("commission"),
        "tax": _filled_sum("tax"),
        "total_fee": _filled_sum("total_fee"),
        "max_participation_rate_observed": (
            float(
                pd.to_numeric(
                    filled.get("participation_rate"), errors="coerce"
                ).fillna(0).max()
            )
            if not filled.empty
            else 0.0
        ),
    }
    turnover = {
        "turnover_total": float(metrics["turnover_total"]),
        "turnover_annualized": float(metrics["turnover_annualized"]),
        "rebalance_due_day_count": int(metrics["rebalance_due_day_count"]),
        "rebalance_executed_day_count": int(metrics["rebalance_executed_day_count"]),
    }
    rolling_summary = {
        str(window): {
            "return": _series_summary(group["rolling_return"]),
            "max_drawdown": _series_summary(group["rolling_max_drawdown"]),
        }
        for window, group in rolling.groupby("window_sessions")
    }
    exposure_summary = {
        column: _series_summary(exposure[column])
        for column in exposure.columns if column != "trade_date"
    }

    inputs = {
        "backtest_manifest_sha256": _sha256(manifest_path),
        "daily_summary_sha256": _sha256(daily_path),
        "metrics_sha256": _sha256(metrics_path),
        "executions_sha256": _sha256(executions_path),
        "valuation_ledger_sha256": _sha256(valuation_path),
        "signal_id": signal_id,
        "signal_run_id": signal_run_id,
        "signal_manifest_sha256": signal_identity["manifest_sha256"],
        "predictions_sha256": signal_identity["predictions_sha256"],
        "benchmark_id": benchmark_id,
        "benchmark_path": str(benchmark_path),
        "benchmark_sha256": _sha256(benchmark_path),
    }
    analytics = _finite({
        "schema_version": SCHEMA_VERSION,
        "backtest_id": manifest["backtest_id"],
        "strategy_run_id": manifest["strategy_run_id"],
        "start_date": manifest["start_date"],
        "end_date": manifest["end_date"],
        "holdout_start": holdout_start,
        "holdout_consumed": holdout_consumed,
        "terminal_authorization_ref": terminal_authorization_ref,
        "inputs": inputs,
        "portfolio_spec": {
            "top_n": int(manifest["allocation_params"]["top_n"]),
            "rebalance_freq": manifest["rebalance_freq"],
            "commission": float(manifest["commission_bp"]),
            "stamp_duty": float(manifest["stamp_duty_bp"]),
            "min_commission": float(manifest["min_commission"]),
            "slippage": float(manifest["slippage"]),
        },
        "performance": performance,
        "annual_returns": annual.to_dict(orient="records"),
        "rolling": rolling_summary,
        "turnover": turnover,
        "execution": execution_summary,
        "exposure_and_concentration": exposure_summary,
        "benchmark": {
            "id": benchmark_id,
            "performance": benchmark_performance,
            "relative": relative,
            "capm": capm,
            "return_contract": "first_day_open_to_close_then_close_to_close_v1",
            "capm_contract": "daily_ols_zero_risk_free_rate_v1",
        },
        "topn_selection_stability": stability_summary,
        "regime_contract": {
            "benchmark_id": benchmark_id,
            "trend_lookback_sessions": 60,
            "information_lag_sessions": 1,
            "thresholds": {"uptrend": 0.05, "downtrend": -0.05},
        },
        "regime_performance": regime.to_dict(orient="records"),
    })

    csv_outputs = {
        "annual_returns.csv": annual,
        "rolling_metrics.csv": rolling,
        "exposure_daily.csv": exposure,
        "topn_stability.csv": stability,
        "regime_returns.csv": regime,
    }
    for name, frame in csv_outputs.items():
        _write_text(directory / name, frame.to_csv(index=False, lineterminator="\n"))
    _write_text(
        directory / "portfolio_analytics.json",
        json.dumps(analytics, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
    )
    identity = {
        "schema_version": SCHEMA_VERSION,
        "backtest_id": manifest["backtest_id"],
        "output_name": output_name,
        "producer_code_sha256": _sha256(Path(__file__)),
        **inputs,
        "holdout_start": holdout_start,
        "holdout_consumed": holdout_consumed,
        "terminal_authorization_ref": terminal_authorization_ref,
        "rolling_windows": list(rolling_windows),
    }
    analytics_manifest = {
        **identity,
        "portfolio_analytics_identity_sha256": _canonical_hash(identity),
        "outputs": {
            name: {"sha256": _sha256(directory / name)}
            for name in ("portfolio_analytics.json", *csv_outputs)
        },
    }
    _write_text(
        directory / "portfolio_analytics_manifest.json",
        json.dumps(analytics_manifest, indent=2, ensure_ascii=False) + "\n",
    )
    return {
        "portfolio_analytics_identity_sha256": analytics_manifest[
            "portfolio_analytics_identity_sha256"
        ],
        "manifest": str(directory / "portfolio_analytics_manifest.json"),
        "analytics": str(directory / "portfolio_analytics.json"),
    }


def validate_portfolio_analytics(
    *,
    backtest_dir: str | Path,
    research_root: str | Path,
    output_name: str,
) -> dict[str, Any]:
    """Independently validate one named portfolio-analytics artifact."""
    source = Path(backtest_dir).resolve()
    root = Path(research_root).resolve()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", output_name):
        raise ValueError("portfolio analytics output_name is not a safe path segment")
    directory = source / "portfolio_analytics" / output_name
    manifest_path = directory / "portfolio_analytics_manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("portfolio analytics manifest is not a regular file")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    identity = {
        key: value for key, value in manifest.items()
        if key not in {"portfolio_analytics_identity_sha256", "outputs"}
    }
    if _canonical_hash(identity) != manifest.get(
        "portfolio_analytics_identity_sha256"
    ):
        raise ValueError("portfolio analytics identity hash mismatch")
    for name, expected in manifest.get("outputs", {}).items():
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"portfolio analytics output is not regular: {name}")
        if _sha256(path) != expected.get("sha256"):
            raise ValueError(f"portfolio analytics output hash mismatch: {name}")
    source_files = {
        "backtest_manifest_sha256": source / "manifest.json",
        "daily_summary_sha256": source / "daily_summary.csv",
        "metrics_sha256": source / "metrics.json",
        "executions_sha256": source / "executions.csv",
        "valuation_ledger_sha256": source / "valuation_ledger.csv",
        "benchmark_sha256": Path(str(manifest["benchmark_path"])),
    }
    for key, path in source_files.items():
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"portfolio analytics source is not regular: {path}")
        if _sha256(path) != manifest.get(key):
            raise ValueError(f"portfolio analytics source hash mismatch: {key}")
    signal = SignalStore(root).validate_backtest_source(
        str(manifest["signal_id"]), str(manifest["signal_run_id"])
    )
    if signal["manifest_sha256"] != manifest.get("signal_manifest_sha256"):
        raise ValueError("portfolio analytics signal manifest hash mismatch")
    if signal["predictions_sha256"] != manifest.get("predictions_sha256"):
        raise ValueError("portfolio analytics predictions hash mismatch")
    return {
        "portfolio_analytics_identity_sha256": manifest[
            "portfolio_analytics_identity_sha256"
        ],
        "manifest": str(manifest_path),
        "analytics": str(directory / "portfolio_analytics.json"),
        "validation": "passed",
    }


def run_portfolio_analytics_config(
    config_path: str | Path, *, validate_only: bool = False
) -> dict[str, Any]:
    """Write or validate several benchmark views over one frozen backtest."""
    path = Path(config_path).resolve()
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"portfolio analytics config is not a regular file: {path}")
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if config.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ValueError("unsupported portfolio analytics config schema")
    common = {
        "backtest_dir": config["backtest_dir"],
        "research_root": config["research_root"],
    }
    results: list[dict[str, Any]] = []
    for benchmark in config.get("benchmarks") or []:
        output_name = str(benchmark["output_name"])
        if validate_only:
            result = validate_portfolio_analytics(
                **common, output_name=output_name
            )
        else:
            result = write_portfolio_analytics(
                **common,
                benchmark_id=str(benchmark["benchmark_id"]),
                benchmark_csv=benchmark["benchmark_csv"],
                holdout_start=str(config["holdout_start"]),
                output_name=output_name,
                terminal_authorization_ref=config.get(
                    "terminal_authorization_ref"
                ),
            )
        results.append({"benchmark_id": benchmark["benchmark_id"], **result})
    if not results:
        raise ValueError("portfolio analytics config has no benchmarks")
    return {"config": str(path), "benchmarks": results}
