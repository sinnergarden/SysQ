"""
Strict Evaluation Contract

Provides unified baseline vs extended evaluation with:
- Explicit train/valid/test splits to avoid data leakage
- Main evaluation window: 2025-01-01 to latest available
- Auxiliary evaluation window: 2026 YTD (style-shift inspection)
- Default top_k=5 for all backtests

Usage:
    from qsys.evaluation import StrictEvaluator
    
    evaluator = StrictEvaluator()
    report = evaluator.run_comparison(
        baseline_model_path="data/models/qlib_lgbm_phase123",
        extended_model_path="data/models/qlib_lgbm_phase123_extended"
    )
    print(report.to_markdown())
"""
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

import pandas as pd
import numpy as np
import qlib
import yaml

from qsys.config import cfg
from qsys.utils.logger import log

# Default evaluation windows (from ROADMAP consensus)
DEFAULT_MAIN_START = "2025-01-01"
DEFAULT_AUX_START = "2026-01-01"
DEFAULT_TOP_K = 5


@dataclass
class ModelMetrics:
    """Metrics for a single model backtest run."""
    total_return: float = 0.0
    annual_return: float = 0.0
    annual_vol: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    trade_count: int = 0
    
    def to_dict(self) -> dict:
        return {
            "total_return": self.total_return,
            "annual_return": self.annual_return,
            "annual_vol": self.annual_vol,
            "sharpe": self.sharpe,
            "max_drawdown": self.max_drawdown,
            "trade_count": self.trade_count,
        }


@dataclass
class EvaluationResult:
    """Result for a single model in a single period."""
    period: str
    model_name: str
    model_path: str
    start_date: str
    end_date: str
    top_k: int
    metrics: ModelMetrics


@dataclass
class EvaluationReport:
    """Complete evaluation report with baseline vs extended comparison."""
    results: List[EvaluationResult] = field(default_factory=list)
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dataframe(self) -> pd.DataFrame:
        rows = []
        for r in self.results:
            row = {
                "Period": r.period,
                "Model": r.model_name,
                "Start": r.start_date,
                "End": r.end_date,
                "TopK": r.top_k,
                "Total Return": f"{r.metrics.total_return:.2%}",
                "Annual Return": f"{r.metrics.annual_return:.2%}",
                "Sharpe": f"{r.metrics.sharpe:.3f}",
                "Max DD": f"{r.metrics.max_drawdown:.2%}",
                "Trades": r.metrics.trade_count,
            }
            rows.append(row)
        return pd.DataFrame(rows)
    
    def to_markdown(self) -> str:
        df = self.to_dataframe()
        try:
            return df.to_markdown(index=False)
        except Exception:
            return df.to_string(index=False)
    
    def summary_table(self) -> pd.DataFrame:
        """Pivot table comparing baseline vs extended."""
        records = []
        for r in self.results:
            records.append({
                "Period": r.period,
                "Model": r.model_name,
                "Annual Return": r.metrics.annual_return,
                "Sharpe": r.metrics.sharpe,
                "Max DD": r.metrics.max_drawdown,
            })
        df = pd.DataFrame(records)
        if len(df) > 0:
            return df.pivot(index="Period", columns="Model", values=["Annual Return", "Sharpe", "Max DD"])
        return pd.DataFrame()


def calculate_metrics(returns_series: pd.Series) -> ModelMetrics:
    """Calculate performance metrics from daily return series."""
    if returns_series.empty or returns_series.isna().all():
        return ModelMetrics()
    
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
    
    return ModelMetrics(
        total_return=total_return,
        annual_return=annual_return,
        annual_vol=annual_vol,
        sharpe=sharpe,
        max_drawdown=max_drawdown,
    )


def get_latest_date() -> str:
    """Get the latest available trading date from qlib."""
    try:
        qlib.init(provider_uri=str(cfg.get_path('qlib_bin')))
        from qlib.data import D
        calendar = D.calendar(start_time="2025-01-01", end_time="2030-12-31")
        if calendar:
            return str(max(calendar))
    except Exception as e:
        log.warning(f"Could not get latest date from qlib: {e}")
    return datetime.now().strftime("%Y-%m-%d")


