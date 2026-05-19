"""Generic rank-weighted capped portfolio builder."""
from __future__ import annotations

import pandas as pd


def build_rank_weight_portfolio(
    scores: pd.Series,
    account,
    *,
    top_n: int = 20,
    buffer_hold: int = 60,
    buffer_buy: int = 40,
    single_stock_cap: float = 0.07,
) -> dict[str, float]:
    """Build a rank-weighted portfolio with buffer rules and single-stock cap.

    Parameters
    ----------
    scores : pd.Series
        Instrument → score (higher is better).
    account : Account
        Current account (used to check existing positions).
    top_n : int
        Target portfolio size.
    buffer_hold : int
        Keep existing positions if rank <= buffer_hold.
    buffer_buy : int
        Only buy new positions if rank <= buffer_buy.
    single_stock_cap : float
        Max weight per stock (capped, excess redistributed).

    Returns
    -------
    dict of {instrument: target_weight}
    """
    ranked = scores.sort_values(ascending=False)
    ranks = pd.Series(range(1, len(ranked) + 1), index=ranked.index)
    held = set(account.positions.keys())

    # Keep current holdings within buffer hold threshold
    keep = {}
    for inst in held:
        if inst in ranks.index and ranks[inst] <= buffer_hold:
            keep[inst] = scores.get(inst, 0.0)

    remaining = max(0, top_n - len(keep))
    buys = []
    if remaining > 0:
        for inst in ranked.index:
            if inst in held:
                continue
            if ranks[inst] > buffer_buy:
                continue
            buys.append(inst)
            if len(buys) >= remaining:
                break

    selected = list(keep.keys()) + buys
    if not selected:
        return {}

    # Rank weight (linear decay) + cap redistribution
    tr = sum(range(1, len(selected) + 1))
    ws = {}
    for ri, s in enumerate(selected):
        raw_w = (len(selected) - ri) / tr
        if raw_w > single_stock_cap:
            ws[s] = single_stock_cap
        else:
            ws[s] = raw_w

    # Normalize to sum=1
    wt = sum(ws.values())
    if wt > 0:
        ws = {k: v / wt for k, v in ws.items()}

    return ws
