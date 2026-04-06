"""
Daily Operations Report

Generates structured reports for pre-open and post-close workflows.
"""

from pathlib import Path
from typing import Optional

from qsys.reports.base import DEFAULT_REPORT_OUTPUT_DIR, RunReport, ReportStatus, save_report


def _validate_plan_summary_dates(plan_summary: dict | None, *, account_name: str, signal_date: str, execution_date: str) -> dict:
    summary = dict(plan_summary or {})
    expected = {
        "signal_date": signal_date,
        "execution_date": execution_date,
    }
    for field, expected_value in expected.items():
        actual = summary.get(field)
        if actual is None:
            continue
        if str(actual) != str(expected_value):
            raise ValueError(
                f"Refusing to build {account_name} report section: {field}={actual} does not match intended {expected_value}"
            )
    return summary


class DailyOpsReport:
    """
    Daily Operations Report Generator
    
    Handles both:
    - Pre-open (run_daily_trading): Generate trading plans
    - Post-close (run_post_close): Reconcile real vs shadow
    """
    
    @staticmethod
    def generate_pre_open_report(
        signal_date: str,
        execution_date: str,
        data_status: dict,
        model_info: dict,
        shadow_plan_summary: dict,
        real_plan_summary: dict,
        signal_quality_summary: dict | None = None,
        duration_seconds: float = None,
        blockers: list = None,
        notes: list = None,
    ) -> RunReport:
        """Generate a pre-open daily trading report"""
        shadow_plan_summary = _validate_plan_summary_dates(
            shadow_plan_summary,
            account_name="shadow",
            signal_date=signal_date,
            execution_date=execution_date,
        )
        real_plan_summary = _validate_plan_summary_dates(
            real_plan_summary,
            account_name="real",
            signal_date=signal_date,
            execution_date=execution_date,
        )

        # Determine overall status based on blockers and plan anomalies
        has_blockers = bool(blockers)
        shadow_empty = not shadow_plan_summary or shadow_plan_summary.get("trades", 0) == 0
        real_empty = not real_plan_summary or real_plan_summary.get("trades", 0) == 0
        
        # Status logic: failed if blockers, partial if empty plans
        if has_blockers:
            status = ReportStatus.PARTIAL
        elif shadow_empty and real_empty:
            status = ReportStatus.SKIPPED  # Both empty = nothing to do
        elif shadow_empty or real_empty:
            status = ReportStatus.PARTIAL  # One empty = partial
        else:
            status = ReportStatus.SUCCESS
        
        report = RunReport(
            workflow="daily_ops_pre_open",
            signal_date=signal_date,
            execution_date=execution_date,
            status=status,
            duration_seconds=duration_seconds,
        )
        
        # Set data and model info
        report.data_status = data_status
        report.model_info = model_info
        
        # Add plan summaries with appropriate status
        if shadow_empty:
            shadow_status = ReportStatus.SKIPPED
            if not shadow_plan_summary:
                shadow_plan_summary = {"status": "no_plan", "trades": 0, "symbols": []}
            else:
                shadow_plan_summary["status"] = "empty_plan"
        else:
            shadow_status = ReportStatus.SUCCESS
            
        report.add_section(
            name="Shadow Account Plan",
            status=shadow_status,
            metrics=shadow_plan_summary,
        )
        
        if real_empty:
            real_status = ReportStatus.SKIPPED
            if not real_plan_summary:
                real_plan_summary = {"status": "no_plan", "trades": 0, "symbols": []}
            else:
                real_plan_summary["status"] = "empty_plan"
        else:
            real_status = ReportStatus.SUCCESS
            
        report.add_section(
            name="Real Account Plan", 
            status=real_status,
            metrics=real_plan_summary,
        )

        if signal_quality_summary:
            signal_status = ReportStatus.SUCCESS
            if signal_quality_summary.get("data_quality_status") in {"failed", "partial"}:
                signal_status = ReportStatus.PARTIAL
            report.add_section(
                name="Signal Quality Gate",
                status=signal_status,
                metrics=signal_quality_summary,
            )
        
        # Add anomaly detection notes
        if data_status.get("health_ok") is False:
            report.add_note("⚠️ Data health check reported issues")
        if data_status.get("aligned") is False:
            report.add_note("⚠️ Raw/qlib data not aligned")
        
        # Add blockers and notes
        for blocker in (blockers or []):
            report.add_blocker(blocker)
        for note in (notes or []):
            report.add_note(note)
        
        return report
    
    @staticmethod
    def generate_post_close_report(
        signal_date: str,
        execution_date: str,
        data_status: dict,
        model_info: dict,
        reconciliation_summary: dict,
        signal_quality_summary: dict | None = None,
        real_trades_count: int = 0,
        position_gaps_count: int = 0,
        duration_seconds: float = None,
        blockers: list = None,
        notes: list = None,
    ) -> RunReport:
        """Generate a post-close reconciliation report"""
        report = RunReport(
            workflow="daily_ops_post_close",
            signal_date=signal_date,
            execution_date=execution_date,
            status=ReportStatus.SUCCESS if not blockers else ReportStatus.PARTIAL,
            duration_seconds=duration_seconds,
        )
        
        report.data_status = data_status
        report.model_info = model_info
        
        report.add_section(
            name="Reconciliation",
            status=ReportStatus.SUCCESS,
            metrics=reconciliation_summary,
            details={
                "real_trades_count": real_trades_count,
                "position_gaps_count": position_gaps_count,
            },
        )

        if signal_quality_summary:
            signal_status = ReportStatus.SUCCESS
            if signal_quality_summary.get("status") in {"failed", "missing_plan"}:
                signal_status = ReportStatus.PARTIAL
            report.add_section(
                name="Signal Quality",
                status=signal_status,
                metrics=signal_quality_summary,
            )
        
        for blocker in (blockers or []):
            report.add_blocker(blocker)
        for note in (notes or []):
            report.add_note(note)
        
        return report
    
    @staticmethod
    def save(report: RunReport, output_dir: str = str(DEFAULT_REPORT_OUTPUT_DIR)) -> str:
        """Save the report to file"""
        return save_report(report, output_dir)