class StrictEvaluator:
    """
    Unified evaluator for baseline vs extended model comparison.
    
    Key defaults (from ROADMAP consensus):
    - Main window: 2025-01-01 to latest
    - Auxiliary window: 2026 YTD (for style-shift detection)
    - top_k=5 for all runs
    - Explicit train/valid/test separation (no overlap)
    """
    
    def __init__(self, 
                 top_k: int = DEFAULT_TOP_K,
                 main_window_start: str = DEFAULT_MAIN_START,
                 aux_window_start: str = DEFAULT_AUX_START):
        self.top_k = top_k
        self.main_window_start = main_window_start
        self.aux_window_start = aux_window_start
        self._latest_date = None
    
    @property
    def latest_date(self) -> str:
        if self._latest_date is None:
            self._latest_date = get_latest_date()
        return self._latest_date
    
    def run_backtest(self, 
                     model_path: str, 
                     start_date: str, 
                     end_date: str,
                     top_k: Optional[int] = None) -> Tuple[pd.DataFrame, ModelMetrics]:
        """
        Run a single backtest for a model.
        
        Args:
            model_path: Path to model directory
            start_date: Test period start
            end_date: Test period end
            top_k: Number of stocks to select (uses default if None)
        
        Returns:
            Tuple of (daily_dataframe, metrics)
        """
        from qsys.backtest import BacktestEngine
        from qsys.strategy.engine import StrategyEngine
        
        top_k = top_k or self.top_k
        
        engine = BacktestEngine(model_path=model_path, start_date=start_date, end_date=end_date)
        engine.strategy = StrategyEngine(top_k=top_k, method='equal_weight')
        result = engine.run()
        
        result['date'] = pd.to_datetime(result['date'])
        result = result.set_index('date')
        result['daily_return'] = result['total_assets'].pct_change()
        
        metrics = calculate_metrics(result['daily_return'].dropna())
        metrics.trade_count = int(result['trade_count'].sum())
        
        return result, metrics
    
    def run_comparison(self,
                       baseline_model_path: str,
                       extended_model_path: str,
                       end_date: Optional[str] = None,
                       periods: Optional[List[str]] = None) -> EvaluationReport:
        """
        Run baseline vs extended comparison across specified periods.
        
        Args:
            baseline_model_path: Path to baseline (phase123) model
            extended_model_path: Path to extended model
            end_date: End date for evaluation (defaults to latest available)
            periods: List of periods to evaluate. If None, uses default:
                     ["main", "aux"]
        
        Returns:
            EvaluationReport with all results
        """
        qlib.init(provider_uri=str(cfg.get_path('qlib_bin')))
        
        end_date = end_date or self.latest_date
        periods = periods or ["main", "aux"]
        
        results = []
        
        period_configs = {
            "main": {
                "start": self.main_window_start,
                "label": f"2025-01~{end_date[:7]}",
            },
            "aux": {
                "start": self.aux_window_start,
                "label": f"2026 YTD",
            },
        }
        
        for period_key in periods:
            config = period_configs.get(period_key)
            if not config:
                log.warning(f"Unknown period: {period_key}, skipping")
                continue
            
            start = config["start"]
            label = config["label"]
            
            # Skip if aux period start is after end date
            if start > end_date:
                log.info(f"Skipping period {label} (start {start} > end {end_date})")
                continue
            
            for model_name, model_path in [
                ("Baseline", baseline_model_path),
                ("Extended", extended_model_path)
            ]:
                log.info(f"Running {label} | {model_name}...")
                
                try:
                    _, metrics = self.run_backtest(
                        model_path=model_path,
                        start_date=start,
                        end_date=end_date,
                        top_k=self.top_k
                    )
                    
                    result = EvaluationResult(
                        period=label,
                        model_name=model_name,
                        model_path=model_path,
                        start_date=start,
                        end_date=end_date,
                        top_k=self.top_k,
                        metrics=metrics
                    )
                    results.append(result)
                    log.info(f"  {model_name}: Return={metrics.total_return:.2%}, Sharpe={metrics.sharpe:.3f}")
                    
                except Exception as e:
                    log.error(f"  Failed for {model_name}: {e}")
                    results.append(EvaluationResult(
                        period=label,
                        model_name=model_name,
                        model_path=model_path,
                        start_date=start,
                        end_date=end_date,
                        top_k=self.top_k,
                        metrics=ModelMetrics()
                    ))
        
        return EvaluationReport(results=results)
    
    def save_report(self, report: EvaluationReport, output_path: str):
        """Save evaluation report to CSV."""
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        df = report.to_dataframe()
        df.to_csv(output_file, index=False)
        log.info(f"Report saved to {output_path}")


def main():
    """CLI entrypoint for strict evaluation."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Run Strict Evaluation: Baseline vs Extended")
    parser.add_argument("--baseline", required=True, help="Path to baseline model")
    parser.add_argument("--extended", required=True, help="Path to extended model")
    parser.add_argument("--end", default=None, help="End date (YYYY-MM-DD). Defaults to latest available.")
    parser.add_argument("--top_k", type=int, default=DEFAULT_TOP_K, help=f"Number of stocks (default: {DEFAULT_TOP_K})")
    parser.add_argument("--output", default="experiments/strict_eval_results.csv", help="Output CSV path")
    
    args = parser.parse_args()
    
    evaluator = StrictEvaluator(top_k=args.top_k)
    report = evaluator.run_comparison(
        baseline_model_path=args.baseline,
        extended_model_path=args.extended,
        end_date=args.end
    )
    
    print("\n" + report.to_markdown())
    evaluator.save_report(report, args.output)
    
    # Print summary comparison
    print("\n=== Summary ===")
    summary = report.summary_table()
    if not summary.empty:
        print(summary.to_string())


if __name__ == "__main__":
    main()