"""
Strict Evaluation Report

Generates structured reports for strict evaluation workflows.
"""

from typing import List, Optional

from qsys.reports.base import RunReport, ReportStatus, save_report


class StrictEvalReport:
    """Strict Evaluation Report Generator"""
    
    @staticmethod
    def generate(
        baseline_model_path: str,
        extended_model_path: str,
        end_date: str,
        results: List[dict],
        top_k: int = 5,
        duration_seconds: float = None,
        blockers: list = None,
        notes: list = None,
    ) -> RunReport:
        """Generate a strict evaluation report"""
        status = ReportStatus.SUCCESS
        if blockers:
            status = ReportStatus.PARTIAL
        
        report = RunReport(
            workflow="strict_eval",
            execution_date=end_date,
            status=status,
            duration_seconds=duration_seconds,
        )
        
        # Set model info for both models
        report.model_info = {
            "baseline_model": baseline_model_path,
            "extended_model": extended_model_path,
            "top_k": top_k,
        }
        
        # Add baseline results section
        baseline_results = [r for r in results if r.get("model") == "Baseline"]
        if baseline_results:
            for r in baseline_results:
                period = r.get("period", "unknown")
                metrics = {
                    "annual_return": r.get("annual_return", "N/A"),
                    "sharpe": r.get("sharpe", "N/A"),
                    "max_drawdown": r.get("max_drawdown", "N/A"),
                    "trade_count": r.get("trade_count", 0),
                }
                report.add_section(
                    name=f"Baseline ({period})",
                    status=ReportStatus.SUCCESS,
                    metrics=metrics,
                )
        
        # Add extended results section
        extended_results = [r for r in results if r.get("model") == "Extended"]
        if extended_results:
            for r in extended_results:
                period = r.get("period", "unknown")
                metrics = {
                    "annual_return": r.get("annual_return", "N/A"),
                    "sharpe": r.get("sharpe", "N/A"),
                    "max_drawdown": r.get("max_drawdown", "N/A"),
                    "trade_count": r.get("trade_count", 0),
                }
                report.add_section(
                    name=f"Extended ({period})",
                    status=ReportStatus.SUCCESS,
                    metrics=metrics,
                )
        
        # Add comparison notes if both models exist
        if baseline_results and extended_results:
            # Calculate improvement
            baseline_return = baseline_results[0].get("annual_return", 0)
            extended_return = extended_results[0].get("annual_return", 0)
            if baseline_return and extended_return and baseline_return != "N/A":
                try:
                    improvement = ((extended_return - baseline_return) / abs(baseline_return)) * 100 if baseline_return != 0 else 0
                    report.add_note(f"Annual return improvement: {improvement:+.1f}%")
                except (TypeError, ZeroDivisionError):
                    pass
        
        # Add blockers and notes
        for blocker in (blockers or []):
            report.add_blocker(blocker)
        for note in (notes or []):
            report.add_note(note)
        
        return report
    
    @staticmethod
    def from_evaluation_report(eval_report, baseline_path: str, extended_path: str, **kwargs) -> RunReport:
        """Generate report from qsys.evaluation.evaluator.EvaluationReport"""
        baseline_meta = kwargs.pop("baseline_meta", None) or {}
        extended_meta = kwargs.pop("extended_meta", None) or {}
        results = []
        
        for r in eval_report.results:
            results.append({
                "period": r.period,
                "model": r.model_name,
                "annual_return": r.metrics.annual_return,
                "sharpe": r.metrics.sharpe,
                "max_drawdown": r.metrics.max_drawdown,
                "trade_count": r.metrics.trade_count,
                "total_return": r.metrics.total_return,
            })
        
        # Get end date from first result or use latest
        end_date = results[0].get("end_date", "N/A") if results else "N/A"
        
        report = StrictEvalReport.generate(
            baseline_model_path=baseline_path,
            extended_model_path=extended_path,
            end_date=end_date,
            results=results,
            **kwargs
        )
        if baseline_meta.get("feature_set_name"):
            report.model_info["baseline_feature_set"] = baseline_meta.get("feature_set_name")
        if extended_meta.get("feature_set_name"):
            report.model_info["extended_feature_set"] = extended_meta.get("feature_set_name")
        return report
    
    @staticmethod
    def save(report: RunReport, output_dir: str = "data/reports") -> str:
        """Save the report to file"""
        return save_report(report, output_dir)
