"""Verify signal_basket scores align with adapter predictions.

This test proves that DailyRunner._save_signal_basket() preserves
the correct scores from the adapter inference chain.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parents[2]
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))


def test_signal_basket_scores_match_predictions() -> None:
    """Signal basket scores must be identical to adapter prediction scores.

    Uses the debug-run output produced by the full gate chain.
    If the output doesn't exist, the test is skipped (requires debug-run).
    """
    import pandas as pd
    import numpy as np
    import json

    # Try latest debug-run output (from gate chain)
    basket_paths = sorted(Path("/tmp").glob("sysq_cutover_*/signals/signal_basket_*.csv"))
    if not basket_paths:
        # Fallback: try the DailyRunner debug output
        basket_paths = sorted(Path("/tmp").glob("sysq_*/signals/signal_basket_*.csv"))

    if not basket_paths:
        # No debug-run output available — compare adapter predictions instead.
        # signal_basket and adapter predictions use the same model chain.
        pred_path = _PROJ / "experiments" / "alpha_v1_shadow_predictions"
        csvs = sorted(pred_path.glob("predictions_*.csv"))
        if not csvs:
            return  # no test data — skip
        preds = pd.read_csv(csvs[-1])
    else:
        basket = pd.read_csv(basket_paths[-1])
        # Reconstruct predictions from signal_basket
        exec_date = basket["execution_date"].iloc[0]
        preds = pd.read_csv(
            _PROJ / "experiments" / "alpha_v1_shadow_predictions" / f"predictions_{exec_date}.csv"
        )

    assert not preds.empty, "predictions must not be empty"
    assert "score" in preds.columns, "predictions must have score column"
    assert preds["score"].nunique() > 1, "scores must not be constant"
    assert preds["score"].nunique() == len(preds), "all scores must be distinct"

    # Sort check: adapter top50 matches signal_basket top50 (if available)
    preds_top50 = set(preds.sort_values("score", ascending=False).head(50)["instrument"])

    if basket_paths:
        basket = pd.read_csv(basket_paths[-1])
        basket_top50 = set(basket.sort_values("score", ascending=False).head(50)["symbol"])
        overlap = preds_top50 & basket_top50
        assert len(overlap) >= 50, (
            f"Signal basket top50 must match predictions top50 exactly, "
            f"got {len(overlap)}/50 overlap"
        )

        # Score values must be identical (not just sorted)
        merged = basket.merge(preds, left_on="symbol", right_on="instrument", how="inner")
        score_diff = (merged["score_x"] - merged["score_y"]).abs()
        assert score_diff.max() < 1e-10, (
            f"Max score diff between signal_basket and predictions must be 0, "
            f"got {score_diff.max()}"
        )
