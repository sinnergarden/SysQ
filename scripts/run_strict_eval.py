#!/usr/bin/env python3
"""
Strict factor comparison: Alpha158 baseline vs Extended features.
- Train window should avoid test overlap.
- Main test window: 2025-01-01 to latest available.
- 2026 YTD slice: style-shift inspection.
- top_k=5 for all backtests.
"""
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

import click
import pandas as pd
import numpy as np
import qlib

from qsys.config import cfg
from qsys.utils.logger import log

YTD_START = "2026-01-01"


def calculate_metrics(returns_series: pd.Series):
    if returns_series.empty or returns_series.isna().all():
        return {}
    total_return = (1 + returns_series).prod() - 1
    n_periods = len(returns_series)
    annual_factor = 252 / n_periods if n_periods > 0 else 1
    annual_return = (1 + total_return) ** annual_factor - 1
    annual_vol = returns_series.std() * np.sqrt(252)
    sharpe = annual_return / annual_vol if annual_vol > 0 else 0
    cum_returns = (1 + returns_series).cumprod()
    running_max = cum_returns.expanding().max()
    drawdown = (cum_returns - running_max) / running_max
    max_drawdown = drawdown.min()
    return {
        "total_return": total_return,
        "annual_return": annual_return,
        "annual_vol": annual_vol,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
    }


def run_backtest_for_model(model_path, start_date, end_date, top_k=5):
    from qsys.backtest import BacktestEngine
    from qsys.strategy.engine import StrategyEngine

    engine = BacktestEngine(model_path=model_path, start_date=start_date, end_date=end_date)
    engine.strategy = StrategyEngine(top_k=top_k, method='equal_weight')
    result = engine.run()
    result['date'] = pd.to_datetime(result['date'])
    result = result.set_index('date')
    result['daily_return'] = result['total_assets'].pct_change()
    metrics = calculate_metrics(result['daily_return'].dropna())
    metrics['trade_count'] = result['trade_count'].sum()
    return metrics


@click.command()
@click.option('--test_start', default='2025-01-01')
@click.option('--test_end', default='2026-03-20')
@click.option('--top_k', default=5)
def main(test_start, test_end, top_k):
    qlib.init(provider_uri=str(cfg.get_path('qlib_bin')))
    root_path = cfg.get_path("root")
    baseline_path = root_path / "models" / "qlib_lgbm"
    extended_path = root_path / "models" / "qlib_lgbm_extended"

    results = []
    for label, start_date in [("2025-01~latest", test_start), ("2026 YTD", YTD_START)]:
        for model_name, model_path in [("Baseline", baseline_path), ("Extended", extended_path)]:
            metrics = run_backtest_for_model(model_path, start_date, test_end, top_k)
            results.append({
                "Period": label,
                "Model": model_name,
                "Total Return": metrics.get('total_return', 0),
                "Annual Return": metrics.get('annual_return', 0),
                "Sharpe": metrics.get('sharpe', 0),
                "Max DD": metrics.get('max_drawdown', 0),
                "Trades": metrics.get('trade_count', 0),
            })

    summary_df = pd.DataFrame(results)
    output_path = project_root / "experiments" / "strict_eval_results.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary_df.to_csv(output_path, index=False)
    log.info("\n" + summary_df.to_string(index=False))
    log.info(f"Results saved to {output_path}")


if __name__ == '__main__':
    main()
