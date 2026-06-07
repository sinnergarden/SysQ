"""Ops API router — read-only endpoints for daily ops state."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from qsys.ops_api.repository import OpsRepository
from qsys.ops_api.schema import DailyOpsSummary, SignalBasketRow

router = APIRouter(prefix="/api/ops", tags=["ops"])
_repo: OpsRepository | None = None


def _get_repo() -> OpsRepository:
    global _repo
    if _repo is None:
        _repo = OpsRepository()
    return _repo


@router.get("/daily/{date}")
def get_daily_summary(date: str) -> DailyOpsSummary:
    """Return a structured summary of daily ops for *date* (YYYY-MM-DD)."""
    summary = _get_repo().get_daily_summary(date)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"No ops data found for {date}")
    return summary


@router.get("/daily/{date}/signal-basket")
def get_signal_basket(date: str, top_n: int | None = None) -> list[SignalBasketRow]:
    """Return signal basket rows for *date*. Optional ``top_n`` limit."""
    rows = _get_repo().get_signal_basket(date, top_n=top_n)
    if rows is None:
        raise HTTPException(status_code=404, detail=f"No signal basket found for {date}")
    return rows


@router.get("/daily/{date}/portfolio")
def get_portfolio(date: str, account_id: str = "shadow_alpha_v1") -> dict:
    """Return portfolio snapshot for *account_id* on *date*."""
    snapshot = _get_repo().get_portfolio(account_id, date)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"No portfolio snapshot found for {date} / {account_id}")
    return snapshot.to_dict()
