"""
ADR-007 Artifact Contract Package.

Provides standard artifact contracts, adapters, writers, and validators
for all SysQ strategy outputs defined in ADR-007.

Usage:
    from qsys.artifacts.adapters import adapt_predictions
    from qsys.artifacts.writer import write_artifact, sidecar_path
    from qsys.artifacts.validator import validate

    artifacts = adapt_predictions("predictions.csv", strategy_id="alpha_v1")
    for art in artifacts:
        errors = validate(art)
        if not errors:
            write_artifact(art, sidecar_path("predictions.csv"))
"""

from qsys.artifacts.contracts import (
    SignalArtifact,
    OrderIntentArtifact,
    ExecutionArtifact,
    PortfolioSnapshot,
    CandidateReport,
    RunManifest,
    artifact_to_dict,
)
from qsys.artifacts.adapters import (
    adapt_predictions,
    adapt_order_intents,
    adapt_executions,
    adapt_portfolio_snapshot,
    build_run_manifest,
    read_plan_meta,
    read_execution_summary,
)
from qsys.artifacts.writer import write_artifact, sidecar_path
from qsys.artifacts.validator import validate

__all__ = [
    "SignalArtifact",
    "OrderIntentArtifact",
    "ExecutionArtifact",
    "PortfolioSnapshot",
    "CandidateReport",
    "RunManifest",
    "artifact_to_dict",
    "adapt_predictions",
    "adapt_order_intents",
    "adapt_executions",
    "adapt_portfolio_snapshot",
    "build_run_manifest",
    "read_plan_meta",
    "read_execution_summary",
    "write_artifact",
    "sidecar_path",
    "validate",
]
