"""
Training Report

Generates structured reports for model training workflows.
"""

from qsys.reports.base import DEFAULT_REPORT_OUTPUT_DIR, RunReport, ReportStatus, save_report


class TrainingReport:
    """Training Report Generator"""
    
    @staticmethod
    def generate(
        signal_date: str,
        data_status: dict,
        model_info: dict,
        training_metrics: dict,
        feature_count: int = 0,
        sample_count: int = 0,
        duration_seconds: float = None,
        backtest_info: dict = None,
        blockers: list = None,
        notes: list = None,
    ) -> RunReport:
        """Generate a training report"""
        status = ReportStatus.SUCCESS
        if blockers:
            status = ReportStatus.FAILED if any("failed" in b.lower() for b in blockers) else ReportStatus.PARTIAL
        
        report = RunReport(
            workflow="train",
            signal_date=signal_date,
            status=status,
            duration_seconds=duration_seconds,
        )
        
        report.data_status = data_status
        report.model_info = model_info
        report.model_info["feature_count"] = feature_count
        report.model_info["sample_count"] = sample_count
        
        # Training section
        report.add_section(
            name="Training",
            status=ReportStatus.SUCCESS if status == ReportStatus.SUCCESS else ReportStatus.FAILED,
            metrics=training_metrics,
        )
        
        # Backtest section (optional)
        if backtest_info:
            report.add_section(
                name="Post-Train Backtest",
                status=ReportStatus.SUCCESS,
                metrics=backtest_info,
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
