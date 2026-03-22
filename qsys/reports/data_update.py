"""
Data Update Report

Generates structured reports for data update workflows.
"""

from typing import Optional

from qsys.reports.base import RunReport, ReportStatus, save_report


class DataUpdateReport:
    """Data Update Report Generator"""
    
    @staticmethod
    def generate(
        raw_latest: str,
        qlib_latest: str,
        aligned: bool,
        gap_days: int = None,
        collector_stats: dict = None,
        adapter_stats: dict = None,
        duration_seconds: float = None,
        blockers: list = None,
        notes: list = None,
    ) -> RunReport:
        """Generate a data update report"""
        status = ReportStatus.SUCCESS
        if blockers:
            status = ReportStatus.PARTIAL
        elif not aligned:
            status = ReportStatus.PARTIAL
        
        report = RunReport(
            workflow="data_update",
            status=status,
            duration_seconds=duration_seconds,
        )
        
        # Set data status
        report.data_status = {
            "raw_latest": raw_latest,
            "qlib_latest": qlib_latest,
            "aligned": aligned,
        }
        if gap_days is not None:
            report.data_status["gap_days"] = gap_days
        
        # Add collector stats section
        if collector_stats:
            report.add_section(
                name="Data Collection",
                status=ReportStatus.SUCCESS,
                metrics=collector_stats,
            )
        
        # Add adapter/sync stats section
        if adapter_stats:
            report.add_section(
                name="Data Sync",
                status=ReportStatus.SUCCESS,
                metrics=adapter_stats,
            )
        
        # Add data alignment section
        alignment_metrics = {
            "raw_latest": raw_latest,
            "qlib_latest": qlib_latest,
            "aligned": aligned,
            "gap_days": gap_days if gap_days is not None else "N/A",
        }
        report.add_section(
            name="Data Alignment",
            status=ReportStatus.SUCCESS if aligned else ReportStatus.PARTIAL,
            metrics=alignment_metrics,
        )
        
        # Add blockers and notes
        for blocker in (blockers or []):
            report.add_blocker(blocker)
        for note in (notes or []):
            report.add_note(note)
        
        return report
    
    @staticmethod
    def from_adapter_status(adapter, duration_seconds: float = None, **kwargs) -> RunReport:
        """Generate report from QlibAdapter status"""
        status_report = adapter.get_data_status_report()
        
        return DataUpdateReport.generate(
            raw_latest=status_report.get("raw_latest", "N/A"),
            qlib_latest=status_report.get("qlib_latest", "N/A"),
            aligned=status_report.get("aligned", False),
            gap_days=status_report.get("gap_days"),
            duration_seconds=duration_seconds,
            **kwargs
        )
    
    @staticmethod
    def save(report: RunReport, output_dir: str = "data/reports") -> str:
        """Save the report to file"""
        return save_report(report, output_dir)