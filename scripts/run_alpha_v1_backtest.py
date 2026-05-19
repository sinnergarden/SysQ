#!/usr/bin/env python3
"""Alpha V1 — Rolling Weekly Backtest (thin CLI)."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore")

import argparse
import numpy as np
import pandas as pd

from qlib.data import D
from qsys.data.adapter import QlibAdapter
from qsys.feature.library import FeatureLibrary
from qsys.reports.backtest import BacktestReport
from qsys.reports.base import ReportSection, ReportStatus, save_report
from qsys.trader.account import Account
from qsys.trader.matcher import MatchEngine

from qsys.backtest import (
    BacktestEngine,
    build_rank_weight_portfolio,
    build_trading_day_windows,
    compute_trade_flags,
    get_rebalance_dates,
)
from qsys.config.loader import load_yaml_config, merge_cli_overrides, write_resolved_config
from qsys.strategy.alpha_v1 import (
    build_candidate_from_config,
    get_clean_features,
    precompute_alpha_v1_signals,
)


# ── Data Loading (Qlib-specific, kept in script) ──

def load_data(universe, start, end, data_end, price_mode):
    """Load all data upfront. Returns (DataFrame, clean_features)."""
    print("[Data] Loading...")
    t0 = time.time()

    adapter = QlibAdapter()
    adapter.init_qlib()
    all_features = FeatureLibrary.get_semantic_all_features_config()

    fetch_end = data_end or end or datetime.now().strftime("%Y-%m-%d")

    raw = adapter.get_features(
        universe, all_features + ["$close"],
        start_time=start, end_time=fetch_end,
    )
    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    frame = frame.loc[:, ~frame.columns.duplicated()]

    try:
        insts = D.instruments(universe)
        open_raw = D.features(insts, ["$open"], start_time=start, end_time=fetch_end)
        open_df = open_raw.reset_index().rename(columns={"datetime": "trade_date"})
        open_df = open_df[["trade_date", "instrument", "$open"]].dropna(subset=["$open"])
        open_df = open_df.drop_duplicates(subset=["trade_date", "instrument"])
        frame = frame.merge(open_df, on=["trade_date", "instrument"], how="left")
        if "$open" in frame.columns:
            n_nonnull = frame["$open"].notna().sum()
            print(f"  $open loaded (non-null: {n_nonnull})")
            if n_nonnull == 0:
                raise ValueError("$open is entirely null")
        else:
            raise ValueError("$open column missing")
    except Exception as e:
        if price_mode == "open":
            print("  ERROR: $open unavailable and --price-mode=open.")
            raise RuntimeError(f"$open fetch failed in price_mode='open': {e}") from e
        print(f"  WARN: $open failed ({e}), using $close (price_mode=close_fallback)")
        frame["$open"] = frame["$close"]

    # VWAP
    if "$amount" in frame.columns and "$volume" in frame.columns:
        vol_safe = frame["$volume"].replace(0, np.nan)
        frame["$vwap"] = frame["$amount"] / vol_safe
    else:
        frame["$vwap"] = frame["$close"]

    # Industry
    db_paths = [Path("data/meta.db"), Path("data/meta/meta.db")]
    for dp in db_paths:
        if dp.exists():
            with sqlite3.connect(dp) as conn:
                sb = pd.read_sql("select ts_code, industry from stock_basic", conn)
            sb = sb.rename(columns={"ts_code": "instrument"})
            frame = frame.merge(sb, on="instrument", how="left")
            break

    frame = frame.sort_values(["instrument", "trade_date"]).reset_index(drop=True)
    clean_features = get_clean_features(all_features)
    print(
        f"  Full data: {len(frame)} rows, {frame['trade_date'].nunique()}d, "
        f"clean features={len(clean_features)}"
    )
    print(f"  Time: {time.time()-t0:.1f}s")

    # Forward returns for IC computation
    from qsys.signal.alpha_v1.labels import make_forward_returns
    make_forward_returns(frame, horizons=[1, 5, 20])

    return frame, clean_features


# ── Metrics / Health / Outputs (kept in script for simplicity) ──

def compute_window_metrics(daily_rows, trade_rows, test_data):
    """Compute performance metrics from aggregated results."""
    ddf = pd.DataFrame(daily_rows) if daily_rows else pd.DataFrame()
    tdf = pd.DataFrame(trade_rows) if trade_rows else pd.DataFrame()
    if ddf.empty or len(ddf) < 5:
        return {"error": "insufficient_data"}, ddf, tdf

    eq = ddf["equity"].values
    tr = eq[-1] / eq[0] - 1.0
    nd = len(ddf)
    ann = (1 + tr) ** (252 / nd) - 1 if nd > 0 else 0.0
    dr = ddf["ret"].values
    ds = max(np.nanstd(dr), 1e-10)
    av = ds * np.sqrt(252)
    sp = (np.nanmean(dr) / ds) * np.sqrt(252)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    mdd = float(np.min(dd))
    cal = ann / abs(mdd) if mdd < 0 else 0.0

    idx = test_data.groupby("trade_date")["fwd_1d"].mean().rename("idx")
    idx = idx.reindex(pd.to_datetime(ddf["date"])).fillna(0.0)
    ex = dr - idx.values
    ex_ann = (1 + np.nanmean(ex)) ** 252 - 1
    ex_sp = (np.nanmean(ex) / max(np.nanstd(ex), 1e-10)) * np.sqrt(252)

    ttf = float(tdf["fee"].sum()) if not tdf.empty and "fee" in tdf.columns else 0.0
    ae = eq.mean()
    ato = 0.0
    if not tdf.empty and "amount" in tdf.columns and "price" in tdf.columns:
        ato = ((tdf["amount"] * tdf["price"]).sum() / max(ae, 1)) * (252 / max(nd, 1))

    wd = int(np.sum(dr > 0))
    ld = int(np.sum(dr < 0))
    wr = wd / (wd + ld) if wd + ld > 0 else 0.0

    return {
        "total_ret": round(tr, 6), "ann_ret": round(ann, 6), "ann_vol": round(av, 6),
        "sharpe": round(sp, 4), "max_dd": round(mdd, 6), "calmar": round(cal, 4),
        "ex_ann": round(ex_ann, 6), "ex_sharpe": round(ex_sp, 4),
        "win_rate": round(wr, 4), "avg_pos": round(float(ddf["npos"].mean()), 1),
        "ann_to": round(ato, 4), "cost": round(ttf, 2), "ndays": nd,
    }, ddf, tdf


def build_health_report(daily_df, rolling_metrics, config):
    """Check health thresholds and generate alerts."""
    alerts = []
    if not daily_df.empty:
        eq = daily_df["equity"].values
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / peak
        max_dd = float(np.min(dd))
        if max_dd < config.health.dd_crit:
            alerts.append({"severity": "critical", "metric": "MaxDD", "value": round(max_dd, 4), "threshold": -0.20})
        elif max_dd < config.health.dd_warn:
            alerts.append({"severity": "warning", "metric": "MaxDD", "value": round(max_dd, 4), "threshold": -0.15})
    if not rolling_metrics.empty and "total_return" in rolling_metrics.columns:
        pos = int((rolling_metrics["total_return"] > 0).sum())
        wr = pos / max(len(rolling_metrics), 1)
        if wr < 0.4:
            alerts.append({"severity": "critical", "metric": "WinRate", "value": round(wr, 4), "threshold": 0.40})
        elif wr < 0.5:
            alerts.append({"severity": "warning", "metric": "WinRate", "value": round(wr, 4), "threshold": 0.50})
    return {"n_windows": len(rolling_metrics), "n_alerts": len(alerts), "alerts": alerts, "healthy": len(alerts) == 0}


def _capture_git_sha():
    try:
        root = Path(__file__).resolve().parent.parent
        result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=root, timeout=5)
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _compute_benchmark_equity(equity_series, universe, init_cap):
    try:
        adapter = QlibAdapter()
        adapter.init_qlib()
        dates = [d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10] for d in pd.to_datetime(equity_series.index)]
        benchmark_prices = {}
        for date_str in dates:
            try:
                features = adapter.get_features(universe, ["$close"], start_time=date_str, end_time=date_str)
                if features is not None and not features.empty:
                    closes = features["$close"].dropna()
                    if len(closes) > 0:
                        benchmark_prices[date_str] = float(closes.mean())
            except Exception:
                pass
        if benchmark_prices:
            sorted_dates = sorted(benchmark_prices.keys())
            base = benchmark_prices[sorted_dates[0]]
            if base > 0:
                return pd.Series({d: init_cap * benchmark_prices[d] / base for d in sorted_dates}, name="benchmark_equity")
    except Exception:
        pass
    return pd.Series(init_cap, index=equity_series.index, name="benchmark_equity")


def save_ui_report(daily_df, rolling_metrics, perf, total_time, config, universe, start, end, data_end, price_mode, output_dir, ui_reports_dir, signal_rows=None, quintile_log=None, feature_count=0):
    """Generate UI-visible BacktestReport."""
    ui_reports_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"alpha_v1_candidate_{universe}_blend20_weekly_top20_buffer"
    daily_path = ui_reports_dir.parent / f"backtest_result_{run_id}.csv"
    windows_path = ui_reports_dir.parent / f"rolling_windows_{run_id}.csv"

    daily_csv_save = daily_df.copy()
    if "zc_equity" in daily_csv_save.columns:
        daily_csv_save = daily_csv_save.rename(columns={"zc_equity": "zero_cost_total_assets"})
    eq_vals = daily_csv_save["equity"].values
    peak = np.maximum.accumulate(eq_vals)
    daily_csv_save["drawdown"] = (eq_vals - peak) / peak
    daily_csv_save["date_dt"] = pd.to_datetime(daily_csv_save["date"])
    daily_csv_save = daily_csv_save.set_index("date_dt")
    benchmark = _compute_benchmark_equity(daily_csv_save["equity"], universe, config.target_cash)
    daily_csv_save["benchmark_equity"] = benchmark
    daily_csv_save = daily_csv_save.reset_index(drop=True)
    daily_csv_save.to_csv(daily_path, index=False)
    rolling_metrics.to_csv(windows_path, index=False)
    pos_windows = int((rolling_metrics["total_return"] > 0).sum()) if "total_return" in rolling_metrics.columns else 0

    ddf = daily_df.copy()
    ddf["date"] = pd.to_datetime(ddf["date"])
    ddf["week"] = ddf["date"].dt.to_period("W").astype(str)
    weekly_grp = ddf.groupby("week").agg(start_assets=("equity", "first"), end_assets=("equity", "last"))
    weekly_grp["return"] = weekly_grp["end_assets"] / weekly_grp["start_assets"] - 1
    weekly_returns = [{"week": idx, "return": round(float(row["return"]), 6)} for idx, row in weekly_grp.iterrows()]
    pos_weeks = sum(1 for w in weekly_returns if w["return"] > 0)
    ddf["month"] = ddf["date"].dt.to_period("M").astype(str)
    monthly_grp = ddf.groupby("month").agg(start_assets=("equity", "first"), end_assets=("equity", "last"))
    monthly_grp["return"] = monthly_grp["end_assets"] / monthly_grp["start_assets"] - 1
    monthly_returns = [{"month": idx, "return": round(float(row["return"]), 6)} for idx, row in monthly_grp.iterrows()]

    report_df = daily_df.copy().rename(columns={"equity": "total_assets"})
    experiment_spec = {
        "strategy_id": "alpha_v1", "strategy_version": config.version,
        "rolling": {"window_count": len(rolling_metrics), "test_window_days": config.training.test_days, "step_days": config.training.step_days, "windows_completed": len(rolling_metrics), "windows_failed": 0, "retrain_per_window": True, "label_type": "blended_5d_20d"},
        "initial_cash": config.target_cash, "universe": universe, "top_k": config.portfolio.top_n,
        "strategy": "alpha_v1_candidate_blend20_weekly_top20_buffer",
        "date_range": {"start": start, "end": end, "data_end": data_end},
        "price_mode": price_mode, "live_like": price_mode == "open",
        "warnings": ["price_mode=close_fallback: $close used for execution"] if price_mode == "close_fallback" else [],
        "blend_ratio": {"5d": config.blend.blend_5d, "20d": config.blend.blend_20d},
        "feature_set": f"clean_{feature_count}",
        "label": {"type": "cross_sectional_zscore", "horizons": [5, 20], "clip": 3.0},
        "cost_model": config.cost.cost_params,
        "portfolio": {"top_n": config.portfolio.top_n, "buffer_hold": config.portfolio.buffer_hold, "buffer_buy": config.portfolio.buffer_buy, "single_stock_cap": config.portfolio.single_stock_cap, "rebalance_freq": config.portfolio.rebalance_freq},
        "training": {"train_days": config.training.train_days, "test_days": config.training.test_days, "step_days": config.training.step_days, "n_estimators": config.training.n_estimators, "lgb_params": config.training.lgb_params},
        "health_thresholds": config.health.to_dict(),
    }
    report = BacktestReport.from_backtest_result(
        result_df=report_df,
        model_path=str(Path(".").resolve() / "data" / "models" / "alpha_v1_candidate"),
        start_date=str(daily_df["date"].iloc[0]) if not daily_df.empty else "",
        end_date=str(daily_df["date"].iloc[-1]) if not daily_df.empty else "",
        top_k=config.portfolio.top_n, universe=universe, duration_seconds=total_time,
        daily_result_path=str(daily_path), experiment_spec=experiment_spec,
    )
    report.run_id = run_id
    report.plan_summary = experiment_spec
    report.model_info.update({"model_name": "alpha_v1_candidate_ensemble", "feature_set": f"clean_{feature_count}", "label_type": "blended_5d_20d", "blend_ratio": f"{config.blend.blend_5d}:{config.blend.blend_20d}", "strategy": "alpha_v1_candidate_blend20_weekly_top20_buffer", "strategy_version": config.version})

    def sec(name, metrics):
        return ReportSection(name=name, status=ReportStatus.SUCCESS, metrics=metrics)
    sections = [
        sec("Performance", {"total_return": f"{perf['total_ret']*100:.2f}%", "annual_return": f"{perf['ann_ret']*100:.2f}%", "sharpe": f"{perf['sharpe']:.3f}", "max_drawdown": f"{perf['max_dd']*100:.2f}%", "calmar": f"{perf['calmar']:.3f}"}),
        sec("Cost Analysis", {"total_fees": f"{perf.get('cost', 0):.2f}", "annualized_turnover": f"{perf.get('ann_to', 0):.1f}x"}),
        sec("Rolling Windows", {"window_count": str(len(rolling_metrics)), "window_win_rate": f"{pos_windows / max(len(rolling_metrics), 1) * 100:.1f}%", "mean_window_return": f"{rolling_metrics['total_return'].mean() * 100:.2f}%" if "total_return" in rolling_metrics.columns else ""}),
        sec("Weekly Returns", {"total_weeks": str(len(weekly_returns)), "positive_weeks": f"{pos_weeks}/{len(weekly_returns)}", "weekly_win_rate": f"{pos_weeks / max(len(weekly_returns), 1) * 100:.1f}%", "best_week": f"{max(w['return'] for w in weekly_returns) * 100:.2f}%" if weekly_returns else "", "worst_week": f"{min(w['return'] for w in weekly_returns) * 100:.2f}%" if weekly_returns else ""}),
    ]
    report.sections = sections

    signal_metrics_payload: dict[str, Any] = {"status": "available", "aggregate": {}}
    if signal_rows and len(signal_rows) > 0:
        ic_vals = [r.get("IC_mean", np.nan) for r in signal_rows if "IC_mean" in r]
        ric_vals = [r.get("RankIC_mean", np.nan) for r in signal_rows if "RankIC_mean" in r]
        ic_vals = [v for v in ic_vals if not np.isnan(v)]
        ric_vals = [v for v in ric_vals if not np.isnan(v)]
        if ic_vals:
            ic_m, ic_s = float(np.mean(ic_vals)), float(np.std(ic_vals)) if len(ic_vals) > 1 else 1.0
            ric_m, ric_s = float(np.mean(ric_vals)), float(np.std(ric_vals)) if len(ric_vals) > 1 else 1.0
            signal_metrics_payload["IC"] = round(ic_m, 6)
            signal_metrics_payload["RankIC"] = round(ric_m, 6)
            signal_metrics_payload["ICIR"] = round(ic_m / ic_s, 4) if ic_s > 1e-10 else 0
            signal_metrics_payload["RankICIR"] = round(ric_m / ric_s, 4) if ric_s > 1e-10 else 0
            signal_metrics_payload["aggregate"]["IC"] = {"values": ic_vals}
            signal_metrics_payload["aggregate"]["RankIC"] = {"values": ric_vals}
    else:
        signal_metrics_payload["status"] = "not_available"
    if "total_return" in rolling_metrics.columns:
        tr_vals = [float(v) for v in rolling_metrics["total_return"].tolist() if pd.notna(v)]
        signal_metrics_payload["aggregate"]["total_return"] = {"values": tr_vals}
        signal_metrics_payload.setdefault("IC", 0)
        signal_metrics_payload.setdefault("RankIC", 0)
        signal_metrics_payload.setdefault("ICIR", 0)
        signal_metrics_payload.setdefault("RankICIR", 0)
    signal_metrics_payload["long_short_spread"] = 0
    signal_metrics_payload["aggregate"]["long_short_spread"] = {"values": []}
    signal_metrics_payload["aggregate"]["turnover"] = {"values": [0.0]}
    signal_path = ui_reports_dir.parent / f"signal_metrics_{run_id}.json"
    with open(signal_path, "w") as f:
        json.dump(signal_metrics_payload, f, indent=2)
    group_path = ui_reports_dir.parent / f"group_returns_{run_id}.csv"
    if quintile_log and len(quintile_log) > 0:
        group_df = pd.DataFrame(quintile_log).sort_values(["group", "date"])
        group_df["ret"] = group_df.groupby("group")["nav"].transform(lambda x: x.pct_change())
        group_means = group_df.groupby("group")["ret"].mean().to_dict()
        group_df["mean_return"] = group_df["group"].map(group_means)
        group_df["label_horizon"] = "5d"
        group_df.to_csv(group_path, index=False)
    else:
        pd.DataFrame().to_csv(group_path, index=False)
    with open(ui_reports_dir.parent / f"weekly_returns_{run_id}.json", "w") as f:
        json.dump(weekly_returns, f, indent=2)
    with open(ui_reports_dir.parent / f"monthly_returns_{run_id}.json", "w") as f:
        json.dump(monthly_returns, f, indent=2)
    report.artifacts = {"daily_result": str(daily_path), "signal_metrics": str(signal_path), "group_returns": str(group_path), "execution_audit": str(ui_reports_dir.parent / f"execution_audit_{run_id}.csv"), "rolling_windows": str(windows_path), "weekly_returns": str(ui_reports_dir.parent / f"weekly_returns_{run_id}.json"), "monthly_returns": str(ui_reports_dir.parent / f"monthly_returns_{run_id}.json"), "trades": str(output_dir / "trades" / "trade_log.csv")}
    pd.DataFrame().to_csv(ui_reports_dir.parent / f"execution_audit_{run_id}.csv", index=False)
    saved = save_report(report, output_dir=str(ui_reports_dir))
    print(f"  → {saved}")
    return run_id


# ── Main ──

def main():
    t_start = time.time()
    parser = argparse.ArgumentParser(description="Alpha V1 Production Candidate — Rolling Weekly Backtest")
    parser.add_argument("--universe", default="csi300", choices=["csi300", "csi800"])
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--data-end", default=None)
    parser.add_argument("--price-mode", default="open", choices=["open", "close_fallback"])
    parser.add_argument("--config", default=None)
    parser.add_argument("--top-n", type=int, default=None)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    # ── Config ──
    config_path = args.config or (Path(__file__).resolve().parent.parent / "configs" / "alpha_v1" / f"backtest_{args.universe}.yaml")
    if Path(config_path).exists():
        yaml_config = load_yaml_config(config_path)
        print(f"[Config] Loaded: {config_path}")
    else:
        yaml_config = {}
        if args.config is not None:
            raise FileNotFoundError(f"Config not found: {config_path}")
        print("[Config] No YAML file, using spec defaults")

    resolved = merge_cli_overrides(yaml_config, args)
    config = build_candidate_from_config(resolved)
    exec_cfg = resolved.get("execution", {})
    universe = exec_cfg.get("universe", args.universe)
    price_mode = exec_cfg.get("price_mode", args.price_mode)
    start = exec_cfg.get("start", args.start)
    end = exec_cfg.get("end", args.end)
    data_end = exec_cfg.get("data_end", args.data_end)
    output_dir = Path(exec_cfg.get("output_dir", f"experiments/alpha_v1_candidate_{universe}"))
    ui_reports_dir = Path("experiments/reports")

    output_dir.mkdir(parents=True, exist_ok=True)
    write_resolved_config(resolved, output_dir / "config.resolved.yaml")
    print(f"[Config] Resolved config written: {output_dir / 'config.resolved.yaml'}")

    print("=" * 70)
    print(f"QSYS Alpha V1 — Production Candidate Rolling Backtest ({universe})")
    print("Strategy: qsys_alpha_v1_candidate_blend20_weekly_top20_buffer")
    print("=" * 70)

    # 1. Load data
    frame, clean_features = load_data(universe, start, end, data_end, price_mode)
    frame = compute_trade_flags(frame)
    frame["daily_ret"] = frame.groupby("instrument")["$close"].pct_change()

    # 2. Build windows
    all_dates_dt = [pd.Timestamp(d) for d in sorted(frame["trade_date"].unique())]
    windows = build_trading_day_windows(
        all_dates_dt,
        train_days=config.training.train_days,
        test_days=config.training.test_days,
        step_days=config.training.step_days,
    )
    if end is not None:
        windows = [w for w in windows if w["test_end"] <= end]
    print(f"\n[Windows] {len(windows)} total ({windows[0]['test_start']} ~ {windows[-1]['test_end']})")

    # 3. Pre-compute signals (alpha_v1 strategy)
    print(f"\n{'='*70}")
    print(f"Signal Pre-computation ({len(windows)} windows)")
    print(f"{'='*70}")
    signal_lookup, prediction_rows, signal_rows = precompute_alpha_v1_signals(
        frame, windows, clean_features, config,
    )

    # 4. Run backtest
    print(f"\n{'='*70}")
    print("Running Backtest...")
    print(f"{'='*70}")
    account = Account(init_cash=config.target_cash)
    zc_account = Account(init_cash=config.target_cash)
    matcher = MatchEngine(
        commission=config.cost.commission, stamp_duty=config.cost.stamp_duty,
        min_commission=config.cost.min_commission, slippage=config.cost.slippage,
    )
    zc_matcher = MatchEngine(commission=0.0, stamp_duty=0.0, min_commission=0.0, slippage=0.0)

    all_dates = sorted(frame["trade_date"].unique())
    rebal_dates = get_rebalance_dates(all_dates, config.portfolio.rebalance_freq)

    # Only iterate dates within test-window ranges
    all_test_dates = set()
    for w in windows:
        for d in pd.date_range(w["test_start"], w["test_end"], freq="D"):
            all_test_dates.add(d.strftime("%Y-%m-%d"))
    test_dates = sorted(
        d for d in all_dates
        if d.strftime("%Y-%m-%d") in all_test_dates
        or any(w["test_start"] <= d.strftime("%Y-%m-%d") <= w["test_end"] for w in windows)
    )

    engine = BacktestEngine(account, matcher, zc_account=zc_account, zc_matcher=zc_matcher)
    result = engine.run(
        frame, signal_lookup, rebal_dates, build_rank_weight_portfolio,
        dates=test_dates,
        top_n=config.portfolio.top_n,
        buffer_hold=config.portfolio.buffer_hold,
        buffer_buy=config.portfolio.buffer_buy,
        single_stock_cap=config.portfolio.single_stock_cap,
    )

    total_time = time.time() - t_start

    # 5. Aggregate results
    print(f"\n{'='*70}")
    print("Aggregating Results...")
    print(f"{'='*70}")
    daily_df = result.daily
    trade_df = result.trades

    # Rolling metrics by grouping daily into windows
    rm_rows = []
    if not daily_df.empty:
        # Re-derive window IDs from date ranges matching window test periods
        for w in windows:
            mask = (daily_df["date"] >= w["test_start"]) & (daily_df["date"] <= w["test_end"])
            grp = daily_df[mask]
            if len(grp) > 1:
                eq = grp["equity"].values
                tr = eq[-1] / eq[0] - 1.0
                n_trades = len(trade_df[trade_df["date"].between(w["test_start"], w["test_end"])]) if not trade_df.empty else 0
                rm_rows.append({
                    "window_id": w["window_id"],
                    "test_start": w["test_start"],
                    "test_end": w["test_end"],
                    "total_return": tr,
                    "n_trades": n_trades,
                })
    rolling_metrics = pd.DataFrame(rm_rows) if rm_rows else pd.DataFrame()
    n_windows_completed = len(rolling_metrics)

    perf, _, _ = compute_window_metrics(
        [{"equity": r["equity"], "ret": r["ret"], "npos": r["npos"], "date": r["date"]} for _, r in daily_df.iterrows()],
        [{"fee": r["fee"], "amount": r.get("amount", 0), "price": r["price"]} for _, r in trade_df.iterrows()] if not trade_df.empty else [],
        frame,
    )
    health = build_health_report(daily_df, rolling_metrics, config)

    # Year-by-year
    if not daily_df.empty:
        daily_df["date_dt"] = pd.to_datetime(daily_df["date"])
        print(f"\n{'='*70}")
        print("Year-by-Year Performance")
        print(f"{'='*70}")
        for yr in sorted(daily_df["date_dt"].dt.year.unique()):
            yr_df = daily_df[daily_df["date_dt"].dt.year == yr]
            if len(yr_df) < 5:
                continue
            yr_eq = yr_df["equity"].values
            yr_tr = yr_eq[-1] / yr_eq[0] - 1
            yr_sharpe = np.mean(yr_df["ret"]) / max(np.std(yr_df["ret"]), 1e-10) * np.sqrt(252)
            print(f"  {yr}: Ret={yr_tr:.2%}, Sharpe={yr_sharpe:.2f}")

    # 6. Save outputs
    print(f"\n{'='*70}")
    print("Saving Outputs...")
    print(f"{'='*70}")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "trades").mkdir(exist_ok=True)
    (output_dir / "reports").mkdir(exist_ok=True)
    daily_df.to_csv(output_dir / "daily_equity.csv", index=False)
    print(f"  → {output_dir / 'daily_equity.csv'}")
    if not trade_df.empty:
        trade_df.to_csv(output_dir / "trades" / "trade_log.csv", index=False)
        print(f"  → {output_dir / 'trades' / 'trade_log.csv'}")
    rolling_metrics.to_csv(output_dir / "rolling_metrics.csv", index=False)
    print(f"  → {output_dir / 'rolling_metrics.csv'}")
    with open(output_dir / "reports" / "health_monitor.json", "w") as f:
        json.dump(health, f, indent=2, default=str)

    # 7. Standard artifacts
    print(f"\n{'='*70}")
    print("Standard Artifacts...")
    print(f"{'='*70}")
    manifest = {
        "run_id": f"alpha_v1_candidate_{universe}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "execution_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "backtest", "universe": universe, "strategy": config.strategy_id,
        "strategy_version": config.version, "top_k": config.portfolio.top_n,
        "price_mode": price_mode, "date_range": {"start": start, "end": end, "data_end": data_end},
        "feature_set": f"clean_{len(clean_features)}", "git_sha": _capture_git_sha(),
        "n_windows": len(windows), "windows_completed": n_windows_completed,
    }
    with open(output_dir / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2, default=str)
    print(f"  → {output_dir / 'manifest.json'}")
    if prediction_rows:
        pred_df = pd.DataFrame(prediction_rows)
        try:
            pred_df.to_parquet(output_dir / "predictions.parquet", index=False)
            print(f"  → {output_dir / 'predictions.parquet'} ({len(pred_df)} rows)")
        except Exception:
            pred_df.to_csv(output_dir / "predictions.csv", index=False)
            print(f"  → {output_dir / 'predictions.csv'} ({len(pred_df)} rows)")

    # 8. UI report
    print(f"\n{'='*70}")
    print("Generating UI Report...")
    print(f"{'='*70}")
    run_id = save_ui_report(
        daily_df, rolling_metrics, perf, total_time, config, universe, start, end, data_end,
        price_mode, output_dir, ui_reports_dir, signal_rows, None, feature_count=len(clean_features),
    )

    # 9. Summary
    print(f"\n{'='*70}")
    print("ALPHA V1 — FINAL RESULTS")
    print(f"{'='*70}")
    print(f"  Windows: {n_windows_completed} completed")
    print(f"  Period:  {daily_df['date'].iloc[0] if not daily_df.empty else 'N/A'} ~ {daily_df['date'].iloc[-1] if not daily_df.empty else 'N/A'}")
    print(f"  Returns: {perf['total_ret']*100:.2f}% total, {perf['ann_ret']*100:.2f}% ann")
    print(f"  Vol:     {perf['ann_vol']*100:.2f}% ann")
    print(f"  Sharpe:  {perf['sharpe']:.4f}")
    print(f"  Max DD:  {perf['max_dd']*100:.2f}%")
    print(f"  Calmar:  {perf['calmar']:.4f}")
    print(f"  Excess:  {perf['ex_ann']*100:.2f}% ann, {perf['ex_sharpe']:.4f} Sharpe")
    print(f"  TO:      {perf['ann_to']:.1f}x")
    print(f"  Cost:    ¥{perf['cost']:,.0f}")
    print(f"  Avg Pos: {perf['avg_pos']:.0f}")
    print(f"  WinRate: {perf['win_rate']*100:.1f}%")
    pos_w = int((rolling_metrics["total_return"] > 0).sum()) if not rolling_metrics.empty else 0
    print(f"\n  Window WinRate: {pos_w}/{n_windows_completed} ({pos_w/max(n_windows_completed,1)*100:.1f}%)")
    print(f"\n  Health: {'✅ PASS' if health['healthy'] else '⚠️ ALERTS'}")
    for a in health.get("alerts", []):
        print(f"    [{a['severity']}] {a['metric']} = {a['value']} (threshold: {a['threshold']})")
    if price_mode == "close_fallback":
        print(f"\n  ⚠ Note: ran with --price-mode=close_fallback ($close used where $open unavailable)")
    else:
        print(f"\n  ✓ Price mode: open (executed at $open, fail-fast if missing)")
    print(f"\n  Total time: {total_time:.0f}s")
    print(f"  UI Report: experiments/reports/backtest_{run_id}.json")
    print(f"  UI ID:     {run_id}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
