"""Strategy allocation boundary — signal → target_weights.

See ``docs/architecture/strategy-allocation-boundary.md``.
"""

from qsys.strategy.allocation.rank_weight import build_rank_weight_targets
from qsys.strategy.allocation.schema import (
    REQUIRED_TARGET_WEIGHT_COLUMNS,
    add_metadata_columns,
    validate_target_weights,
)

__all__ = [
    "REQUIRED_TARGET_WEIGHT_COLUMNS",
    "add_metadata_columns",
    "build_rank_weight_targets",
    "validate_target_weights",
]
