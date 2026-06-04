"""Ops API schema — response models for daily ops endpoints."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class DailyOpsSummary:
    execution_date: str
    overall_status: str | None = None
    pre_open_status: str | None = None
    post_close_status: str | None = None
    signal_count: int = 0
    order_intent_count: int = 0
    reconciliation_status: str | None = None
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    run_id: str | None = None
    artifact_paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SignalBasketRow:
    symbol: str
    score: float
    score_rank: int
    weight: float
    price: float | None = None
    signal_date: str | None = None
    execution_date: str | None = None

    @staticmethod
    def from_csv_row(row: dict[str, Any]) -> "SignalBasketRow":
        return SignalBasketRow(
            symbol=str(row.get("symbol", "")),
            score=float(row.get("score", 0.0)),
            score_rank=int(row.get("score_rank", 0)),
            weight=float(row.get("weight", 0.0)),
            price=float(row["price"]) if row.get("price") else None,
            signal_date=str(row["signal_date"]) if row.get("signal_date") else None,
            execution_date=str(row["execution_date"]) if row.get("execution_date") else None,
        )


@dataclass
class PortfolioSnapshot:
    account_id: str
    trade_date: str
    cash: float
    total_market_value: float
    total_asset: float
    daily_pnl: float | None = None
    daily_return: float | None = None
    position_count: int = 0
    positions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
