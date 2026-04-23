#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import click
import pandas as pd
from qlib.data import D
from tqdm import tqdm

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.backtest import BacktestEngine
from qsys.data.adapter import QlibAdapter
from qsys.evaluation.exposure import compute_portfolio_exposure_diagnostics
from qsys.evaluation.signal_metrics import compute_group_returns, compute_signal_metrics
from qsys.research.mainline import MAINLINE_OBJECTS
from qsys.research.rolling import build_rolling_windows, compute_window_metrics, snapshot_train_window
from qsys.research.signal import to_signal_frame
from qsys.research.strategy_tuning import (
    STRATEGY_VARIANTS,
    build_strategy_summary,
    build_window_stability_summary,
    markdown_table,
    summarize_variant_metrics,
    write_df,
    write_text,
)
from scripts.run_backtest import build_backtest_lineage, load_training_snapshot

OBJECTS = ["feature_173", "feature_254_trimmed"]


def _variant_model_path(name: str) -> Path:
    return project_root / "data" / "models" / MAINLINE_OBJECTS[name].model_name


def _should_rebalance(trade_dates: list[str], idx: int, rebalance_mode: str) -> bool:
    if rebalance_mode == "daily":
        return True
    if rebalance_mode == "weekly":
        return idx % 5 == 0
    raise ValueError(f"Unknown rebalance_mode: {rebalance_mode}")


def run_strategy_variant(model_path: Path, *, start: str, end: str, top_k: int, rebalance_mode: str, turnover_buffer: float) -> tuple[pd.DataFrame, dict, dict]:
    engine = BacktestEngine(
        model_path=model_path,
        universe="csi300",
        start_date=start,
        end_date=end,
        top_k=top_k,
        strategy_type="rank_topk",
        strategy_params={"min_trade_buffer_ratio": turnover_buffer},
    )
    engine.prepare()
    instruments = D.instruments(engine.universe)

    all_features = QlibAdapter().get_features(
        instruments=instruments,
        fields=engine.signal_gen.model.feature_config,
        start_time=engine.start_date,
        end_time=engine.end_date,
    )
    engine.validate_data(all_features, "Features")
    all_scores = engine.signal_gen.predict(all_features)
    if not all_scores.empty and all_scores.index.names == ["instrument", "datetime"]:
        all_scores = all_scores.swaplevel().sort_index()

    price_fields = ["$close", "$open", "$factor", "$paused", "$high_limit", "$low_limit"]
    all_market_data = QlibAdapter().get_features(instruments, price_fields, start_time=engine.start_date, end_time=engine.end_date)
    all_market_data.columns = ["close", "open", "factor", "is_suspended", "limit_up", "limit_down"]
    engine.validate_data(all_market_data, "Market Data")
    all_market_data["is_suspended"] = all_market_data["is_suspended"].fillna(0).astype(bool)
    all_market_data["is_limit_up"] = False
    all_market_data["is_limit_down"] = False
    mask_up = all_market_data["limit_up"] > 0.01
    if mask_up.any():
        all_market_data.loc[mask_up, "is_limit_up"] = all_market_data.loc[mask_up, "close"] >= all_market_data.loc[mask_up, "limit_up"]
    mask_down = all_market_data["limit_down"] > 0.01
    if mask_down.any():
        all_market_data.loc[mask_down, "is_limit_down"] = all_market_data.loc[mask_down, "close"] <= all_market_data.loc[mask_down, "limit_down"]
    if all_market_data.index.names == ["instrument", "datetime"]:
        all_market_data = all_market_data.swaplevel().sort_index()

    history = []
    trade_logs = []
    prior_target_weights: dict[str, float] = {}
    for idx, date in enumerate(tqdm(engine.trade_dates, desc=f"{model_path.name}:{top_k}:{rebalance_mode}:{turnover_buffer}")):
        ts_date = pd.Timestamp(date)
        try:
            scores = all_scores.loc[ts_date]
            market_data = all_market_data.loc[ts_date]
        except KeyError:
            continue
        if scores.empty or market_data.empty:
            continue

        signal_frame = to_signal_frame(scores)
        current_prices = market_data["close"].to_dict()
        selected_scores = engine.strategy.select_target_scores(engine.strategy._apply_soft_filters(signal_frame, market_data))
        raw_weights = engine.strategy._apply_risk_constraints(engine.strategy._calculate_weights(selected_scores)).to_dict() if not selected_scores.empty else {}

        if not _should_rebalance(engine.trade_dates, idx, rebalance_mode):
            target_weights = prior_target_weights
        else:
            target_weights = raw_weights
            prior_target_weights = dict(raw_weights)

        if target_weights:
            selection_rows = []
            for rank, (instrument, score) in enumerate(selected_scores.items(), start=1):
                selection_rows.append({
                    "date": date,
                    "instrument": instrument,
                    "signal_value": float(score),
                    "target_weight": float(target_weights.get(instrument, 0.0)),
                    "selected_rank": rank,
                })
            if selection_rows:
                engine.last_selection_daily = pd.concat([engine.last_selection_daily, pd.DataFrame(selection_rows)], ignore_index=True)

        orders = engine.order_gen.generate_orders(target_weights, engine.account, current_prices)
        trades = engine.matcher.match(orders, engine.account, market_data, current_prices)
        for t in trades:
            order = t.get("order", {})
            trade_logs.append({
                "date": date,
                "symbol": order.get("symbol"),
                "side": order.get("side"),
                "target_weight": target_weights.get(order.get("symbol"), 0.0),
                "filled_amount": t.get("filled_amount", 0),
                "deal_price": t.get("deal_price", 0.0),
                "fee": t.get("fee", 0.0),
                "status": t.get("status"),
                "reason": t.get("reason", ""),
            })

        daily_fee = sum(t.get("fee", 0.0) for t in trades if t.get("status") == "filled")
        daily_turnover = sum(t.get("filled_amount", 0) * t.get("deal_price", 0.0) for t in trades if t.get("status") == "filled")
        engine.account.settlement()
        total_assets = engine.account.get_total_equity(current_prices)
        engine.account.record_daily(date, total_assets)
        history.append({
            "date": date,
            "total_assets": total_assets,
            "cash": engine.account.cash,
            "position_count": len(engine.account.positions),
            "trade_count": len([t for t in trades if t.get("status") == "filled"]),
            "daily_fee": daily_fee,
            "daily_turnover": daily_turnover,
        })

    result = pd.DataFrame(history)
    signal_panel = engine._build_signal_panel(all_scores, all_market_data)
    signal_metrics = compute_signal_metrics(signal_panel, label_horizon=engine.label_horizon)
    engine.last_group_returns = compute_group_returns(signal_panel, label_horizon=engine.label_horizon)
    exposure_panel = engine._build_exposure_panel(instruments)
    engine.last_exposure_summary, engine.last_exposure_timeseries = compute_portfolio_exposure_diagnostics(engine.last_selection_daily, exposure_panel, top_k=top_k)
    return result, signal_metrics, {"turnover_buffer": turnover_buffer, "rebalance_mode": rebalance_mode, "top_k": top_k}


