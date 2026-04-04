from __future__ import annotations

from typing import Any

from qsys.workflow.contracts import build_workflow_result


REQUIRED_TOP_LEVEL_FIELDS = (
    "signal_date",
    "execution_date",
    "data_status",
    "model_info",
    "shadow_plan_summary",
    "real_plan_summary",
)


def _build_markdown_summary(result: dict[str, Any]) -> str:
    summary = result.get("summary", {})
    signal_date = summary.get("signal_date", "unknown")
    execution_date = summary.get("execution_date", "unknown")
    shadow = summary.get("executable_portfolio", {}).get("shadow", {})
    real = summary.get("executable_portfolio", {}).get("real", {})
    decision = result.get("decision", "unknown")
    return (
        f"preopen-plan {decision} | signal_date={signal_date} | execution_date={execution_date} "
        f"| shadow_trades={shadow.get('trades', 0)} | real_trades={real.get('trades', 0)}"
    )


def run_preopen_plan(**kwargs) -> dict[str, Any]:
    # Import lazily so the adapter can be imported without pulling heavy runtime deps until used.
    from scripts.run_daily_trading import run_preopen_workflow

    workflow = run_preopen_workflow(**kwargs)

    missing = [field for field in REQUIRED_TOP_LEVEL_FIELDS if field not in workflow]
    if missing:
        raise ValueError(f"Missing required preopen workflow fields: {missing}")

    blockers = list(workflow.get("blockers") or [])
    data_status = workflow.get("data_status") or {}
    shadow_summary = workflow.get("shadow_plan_summary") or {}
    real_summary = workflow.get("real_plan_summary") or {}

    risk_flags: list[str] = []
    if not data_status.get("health_ok", True):
        risk_flags.append("data_not_ready")
    if kwargs.get("top_k", 5) != 5:
        risk_flags.append("top_k_not_roadmap_default")
    if shadow_summary.get("status") in {"empty_plan", "no_plan"}:
        risk_flags.append("shadow_plan_empty")
    if real_summary.get("status") in {"empty_plan", "no_plan"}:
        risk_flags.append("real_plan_empty")

    decision = "ready"
    blocker = blockers[0] if blockers else None
    if blockers:
        decision = "blocked"
    elif risk_flags:
        decision = "warning"

    result = build_workflow_result(
        task_name="preopen-plan",
        status="ok",
        decision=decision,
        blocker=blocker,
        input_params=kwargs,
        data_status=data_status,
        model_info=workflow.get("model_info") or {},
        artifacts=workflow.get("artifacts") or {},
        summary={
            "signal_date": workflow["signal_date"],
            "execution_date": workflow["execution_date"],
            "target_portfolio": workflow.get("signal_basket_summary") or {},
            "executable_portfolio": {
                "shadow": shadow_summary,
                "real": real_summary,
            },
            "blocked_symbols": workflow.get("blocked_symbols") or [],
            "signal_quality_gate": workflow.get("signal_quality_summary") or {},
            "cash_utilization": workflow.get("cash_utilization") or {},
            "assumptions": workflow.get("assumptions") or {},
        },
        risk_flags=risk_flags,
        next_action=workflow.get("next_action"),
    )
    result["markdown_summary"] = _build_markdown_summary(result)
    return result
