"""
Backtest Report

Generates structured reports for backtest workflows.
"""

from qsys.reports.base import DEFAULT_REPORT_OUTPUT_DIR, RunReport, ReportStatus, save_report


class BacktestReport:
    """Backtest Report Generator"""
    
    @staticmethod
    def generate(
        start_date: str,
        end_date: str,
        model_info: dict,
        metrics: dict,
        daily_result_path: str = None,
        report_path: str = None,
        top_k: int = None,
        universe: str = None,
        duration_seconds: float = None,
        blockers: list = None,
        notes: list = None,
        experiment_spec: dict | None = None,
    ) -> RunReport:
        """Generate a backtest report"""
        status = ReportStatus.SUCCESS
        if blockers:
            status = ReportStatus.PARTIAL
        
        report = RunReport(
            workflow="backtest",
            signal_date=start_date,
            execution_date=end_date,
            status=status,
            duration_seconds=duration_seconds,
        )
        
        # Set model info
        report.model_info = model_info
        if top_k is not None:
            report.model_info["top_k"] = top_k
        if universe:
            report.model_info["universe"] = universe
        if experiment_spec:
            report.model_info.update({
                "feature_set": experiment_spec.get("feature_set"),
                "model_type": experiment_spec.get("model_type"),
                "label_type": experiment_spec.get("label_type"),
                "strategy_type": experiment_spec.get("strategy_type"),
                "retrain_freq": experiment_spec.get("retrain_freq"),
                "rebalance_mode": experiment_spec.get("rebalance_mode"),
                "rebalance_freq": experiment_spec.get("rebalance_freq"),
                "inference_freq": experiment_spec.get("inference_freq"),
            })
        
        # Add metrics section
        report.add_section(
            name="Performance",
            status=ReportStatus.SUCCESS if status == ReportStatus.SUCCESS else ReportStatus.FAILED,
            metrics=metrics,
        )
        
        # Add blockers and notes
        for blocker in (blockers or []):
            report.add_blocker(blocker)
        for note in (notes or []):
            report.add_note(note)
        
        # Add artifacts
        if daily_result_path:
            report.artifacts["daily_result"] = daily_result_path
        if report_path:
            report.artifacts["backtest_report"] = report_path
        
        return report
    
    @staticmethod
    def from_backtest_result(
        result_df,
        model_path: str,
        start_date: str,
        end_date: str,
        top_k: int = None,
        universe: str = "csi300",
        experiment_spec: dict | None = None,
        **kwargs
    ) -> RunReport:
        """Generate report from backtest result DataFrame"""
        import pandas as pd
        import numpy as np
        
        # Calculate metrics from result
        result_df = result_df.copy()
        result_df['date'] = pd.to_datetime(result_df['date'])
        result_df = result_df.set_index('date')
        result_df['daily_return'] = result_df['total_assets'].pct_change()
        
        returns = result_df['daily_return'].dropna()
        
        if returns.empty or returns.isna().all():
            metrics = {"error": "No valid returns data"}
        else:
            total_return = (1 + returns).prod() - 1
            n_periods = len(returns)
            annual_factor = 252 / n_periods if n_periods > 0 else 1
            annual_return = (1 + total_return) ** annual_factor - 1
            annual_vol = returns.std() * np.sqrt(252)
            sharpe = annual_return / annual_vol if annual_vol > 0 else 0
            
            cum_returns = (1 + returns).cumprod()
            running_max = cum_returns.expanding().max()
            drawdown = (cum_returns - running_max) / running_max
            max_drawdown = drawdown.min()
            
            metrics = {
                "total_return": f"{total_return:.2%}",
                "annual_return": f"{annual_return:.2%}",
                "annual_vol": f"{annual_vol:.2%}",
                "sharpe": f"{sharpe:.3f}",
                "max_drawdown": f"{max_drawdown:.2%}",
                "trade_count": int(result_df['trade_count'].sum()) if 'trade_count' in result_df.columns else 0,
                "days": n_periods,
            }
        
        model_info = {
            "model_path": model_path,
            "model_name": model_path.split("/")[-1] if "/" in model_path else model_path,
        }
        
        return BacktestReport.generate(
            start_date=start_date,
            end_date=end_date,
            model_info=model_info,
            metrics=metrics,
            top_k=top_k,
            universe=universe,
            experiment_spec=experiment_spec,
            **kwargs
        )
    
    @staticmethod
    def save(report: RunReport, output_dir: str = str(DEFAULT_REPORT_OUTPUT_DIR)) -> str:
        """Save the report to file"""
        return save_report(report, output_dir)
