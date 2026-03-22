"""
Unified Run Report System for SysQ

This module provides structured report generation for all major workflows:
- Data Update Report
- Training Report
- Backtest Report
- Strict Evaluation Report
- Daily Operations Report (pre-open & post-close)

Each report follows a common schema for consistency and auditability.
"""

from qsys.reports.base import RunReport, ReportSection, ReportStatus, load_report, save_report
from qsys.reports.daily import DailyOpsReport
from qsys.reports.train import TrainingReport
from qsys.reports.backtest import BacktestReport
from qsys.reports.strict_eval import StrictEvalReport
from qsys.reports.data_update import DataUpdateReport

__all__ = [
    "RunReport",
    "ReportSection", 
    "ReportStatus",
    "DailyOpsReport",
    "TrainingReport",
    "BacktestReport",
    "StrictEvalReport",
    "DataUpdateReport",
    "load_report",
    "save_report",
]