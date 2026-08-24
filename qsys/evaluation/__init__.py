"""Evaluation entry points."""

from .evaluator import (
    DEFAULT_MAIN_START,
    DEFAULT_TOP_K,
    EvaluationReport,
    EvaluationResult,
    ModelMetrics,
    StrictEvaluator,
)
from .top_tail import TopTailValidationError, evaluate_top_tail, write_top_tail_artifacts

__all__ = [
    "TopTailValidationError",
    "evaluate_top_tail",
    "write_top_tail_artifacts",
    "StrictEvaluator",
    "EvaluationReport",
    "EvaluationResult",
    "ModelMetrics",
    "DEFAULT_MAIN_START",
    "DEFAULT_TOP_K",
]
