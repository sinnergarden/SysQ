"""Rank-weight target construction — score → target_weights."""

from __future__ import annotations

import pandas as pd

from qsys.strategy.allocation.schema import (
    add_metadata_columns,
    validate_target_weights,
)


def build_rank_weight_targets(
    predictions: pd.DataFrame,
    *,
    trade_date: str | None = None,
    score_column: str = "score",
    instrument_column: str = "instrument",
    top_n: int = 20,
    max_weight: float | None = None,
    weight_decay: str = "linear",
    normalize: bool = True,
    allocation_method: str = "rank_weight",
    strategy_id: str | None = None,
    signal_id: str | None = None,
    signal_run_id: str | None = None,
) -> pd.DataFrame:
    """Build a rank-weighted target allocation from predictions.

    Parameters
    ----------
    predictions:
        DataFrame with at least ``instrument_column`` and ``score_column``.
    trade_date:
        Optional trade date to stamp on output rows.  When ``None``, uses
        the first value from ``predictions["trade_date"]`` if available.
    score_column:
        Column name for scores (default ``score``).
    instrument_column:
        Column name for instruments (default ``instrument``).
    top_n:
        Maximum number of positions to select.
    max_weight:
        Optional maximum weight per instrument.  Weights exceeding
        *max_weight* are capped and the excess is redistributed only
        to names still below *max_weight*.
    weight_decay:
        Weight decay scheme (``linear`` only for now).
    normalize:
        When ``True`` (default), normalize weights to sum to 1.0.
    allocation_method:
        Label for the ``allocation_method`` column.
    strategy_id, signal_id, signal_run_id:
        Optional metadata columns to attach.

    Returns
    -------
    pd.DataFrame
        Columns: ``trade_date``, ``instrument``, ``target_weight``, ``score``,
        ``rank``, ``allocation_method``, plus optional metadata.
    """
    if weight_decay != "linear":
        raise ValueError(
            f"Unsupported weight_decay {weight_decay!r}; only 'linear' is supported"
        )

    if score_column not in predictions.columns:
        raise ValueError(
            f"Score column {score_column!r} not found in predictions"
        )
    if instrument_column not in predictions.columns:
        raise ValueError(
            f"Instrument column {instrument_column!r} not found in predictions"
        )

    if predictions.empty:
        from qsys.strategy.allocation.schema import REQUIRED_TARGET_WEIGHT_COLUMNS
        result = pd.DataFrame(columns=list(REQUIRED_TARGET_WEIGHT_COLUMNS) +
                              ["score", "rank", "allocation_method"])
        if trade_date:
            result["trade_date"] = trade_date
        result = add_metadata_columns(result, allocation_method=allocation_method,
                                      strategy_id=strategy_id, signal_id=signal_id,
                                      signal_run_id=signal_run_id)
        return result

    resolved_trade_date = trade_date
    if resolved_trade_date is None and "trade_date" in predictions.columns:
        resolved_trade_date = str(predictions["trade_date"].iloc[0])

    if resolved_trade_date is None:
        raise ValueError(
            "trade_date could not be resolved: pass it explicitly or include "
            "a 'trade_date' column in predictions"
        )

    # Sort: score descending, instrument ascending (stable for ties)
    sorted_df = predictions.sort_values(
        [score_column, instrument_column],
        ascending=[False, True],
        kind="mergesort",
    ).copy()
    sorted_df["rank"] = range(1, len(sorted_df) + 1)

    # Select top_n
    top = sorted_df.head(top_n)

    # Linear rank weight
    n = len(top)
    if n == 0:
        result = pd.DataFrame(columns=[
            "trade_date", "instrument", "target_weight", "score",
            "rank", "allocation_method",
        ])
        result["trade_date"] = resolved_trade_date
        result = add_metadata_columns(result, allocation_method=allocation_method,
                                      strategy_id=strategy_id, signal_id=signal_id,
                                      signal_run_id=signal_run_id)
        return result

    weights = [float(n - i) for i in range(n)]  # n, n-1, ..., 1
    total = sum(weights)

    # Normalize
    if normalize and total > 0:
        weights = [w / total for w in weights]

    # Max-weight cap with redistribution.
    # After capping, excess is redistributed to names still below max_weight.
    # When all names are at max_weight we stop (sum may be < 1.0).
    if max_weight is not None and max_weight > 0:
        for _ in range(n * 2):
            capped = [min(w, max_weight) for w in weights]
            excess = sum(weights) - sum(capped)
            if excess < 1e-10:
                weights = capped
                break
            # All names at max_weight — can't redistribute further
            if all(c >= max_weight for c in capped):
                weights = capped
                break
            per = excess / sum(1 for c in capped if c < max_weight)
            weights = [min(w + per, max_weight) if w < max_weight else w for w in capped]

    top = top.copy()
    top["target_weight"] = weights
    top["allocation_method"] = allocation_method

    # Rename columns
    rename_map = {score_column: "score", instrument_column: "instrument"}
    top = top.rename(columns=rename_map)

    result = top[["instrument", "score", "rank", "target_weight", "allocation_method"]].copy()
    result["trade_date"] = resolved_trade_date

    result = add_metadata_columns(
        result,
        allocation_method=allocation_method,
        strategy_id=strategy_id,
        signal_id=signal_id,
        signal_run_id=signal_run_id,
    )

    result = result.reset_index(drop=True)
    # Validate only when normalized (sum > 1 is expected otherwise)
    if normalize:
        validate_target_weights(result, allow_empty=True)
    return result