@click.command(name="run_mainline_strategy_tuning")
@click.option("--start", default="2025-01-02")
@click.option("--end", default="2026-03-20")
@click.option("--output_dir", default="experiments/mainline_strategy_tuning")
def main(start: str, end: str, output_dir: str) -> None:
    out_root = (project_root / output_dir).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    summary_rows = []
    stability_rows = []
    variant_rows_dir = out_root / "rolling"
    variant_rows_dir.mkdir(parents=True, exist_ok=True)

    for object_name in OBJECTS:
        model_path = _variant_model_path(object_name)
        snapshot = load_training_snapshot(model_path)
        train_start, train_end = snapshot_train_window(snapshot)
        windows = build_rolling_windows(start=start, end=end, train_start=train_start, train_end=train_end)
        for variant in STRATEGY_VARIANTS:
            variant_name = variant["strategy_variant"]
            metrics_path = variant_rows_dir / object_name / f"{variant_name}.csv"
            metrics_path.parent.mkdir(parents=True, exist_ok=True)
            existing = pd.read_csv(metrics_path) if metrics_path.exists() else pd.DataFrame()
            done = set(existing.get("window_id", pd.Series(dtype=str)).astype(str).tolist()) if not existing.empty else set()
            rows = existing.to_dict(orient="records") if not existing.empty else []
            if len(done) == len(windows):
                metrics = existing.copy()
                summary = summarize_variant_metrics(metrics)
                summary_rows.append({
                    "mainline_object_name": object_name,
                    "strategy_variant": variant_name,
                    "top_k": variant["top_k"],
                    "rebalance_mode": variant["rebalance_mode"],
                    "turnover_buffer": variant["turnover_buffer"],
                    **summary,
                })
                stability_rows.append(build_window_stability_summary(object_name, variant_name, metrics))
                continue
            for window in windows:
                if window.window_id in done:
                    continue
                result, signal_metrics, extra = run_strategy_variant(
                    model_path,
                    start=window.test_start,
                    end=window.test_end,
                    top_k=variant["top_k"],
                    rebalance_mode=variant["rebalance_mode"],
                    turnover_buffer=variant["turnover_buffer"],
                )
                spec = type("Spec", (), {
                    "mainline_object_name": object_name,
                    "bundle_id": MAINLINE_OBJECTS[object_name].bundle_id,
                    "legacy_feature_set_alias": MAINLINE_OBJECTS[object_name].legacy_feature_set_alias,
                })()
                metric = compute_window_metrics(spec=spec, window=window, daily_result=result, signal_metrics=signal_metrics)
                metric.update({
                    "strategy_variant": variant_name,
                    "top_k": variant["top_k"],
                    "rebalance_mode": variant["rebalance_mode"],
                    "turnover_buffer": variant["turnover_buffer"],
                })
                rows.append(metric)
                pd.DataFrame(rows).to_csv(metrics_path, index=False)
            metrics = pd.DataFrame(rows)
            summary = summarize_variant_metrics(metrics)
            summary_rows.append({
                "mainline_object_name": object_name,
                "strategy_variant": variant_name,
                "top_k": variant["top_k"],
                "rebalance_mode": variant["rebalance_mode"],
                "turnover_buffer": variant["turnover_buffer"],
                **summary,
            })
            stability_rows.append(build_window_stability_summary(object_name, variant_name, metrics))

    summary_df = build_strategy_summary(summary_rows)
    stability_df = pd.DataFrame(stability_rows)
    write_df(out_root / "strategy_tuning_summary.csv", summary_df)
    write_text(out_root / "strategy_tuning_summary.md", "# Strategy tuning summary\n\n" + markdown_table(summary_df))
    write_df(out_root / "window_stability_summary.csv", stability_df)
    write_text(out_root / "window_stability_summary.md", "# Window stability summary\n\n" + markdown_table(stability_df))
    print(f"strategy_tuning_summary={out_root / 'strategy_tuning_summary.csv'}")
    print(f"window_stability_summary={out_root / 'window_stability_summary.csv'}")


if __name__ == "__main__":
    main()
