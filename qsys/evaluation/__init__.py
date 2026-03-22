# Evaluation module
# Provides unified baseline vs extended evaluation with explicit windows and defaults

from .evaluator import (
    DEFAULT_MAIN_START,
    DEFAULT_TOP_K,
    EvaluationReport,
    EvaluationResult,
    ModelMetrics,
    StrictEvaluator,
)

__all__ = [
    "StrictEvaluator",
    "EvaluationReport",
    "EvaluationResult",
    "ModelMetrics",
    "DEFAULT_MAIN_START",
    "DEFAULT_TOP_K",
]
