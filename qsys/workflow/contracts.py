from __future__ import annotations

from typing import Any


def build_workflow_result(
    *,
    task_name: str,
    status: str,
    decision: str,
    blocker: str | None = None,
    input_params: dict[str, Any] | None = None,
    data_status: dict[str, Any] | None = None,
    model_info: dict[str, Any] | None = None,
    artifacts: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    risk_flags: list[str] | None = None,
    next_action: str | None = None,
    markdown_summary: str = "",
) -> dict[str, Any]:
    return {
        "task_name": task_name,
        "status": status,
        "decision": decision,
        "blocker": blocker,
        "input_params": input_params or {},
        "data_status": data_status or {},
        "model_info": model_info or {},
        "artifacts": artifacts or {},
        "summary": summary or {},
        "risk_flags": risk_flags or [],
        "next_action": next_action,
        "markdown_summary": markdown_summary,
    }
