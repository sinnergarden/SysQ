"""Static registry for canonical model-training candidates.

The daily strategy registry intentionally contains only full lifecycle
strategies (pre-open, post-close, notifications, and training).  Research
screeners such as ``financial_rc`` need a canonical training entrypoint but
must not pretend to implement the trading lifecycle.  This registry keeps the
two contracts separate while preserving ``scripts/run_daily.py --mode train``
as the single operator entrypoint.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


MODEL_TRAINER_REGISTRY: dict[str, type[Any]] = {}


def register_model_trainer(strategy_id: str, cls: type[Any]) -> None:
    """Register a model-training candidate under a strategy identifier."""

    MODEL_TRAINER_REGISTRY[strategy_id] = cls


def has_model_trainer(strategy_id: str) -> bool:
    """Return whether *strategy_id* has a dedicated training candidate."""

    return strategy_id in MODEL_TRAINER_REGISTRY


def create_model_trainer(
    strategy_id: str,
    config: dict[str, Any] | None = None,
    *,
    project_root: Path | None = None,
) -> Any:
    """Construct a registered model-training candidate."""

    try:
        cls = MODEL_TRAINER_REGISTRY[strategy_id]
    except KeyError as exc:
        raise ValueError(
            f"No dedicated model trainer for strategy_id={strategy_id!r}. "
            f"Known: {sorted(MODEL_TRAINER_REGISTRY)}"
        ) from exc
    if hasattr(cls, "from_config"):
        return cls.from_config(config or {}, project_root=project_root)
    return cls(config=config or {}, project_root=project_root)


from qsys.model.financial_rc_trainer import FinancialRCTrainer  # noqa: E402

register_model_trainer("financial_rc", FinancialRCTrainer)
# Top10 UC is a single-model use case implemented by the same canonical
# artifact trainer; its strategy id remains explicit for registry/lineage
# routing and does not alter the legacy financial_rc candidate.
register_model_trainer("s180_top10", FinancialRCTrainer)
