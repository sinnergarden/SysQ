from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from qsys.broker.miniqmt import AccountSnapshot, MiniQMTAdapter, PositionSnapshot


@dataclass
class ExecutionSnapshot:
    """Composite snapshot combining account, positions, and market quotes.

    Assembled from three sources:
    - ``/account`` → cash, available_cash, total_assets, frozen_cash
    - ``/positions`` → per-instrument quantity, sellable, avg_cost, market_value, last_price
    - ``/quotes`` → limit_up, limit_down, suspend_status, last_price (source of truth for limits)
    """

    account: AccountSnapshot
    positions: list[PositionSnapshot] = field(default_factory=list)
    quotes: dict[str, dict[str, Any]] = field(default_factory=dict)
    trade_date: str = ""

    @property
    def available_cash(self) -> float:
        return self.account.available_cash

    @property
    def position_symbols(self) -> set[str]:
        return {p.symbol for p in self.positions}

    def is_suspended(self, symbol: str) -> bool:
        q = self.quotes.get(symbol)
        return bool(q.get("suspended", False)) if q else False

    def is_limit_up(self, symbol: str, price: float) -> bool:
        q = self.quotes.get(symbol)
        if not q:
            return False
        limit_up = q.get("limit_up")
        return bool(limit_up and limit_up > 0 and price >= limit_up)

    def is_limit_down(self, symbol: str, price: float) -> bool:
        q = self.quotes.get(symbol)
        if not q:
            return False
        limit_down = q.get("limit_down")
        return bool(limit_down and limit_down > 0 and price <= limit_down)

    def get_position(self, symbol: str) -> PositionSnapshot | None:
        for p in self.positions:
            if p.symbol == symbol:
                return p
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "account": {
                "cash": self.account.cash,
                "available_cash": self.account.available_cash,
                "frozen_cash": self.account.frozen_cash,
                "market_value": self.account.market_value,
                "total_assets": self.account.total_assets,
            },
            "positions": [
                {
                    "symbol": p.symbol,
                    "total_amount": p.total_amount,
                    "sellable_amount": p.sellable_amount,
                    "avg_cost": p.avg_cost,
                    "market_value": p.market_value,
                    "last_price": p.last_price,
                }
                for p in self.positions
            ],
            "quotes": self.quotes,
        }


def build_execution_snapshot(
    adapter: MiniQMTAdapter,
    *,
    trade_date: str = "",
    quotes: dict[str, dict[str, Any]] | None = None,
) -> ExecutionSnapshot:
    """Fetch account + positions from the MiniQMT server and compose a snapshot.

    *quotes* can be pre-loaded from an external source (e.g. Qlib daily data,
    a MiniQMT /quotes endpoint). If None, a best-effort attempt is made to
    derive limit status from position ``last_price`` (no actual limit-up/down
    detection).
    """
    account = adapter.fetch_account_snapshot()
    positions = adapter.fetch_positions()
    return ExecutionSnapshot(
        account=account,
        positions=positions,
        quotes=quotes or {},
        trade_date=trade_date,
    )
