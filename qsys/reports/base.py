"""
Base Report Schema for SysQ Unified Run Reports
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class ReportStatus(str, Enum):
    """Report execution status"""
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    PENDING = "pending"


@dataclass
class ReportSection:
    """A logical section within a report"""
    name: str
    status: ReportStatus = ReportStatus.PENDING
    message: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "metrics": self.metrics,
            "details": self.details,
        }


@dataclass
class RunReport:
    """
    Unified Report Schema for all SysQ workflows.
    
    Fields:
    - workflow: Workflow type (data_update, train, backtest, strict_eval, daily_ops)
    - run_id: Unique identifier for this run (timestamp-based)
    - timestamp: ISO timestamp when report was generated
    - status: Overall execution status
    - signal_date: The trading signal date relevant to this run
    - execution_date: The execution date (for daily ops)
    
    - data_status: Data readiness information
        - raw_latest: Latest raw data date
        - qlib_latest: Latest qlib data date
        - aligned: Whether data is aligned
        - health_ok: Whether data health check passed
    
    - model_info: Model version/path information
        - model_path: Path to model used
        - model_version: Version identifier
        - feature_set: Feature set used
        - train_window: Training date range
    
    - plan_summary: Generated plan summary
        - account: Account name (real/shadow)
        - trades: Number of trades
        - symbols: List of symbols in plan
        - total_value: Plan total value
    
    - blockers: Issues or blockers encountered
    - notes: Additional notes
    
    - sections: Detailed sections for each workflow step
    - artifacts: Output file paths generated
    """
    workflow: str
    run_id: str = ""
    timestamp: str = ""
    status: ReportStatus = ReportStatus.PENDING
    signal_date: Optional[str] = None
    execution_date: Optional[str] = None
    
    # Data status
    data_status: dict[str, Any] = field(default_factory=dict)
    
    # Model info
    model_info: dict[str, Any] = field(default_factory=dict)
    
    # Plan summary
    plan_summary: dict[str, Any] = field(default_factory=dict)
    
    # Issues
    blockers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    
    # Detailed sections
    sections: list[ReportSection] = field(default_factory=list)
    
    # Artifacts
    artifacts: dict[str, str] = field(default_factory=dict)
    
    # Duration
    duration_seconds: Optional[float] = None
    
    def __post_init__(self):
        if not self.run_id:
            self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()
    
    def add_section(self, name: str, status: ReportStatus = ReportStatus.PENDING,
                    message: str = "", metrics: dict = None, details: dict = None):
        """Add a detailed section to the report"""
        section = ReportSection(
            name=name,
            status=status,
            message=message,
            metrics=metrics or {},
            details=details or {}
        )
        self.sections.append(section)
        return section
    
    def add_blocker(self, message: str):
        """Add a blocker issue"""
        self.blockers.append(message)
    
    def add_note(self, message: str):
        """Add a note"""
        self.notes.append(message)
    
    def set_data_status(self, raw_latest: str = None, qlib_latest: str = None,
                        aligned: bool = None, health_ok: bool = None):
        """Set data status information"""
        if raw_latest is not None:
            self.data_status["raw_latest"] = raw_latest
        if qlib_latest is not None:
            self.data_status["qlib_latest"] = qlib_latest
        if aligned is not None:
            self.data_status["aligned"] = aligned
        if health_ok is not None:
            self.data_status["health_ok"] = health_ok
    
    def set_model_info(self, model_path: str = None, model_version: str = None,
                       feature_set: str = None, train_window: str = None):
        """Set model information"""
        if model_path is not None:
            self.model_info["model_path"] = model_path
        if model_version is not None:
            self.model_info["model_version"] = model_version
        if feature_set is not None:
            self.model_info["feature_set"] = feature_set
        if train_window is not None:
            self.model_info["train_window"] = train_window
    
    def set_plan_summary(self, account: str = None, trades: int = None,
                         symbols: list = None, total_value: float = None):
        """Set plan summary"""
        if account is not None:
            self.plan_summary["account"] = account
        if trades is not None:
            self.plan_summary["trades"] = trades
        if symbols is not None:
            self.plan_summary["symbols"] = symbols
        if total_value is not None:
            self.plan_summary["total_value"] = total_value
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return {
            "workflow": self.workflow,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "status": self.status.value,
            "signal_date": self.signal_date,
            "execution_date": self.execution_date,
            "data_status": self.data_status,
            "model_info": self.model_info,
            "plan_summary": self.plan_summary,
            "blockers": self.blockers,
            "notes": self.notes,
            "sections": [s.to_dict() for s in self.sections],
            "artifacts": self.artifacts,
            "duration_seconds": self.duration_seconds,
        }
    
    def to_markdown(self) -> str:
        """Generate a human-readable markdown report"""
        lines = [
            f"# {self.workflow.replace('_', ' ').title()} Report",
            f"**Run ID:** {self.run_id}",
            f"**Timestamp:** {self.timestamp}",
            f"**Status:** {self.status.value.upper()}",
            "",
        ]
        
        if self.signal_date:
            lines.append(f"**Signal Date:** {self.signal_date}")
        if self.execution_date:
            lines.append(f"**Execution Date:** {self.execution_date}")
        if self.duration_seconds:
            lines.append(f"**Duration:** {self.duration_seconds:.1f}s")
        lines.append("")
        
        # Data Status
        if self.data_status:
            lines.append("## Data Status")
            for k, v in self.data_status.items():
                lines.append(f"- {k}: {v}")
            lines.append("")
        
        # Model Info
        if self.model_info:
            lines.append("## Model Info")
            for k, v in self.model_info.items():
                lines.append(f"- {k}: {v}")
            lines.append("")
        
        # Plan Summary
        if self.plan_summary:
            lines.append("## Plan Summary")
            for k, v in self.plan_summary.items():
                lines.append(f"- {k}: {v}")
            lines.append("")
        
        # Blockers
        if self.blockers:
            lines.append("## Blockers")
            for b in self.blockers:
                lines.append(f"- ❌ {b}")
            lines.append("")
        
        # Notes
        if self.notes:
            lines.append("## Notes")
            for n in self.notes:
                lines.append(f"- {n}")
            lines.append("")
        
        # Sections
        for section in self.sections:
            status_icon = {
                ReportStatus.SUCCESS: "✅",
                ReportStatus.FAILED: "❌",
                ReportStatus.PARTIAL: "⚠️",
                ReportStatus.SKIPPED: "⏭️",
                ReportStatus.PENDING: "⏳",
            }.get(section.status, "")
            
            lines.append(f"## {status_icon} {section.name}")
            if section.message:
                lines.append(f"{section.message}")
            if section.metrics:
                lines.append("**Metrics:**")
                for k, v in section.metrics.items():
                    lines.append(f"- {k}: {v}")
            lines.append("")
        
        # Artifacts
        if self.artifacts:
            lines.append("## Artifacts")
            for name, path in self.artifacts.items():
                lines.append(f"- {name}: `{path}`")
            lines.append("")
        
        return "\n".join(lines)
    
    @staticmethod
    def from_dict(data: dict) -> RunReport:
        """Create a RunReport from a dictionary"""
        report = RunReport(
            workflow=data.get("workflow", "unknown"),
            run_id=data.get("run_id", ""),
            timestamp=data.get("timestamp", ""),
            status=ReportStatus(data.get("status", "pending")),
            signal_date=data.get("signal_date"),
            execution_date=data.get("execution_date"),
            data_status=data.get("data_status", {}),
            model_info=data.get("model_info", {}),
            plan_summary=data.get("plan_summary", {}),
            blockers=data.get("blockers", []),
            notes=data.get("notes", []),
            artifacts=data.get("artifacts", {}),
            duration_seconds=data.get("duration_seconds"),
        )
        
        for section_data in data.get("sections", []):
            report.sections.append(ReportSection(
                name=section_data.get("name", ""),
                status=ReportStatus(section_data.get("status", "pending")),
                message=section_data.get("message", ""),
                metrics=section_data.get("metrics", {}),
                details=section_data.get("details", {}),
            ))
        
        return report


def save_report(report: RunReport, output_dir: str | Path = "data/reports") -> str:
    """Save a report to JSON file"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    filename = f"{report.workflow}_{report.run_id}.json"
    filepath = output_dir / filename
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, indent=2, ensure_ascii=False)
    
    return str(filepath)


def load_report(filepath: str | Path) -> RunReport:
    """Load a report from JSON file"""
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)
    return RunReport.from_dict(data)