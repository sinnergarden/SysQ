"""TargetWeights schema validation for Framework Stable 2.0.

TargetWeights is the boundary artifact between strategy allocation and
execution planning.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

REQUIRED_TARGET_WEIGHT_COLUMNS = frozenset({"trade_date", "instrument", "target_weight"})
_DEFAULT_TOLERANCE = 0.001


def validate_target_weights(
    frame: pd.DataFrame,
    *,
    allow_empty: bool = False,
    tolerance: float = _DEFAULT_TOLERANCE,
) -> None:
    """Validate a target_weights DataFrame.

    Parameters
    ----------
    frame:
        DataFrame to validate.
    allow_empty:
        When ``True``, an empty DataFrame passes without error.
    tolerance:
        Allowed excess over sum <= 1.0 (default 0.001).

    Raises
    ------
    ValueError
        On any validation failure with a descriptive message.
    """
    if frame.empty:
        if allow_empty:
            return
        raise ValueError("target_weights frame is empty (use allow_empty=True to skip)")

    missing = REQUIRED_TARGET_WEIGHT_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(
            f"Missing required columns: {sorted(missing)}; "
            f"got columns: {list(frame.columns)}"
        )

    # Null checks
    if frame[["trade_date", "instrument", "target_weight"]].isna().any().any():
        raise ValueError("target_weights contains null values in required columns")

    # Duplicates
    dups = frame.duplicated(subset=["trade_date", "instrument"])
    if dups.any():
        examples = frame[dups][["trade_date", "instrument"]].drop_duplicates().head(3)
        raise ValueError(
            f"Duplicate (trade_date, instrument) rows found: "
            f"{examples.to_dict(orient='records')}"
        )

    # Non-finite
    non_finite = ~frame["target_weight"].apply(
        lambda x: isinstance(x, (int, float)) and np.isfinite(x)
    )
    if non_finite.any():
        bad_idx = non_finite[non_finite].index[:3].tolist()
        bad_vals = frame.loc[bad_idx, ["trade_date", "instrument", "target_weight"]]
        if not bad_vals.empty:
            raise ValueError(
                f"target_weights contains NaN or non-finite values: "
                f"{bad_vals.to_dict(orient='records')}"
            )

    # Negative weights
    if (frame["target_weight"] < -tolerance).any():
        raise ValueError(
            "target_weights contains negative values (long-only constraint)"
        )

    # Per-date sum > 1
    date_sums = frame.groupby("trade_date")["target_weight"].sum()
    over = date_sums[date_sums > 1.0 + tolerance]
    if not over.empty:
        examples = over.head(3)
        raise ValueError(
            f"Per-date target_weight sum exceeds 1.0 (tolerance={tolerance}): "
            f"{examples.to_dict()}"
        )


def add_metadata_columns(
    frame: pd.DataFrame,
    *,
    allocation_method: str | None = None,
    strategy_id: str | None = None,
    signal_id: str | None = None,
    signal_run_id: str | None = None,
) -> pd.DataFrame:
    """Add optional metadata columns to a target_weights frame.

    Modifies and returns the frame.
    """
    if allocation_method is not None:
        frame["allocation_method"] = allocation_method
    if strategy_id is not None:
        frame["strategy_id"] = strategy_id
    if signal_id is not None:
        frame["signal_id"] = signal_id
    if signal_run_id is not None:
        frame["signal_run_id"] = signal_run_id
    return frame
