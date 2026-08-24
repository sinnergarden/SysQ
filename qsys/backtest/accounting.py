"""PIT-safe portfolio accounting primitives used by the backtest kernel.

The trader account is intentionally a very small execution object.  Backtests
need a little more state than that object historically carried: a date-aware
T+1 bucket, an auditable cost basis, corporate-action cash receivables, and a
price cache which never doubles as an execution-price source.  This module is
kept under :mod:`qsys.backtest` so it cannot silently change live ledger
semantics.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from qsys.trader.account import Account, Position


EVENT_TYPES = {
    "cash_dividend",
    "stock_dividend",
    "bonus_shares",
    "split",
    "consolidation",
}
EVENT_COLUMNS = [
    "event_id", "instrument", "effective_date", "event_type",
    "cash_per_share", "share_multiplier", "announcement_date",
    "settlement_date", "source", "source_record_id",
    # Optional source/audit fields retained when available.
    "record_date", "ex_date", "pay_date", "div_listdate", "imp_ann_date",
    "source_row_hash",
]
STK_DIV_COMPONENT_TOLERANCE = 1e-9


def _day(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return ""
    parsed = pd.to_datetime(text, errors="coerce")
    return "" if pd.isna(parsed) else parsed.strftime("%Y-%m-%d")


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return default if not math.isfinite(result) else result


def _validate_settlement_date(
    event_type: str, effective_date: Any, settlement_date: Any, *, event_id: str = ""
) -> str:
    effective = _day(effective_date)
    settlement = _day(settlement_date)
    label = event_id or event_type
    if not effective:
        raise ValueError(f"corporate action {label} lacks effective_date")
    if event_type in {"cash_dividend", "stock_dividend", "bonus_shares"}:
        if not settlement:
            raise ValueError(f"{event_type} {label} lacks settlement_date")
        if settlement < effective:
            raise ValueError(
                f"{event_type} settlement_date precedes effective_date: "
                f"{settlement} < {effective}"
            )
    elif event_type in {"split", "consolidation"}:
        if settlement and settlement != effective:
            raise ValueError(
                f"{event_type} settlement_date must be empty or equal effective_date"
            )
    return settlement


@dataclass
class BacktestPosition(Position):
    """Position compatible with trader ``Position`` plus basis accounting."""

    total_basis: float = 0.0
    acquired_date: str = ""

    @property
    def cost_basis(self) -> float:
        return float(self.total_basis)


class BacktestAccount(Account):
    """A backtest-only ``Account`` with auditable, date-aware accounting.

    The public ``positions`` mapping and the base ``Position`` fields remain
    intact, allowing the existing matcher and portfolio helpers to consume the
    account.  All mutations happen through this class, never through the live
    ledger account.
    """

    def __init__(self, init_cash: float = 1_000_000.0):
        super().__init__(init_cash=init_cash)
        self.positions: dict[str, BacktestPosition] = {}
        self.trade_date: str | None = None
        self.realized_trade_pnl = 0.0
        self.corporate_action_income = 0.0
        self.fees = 0.0
        # Public aggregate matches accounting terminology; the private map
        # keeps event-level attribution/idempotency.
        self.dividend_receivable = 0.0
        self._dividend_receivables: dict[str, float] = {}
        self._applied_corporate_actions: set[str] = set()
        self._settled_corporate_actions: set[str] = set()
        self._pending_events: dict[str, dict[str, Any]] = {}
        self._pending_t1: dict[str, dict[str, int]] = {}
        self._pending_corporate_shares: dict[str, dict[str, Any]] = {}
        self.corporate_action_ledger: list[dict[str, Any]] = []
        self._execution_ca_applied_dates: set[str] = set()

    @property
    def total_assets(self) -> float:
        """Cash-like assets, including declared but unpaid dividends."""
        return float(self.cash + self.frozen_cash + self.total_receivable)

    def start_day(self, trade_date: str) -> None:
        """Set the accounting day and release shares bought on earlier days."""
        day = _day(trade_date)
        if not day:
            raise ValueError("trade_date must be a valid ISO date")
        self.trade_date = day
        for symbol, pos in self.positions.items():
            pending = self._pending_t1.get(symbol, {})
            for acquired_day in list(pending):
                if acquired_day < day:
                    pending.pop(acquired_day, None)
            pending_total = sum(pending.values())
            pending_total += sum(
                int(item["amount"]) for item in self._pending_corporate_shares.values()
                if item.get("instrument") == symbol
            )
            pos.sellable_amount = max(0, pos.total_amount - pending_total)
        self._settle_corporate_shares(day)
        self._settle_dividend_receivables(day)

    def settlement(self, trade_date: str | None = None) -> None:
        """Settle T+1 and cash receivables without making same-day buys sellable."""
        if trade_date is not None:
            self.start_day(trade_date)
            return
        # Legacy callers have no date.  Do not release same-day buys; this is
        # intentionally fail-closed rather than reintroducing T+0.
        for pos in self.positions.values():
            if not pos.acquired_date:
                pos.sellable_amount = pos.total_amount

    def _position(self, symbol: str) -> BacktestPosition:
        if symbol not in self.positions:
            self.positions[symbol] = BacktestPosition(symbol=symbol)
        return self.positions[symbol]

    def update_after_deal(
        self, symbol: str, amount: int, price: float, fee: float, side: str
    ) -> None:
        amount = int(amount)
        price = _number(price)
        fee = _number(fee)
        if amount <= 0 or price <= 0:
            raise ValueError("amount and price must be positive")
        if side not in {"buy", "sell"}:
            raise ValueError(f"unsupported side: {side}")
        day = self.trade_date or ""
        gross = amount * price
        self.fees += fee
        if side == "buy":
            total = gross + fee
            if self.cash + 1e-9 < total:
                raise ValueError("insufficient cash")
            self.cash -= total
            pos = self._position(symbol)
            pos.total_basis += total  # fees are part of the basis
            pos.total_amount += amount
            pos.acquired_date = day
            # Existing sellable shares remain sellable; only the new lot is T+1.
            # If this is the first lot, preserve zero.  If an old lot exists,
            # the matcher may still sell that old lot on the same day.
            old_sellable = max(0, pos.sellable_amount)
            pos.sellable_amount = old_sellable
            if day:
                pending = self._pending_t1.setdefault(symbol, {})
                pending[day] = pending.get(day, 0) + amount
            pos.avg_cost = pos.total_basis / pos.total_amount
            return

        pos = self.positions.get(symbol)
        if pos is None or pos.sellable_amount < amount:
            raise ValueError("insufficient sellable shares")
        basis_sold = pos.total_basis * amount / pos.total_amount
        net_proceeds = gross - fee
        self.cash += net_proceeds
        self.realized_trade_pnl += net_proceeds - basis_sold
        pos.total_amount -= amount
        pos.sellable_amount -= amount
        pos.total_basis -= basis_sold
        if pos.total_amount <= 0:
            self.positions.pop(symbol, None)
            self._pending_t1.pop(symbol, None)
        else:
            pos.avg_cost = pos.total_basis / pos.total_amount

    def apply_corporate_action(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """Apply one event once at its effective date.

        Cash dividends become receivables at ex/effective date.  This prevents
        the raw ex-date price drop from creating a fictitious loss while also
        preventing cash from being spent before pay date.
        """
        event_id = str(event.get("event_id") or "")
        if not event_id:
            raise ValueError("corporate action event_id is required")
        event_day = _day(event.get("effective_date")) or self.trade_date or ""
        if event_id in self._applied_corporate_actions:
            # Idempotency includes the audit ledger: a replay must not append
            # a second row for the same event_id.
            return {"event_id": event_id, "status": "already_applied"}
        kind = str(event.get("event_type") or "").lower()
        if kind not in EVENT_TYPES:
            raise ValueError(f"unsupported corporate action: {kind}")
        symbol = str(event.get("instrument") or "")
        pos = self.positions.get(symbol)
        shares_before = int(pos.total_amount) if pos else 0
        basis_before = float(pos.total_basis) if pos else 0.0
        cash_before = float(self.cash)
        receivable_before = float(self.dividend_receivable)
        # Events for an unheld instrument are still marked applied.  This is
        # important for idempotency when a position is opened later.
        if pos is None:
            self._applied_corporate_actions.add(event_id)
            self._record_ca(event, event_day, 0, 0, cash_before, cash_before,
                            basis_before, basis_before, "no_position")
            return {"event_id": event_id, "status": "no_position"}
        if kind == "cash_dividend":
            settlement_date = _validate_settlement_date(
                kind, event_day, event.get("settlement_date"), event_id=event_id
            )
            amount = pos.total_amount * _number(event.get("cash_per_share"))
            self._dividend_receivables[event_id] = amount
            self.dividend_receivable += amount
            self.corporate_action_income += amount
            self._pending_events[event_id] = {
                "settlement_date": settlement_date, "amount": amount,
                "instrument": symbol, "effective_date": event_day,
            }
        else:
            settlement_date = _validate_settlement_date(
                kind, event_day, event.get("settlement_date"), event_id=event_id
            )
            multiplier = _number(event.get("share_multiplier"), 0.0)
            if multiplier <= 0:
                raise ValueError(f"invalid share multiplier for {event_id}")
            new_total = pos.total_amount * multiplier
            if abs(new_total - round(new_total)) > 1e-8:
                raise ValueError(f"non-integer corporate-action shares for {event_id}")
            pos.total_amount = int(round(new_total))
            pending = self._pending_t1.get(symbol, {})
            for acquired_day, amount in list(pending.items()):
                adjusted = amount * multiplier
                if abs(adjusted - round(adjusted)) > 1e-8:
                    raise ValueError(f"non-integer corporate-action shares for {event_id}")
                pending[acquired_day] = int(round(adjusted))
            pending_corp = self._pending_corporate_shares
            for pending_id, pending_event in list(pending_corp.items()):
                if pending_event.get("instrument") != symbol:
                    continue
                adjusted = float(pending_event["amount"]) * multiplier
                if abs(adjusted - round(adjusted)) > 1e-8:
                    raise ValueError(f"non-integer corporate-action shares for {event_id}")
                pending_event["amount"] = int(round(adjusted))
            if kind in {"stock_dividend", "bonus_shares"}:
                new_shares = int(round(new_total)) - shares_before
                if new_shares < 0:
                    raise ValueError(f"invalid share multiplier for {event_id}")
                # Keep the issued shares out of sellable quantity until the
                # list date.  If list date equals ex-date, the normal
                # settlement pass below releases them on the same day.
                self._pending_corporate_shares[event_id] = {
                    "event": dict(event), "instrument": symbol,
                    "settlement_date": settlement_date, "amount": new_shares,
                }
            # Splits/consolidations convert existing shares immediately.  Any
            # pre-existing T+1/corporate pending buckets remain constrained.
            pending_total = sum(pending.values()) + sum(
                int(item["amount"]) for item in pending_corp.values()
                if item.get("instrument") == symbol
            )
            pos.sellable_amount = max(0, pos.total_amount - pending_total)
            # Total basis remains constant; average cost adjusts with shares.
            pos.avg_cost = pos.total_basis / pos.total_amount if pos.total_amount else 0.0
        self._applied_corporate_actions.add(event_id)
        self._record_ca(
            event, event_day, shares_before, int(pos.total_amount), cash_before,
            float(self.cash), basis_before, float(pos.total_basis), "applied",
            receivable_before=receivable_before,
            receivable_after=float(self.dividend_receivable),
        )
        self._settle_corporate_shares(self.trade_date or event_day)
        return {"event_id": event_id, "status": "applied", "type": kind}

    apply_event = apply_corporate_action

    def _settle_corporate_shares(self, day: str) -> None:
        for event_id, pending in list(self._pending_corporate_shares.items()):
            if pending["settlement_date"] > day or event_id in self._settled_corporate_actions:
                continue
            symbol = str(pending.get("instrument") or "")
            amount = int(pending.get("amount", 0) or 0)
            pos = self.positions.get(symbol)
            if pos is None:
                pos = self._position(symbol)
            shares_before = int(pos.total_amount)
            basis = float(pos.total_basis)
            cash_before = float(self.cash)
            receivable_before = float(self.dividend_receivable)
            pos.sellable_amount += amount
            # T+1 pending shares still cannot be sold even after a list date.
            t1_pending = sum(self._pending_t1.get(symbol, {}).values())
            other_corp = sum(int(item["amount"]) for other_id, item in self._pending_corporate_shares.items()
                             if other_id != event_id and item.get("instrument") == symbol)
            pos.sellable_amount = max(0, pos.total_amount - t1_pending - other_corp)
            self._settled_corporate_actions.add(event_id)
            self._pending_corporate_shares.pop(event_id, None)
            event = dict(pending.get("event") or {})
            self._record_ca(event, day, shares_before, int(pos.total_amount), cash_before,
                            float(self.cash), basis, float(pos.total_basis), "settled",
                            receivable_before=receivable_before,
                            receivable_after=float(self.dividend_receivable))

    def _settle_dividend_receivables(self, day: str) -> None:
        for event_id, pending in list(self._pending_events.items()):
            if pending["settlement_date"] <= day and event_id not in self._settled_corporate_actions:
                amount = float(self._dividend_receivables.pop(event_id, 0.0))
                instrument = str(pending.get("instrument") or "")
                pos = self.positions.get(instrument)
                shares = int(pos.total_amount) if pos else 0
                basis = float(pos.total_basis) if pos else 0.0
                cash_before = float(self.cash)
                receivable_before = float(self.dividend_receivable)
                self.dividend_receivable -= amount
                self.cash += amount
                self._settled_corporate_actions.add(event_id)
                self._pending_events.pop(event_id, None)
                self._record_ca(
                    {"event_id": event_id, "instrument": instrument,
                     "event_type": "cash_dividend"}, day, shares, shares,
                    cash_before, float(self.cash), basis, basis, "settled",
                    receivable_before=receivable_before,
                    receivable_after=float(self.dividend_receivable),
                )

    def apply_corporate_actions(
        self, events: Iterable[Mapping[str, Any]], trade_date: str
    ) -> list[dict[str, Any]]:
        """Apply all events effective on ``trade_date`` deterministically."""
        day = _day(trade_date)
        self.start_day(day)
        out = []
        # Cash entitlement is based on shares held before ex-date share
        # adjustment.  Never let arbitrary event_id ordering change it.
        for event in sorted(
            events,
            key=lambda e: (0 if str(e.get("event_type") or "") == "cash_dividend" else 1,
                           str(e.get("event_id", ""))),
        ):
            if _day(event.get("effective_date")) == day:
                out.append(self.apply_corporate_action(event))
        self._settle_dividend_receivables(day)
        return out

    def _record_ca(
        self, event: Mapping[str, Any], event_day: str, shares_before: int,
        shares_after: int, cash_before: float, cash_after: float,
        basis_before: float, basis_after: float, status: str, *,
        receivable_before: float | None = None,
        receivable_after: float | None = None,
    ) -> None:
        self.corporate_action_ledger.append({
            "event_id": str(event.get("event_id") or ""),
            "date": event_day,
            "event_type": str(event.get("event_type") or ""),
            "instrument": str(event.get("instrument") or ""),
            "shares_before": int(shares_before), "shares_after": int(shares_after),
            "cash_delta": float(cash_after - cash_before),
            "receivable_delta": float((receivable_after if receivable_after is not None else self.dividend_receivable)
                                      - (receivable_before if receivable_before is not None else self.dividend_receivable)),
            "basis_before": float(basis_before), "basis_after": float(basis_after),
            "status": status,
        })

    @property
    def corporate_action_ledger_rows(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.corporate_action_ledger]

    def corporate_action_ledger_frame(self) -> pd.DataFrame:
        columns = ["event_id", "date", "event_type", "instrument", "shares_before",
                   "shares_after", "cash_delta", "receivable_delta", "basis_before",
                   "basis_after", "status"]
        return pd.DataFrame(self.corporate_action_ledger_rows, columns=columns)

    @property
    def total_receivable(self) -> float:
        return float(self.dividend_receivable)

    @property
    def realized_pnl(self) -> float:
        return float(self.realized_trade_pnl)

    def unrealized_pnl(self, current_prices: Mapping[str, float]) -> float:
        return float(sum(
            pos.total_amount * _number(current_prices.get(symbol)) - pos.total_basis
            for symbol, pos in self.positions.items()
        ))

    def get_total_equity(self, current_prices: Mapping[str, float]) -> float:
        return float(self.cash + self.total_receivable + sum(
            pos.total_amount * _number(current_prices.get(symbol))
            for symbol, pos in self.positions.items()
        ))


class ValuationState:
    """Recent-valid-close cache, deliberately separate from execution prices."""

    def __init__(self, initial_prices: Mapping[str, Any] | None = None, initial_date: str | None = None) -> None:
        self._prices: dict[str, float] = {}
        self._dates: dict[str, str] = {}
        if initial_prices is not None:
            if not initial_date:
                raise ValueError("initial_date is required with initial_prices")
            self.update(initial_prices, initial_date)

    @property
    def prices(self) -> dict[str, float]:
        return dict(self._prices)

    def seed_asof(self, observations: Any, asof_date: str) -> int:
        """Seed per-instrument last legal prices known by ``asof_date``.

        Accepted inputs are a DataFrame with ``instrument``, ``price`` (or
        ``close``), and ``price_date`` (or ``trade_date``/``date``), a mapping
        of instrument to ``{"price", "price_date"}``, or a mapping to
        ``(price, price_date)``.  Future observations are always rejected.
        """
        asof = _day(asof_date)
        if not asof:
            raise ValueError("asof_date must be a valid ISO date")
        rows: list[dict[str, Any]] = []
        if isinstance(observations, pd.DataFrame):
            frame = observations.copy()
            if "instrument" not in frame:
                raise ValueError("seed observations require instrument")
            price_col = "price" if "price" in frame else "close" if "close" in frame else ""
            date_col = next((col for col in ("price_date", "trade_date", "date") if col in frame), "")
            if not price_col or not date_col:
                raise ValueError("seed observations require price/close and price_date")
            rows = [
                {"instrument": row["instrument"], "price": row[price_col],
                 "price_date": row[date_col]}
                for _, row in frame.iterrows()
            ]
        elif isinstance(observations, Mapping):
            for instrument, value in observations.items():
                if isinstance(value, Mapping):
                    price = value.get("price", value.get("close"))
                    price_date = value.get("price_date", value.get("trade_date", value.get("date")))
                elif isinstance(value, (tuple, list)) and len(value) == 2:
                    price, price_date = value
                else:
                    raise ValueError("mapping seed values require price and price_date")
                rows.append({"instrument": instrument, "price": price, "price_date": price_date})
        else:
            rows = [dict(row) for row in observations]

        normalized: list[tuple[str, str, float]] = []
        for row in rows:
            symbol = str(row.get("instrument") or "").strip()
            price_date = _day(row.get("price_date"))
            price = _number(row.get("price"), float("nan"))
            if not symbol or not price_date:
                raise ValueError("seed observation instrument and price_date are required")
            if price_date > asof:
                raise ValueError(f"future valuation seed for {symbol}: {price_date} > {asof}")
            if not math.isfinite(price) or price <= 0:
                raise ValueError(f"invalid valuation seed price for {symbol}")
            normalized.append((symbol, price_date, price))

        # Latest legal observation per instrument wins.  Conflicting values
        # for the same instrument/date are rejected rather than order-selected.
        selected: dict[str, tuple[str, float]] = {}
        for symbol, price_date, price in sorted(normalized, key=lambda item: (item[0], item[1])):
            current = selected.get(symbol)
            if current and current[0] == price_date and current[1] != price:
                raise ValueError(f"conflicting valuation seeds for {symbol} on {price_date}")
            if current is None or price_date >= current[0]:
                selected[symbol] = (price_date, price)
        for symbol, (price_date, price) in selected.items():
            self._prices[symbol] = price
            self._dates[symbol] = price_date
        return len(selected)

    def update(self, close_prices: Mapping[str, Any], trade_date: str) -> None:
        day = _day(trade_date)
        if not day:
            raise ValueError("trade_date must be a valid ISO date")
        for symbol, value in close_prices.items():
            price = _number(value, float("nan"))
            if math.isfinite(price) and price > 0:
                self._prices[str(symbol)] = price
                self._dates[str(symbol)] = day

    def adjust_for_corporate_action(
        self, event: Mapping[str, Any], held_before: bool | int | float
    ) -> dict[str, Any]:
        """Adjust a cached raw close across an ex-date discontinuity.

        This is valuation-only.  The cached ``price_date`` deliberately stays
        unchanged (and therefore stale) until a legal close is observed.  It
        must never be supplied to the matcher as an execution price.
        """
        symbol = str(event.get("instrument") or "")
        kind = str(event.get("event_type") or "")
        if not bool(held_before):
            return {"instrument": symbol, "status": "not_held"}
        if symbol not in self._prices:
            return {"instrument": symbol, "status": "no_cached_price"}
        before = float(self._prices[symbol])
        if kind == "cash_dividend":
            after = before - _number(event.get("cash_per_share"), 0.0)
        elif kind in {"stock_dividend", "bonus_shares", "split", "consolidation"}:
            multiplier = _number(event.get("share_multiplier"), 0.0)
            if multiplier <= 0:
                raise ValueError("share_multiplier must be positive for valuation adjustment")
            after = before / multiplier
        else:
            raise ValueError(f"unsupported corporate action: {kind}")
        if not math.isfinite(after) or after <= 0:
            raise ValueError(f"corporate action produced invalid cached price for {symbol}")
        self._prices[symbol] = after
        return {
            "instrument": symbol, "status": "adjusted", "price_before": before,
            "price_after": after, "price_date": self._dates[symbol],
        }

    def value_position(self, symbol: str, trade_date: str) -> dict[str, Any]:
        day = _day(trade_date)
        if symbol not in self._prices:
            raise ValueError(f"no legal valuation price for held instrument {symbol}")
        price_date = self._dates[symbol]
        stale_days = max(0, (pd.Timestamp(day) - pd.Timestamp(price_date)).days)
        return {
            "instrument": symbol,
            "price": self._prices[symbol],
            "price_date": price_date,
            "stale_price": bool(stale_days > 0),
            "stale_days": int(stale_days),
        }

    def mark_to_market(
        self, account: BacktestAccount | Account, trade_date: str
    ) -> pd.DataFrame:
        rows = []
        for symbol in sorted(account.positions):
            pos = account.positions[symbol]
            mark = self.value_position(symbol, trade_date)
            rows.append({
                "instrument": symbol,
                "quantity": int(pos.total_amount),
                "sellable_quantity": int(pos.sellable_amount),
                "cost_price": float(pos.avg_cost),
                "last_price": float(mark["price"]),
                "market_value": float(pos.total_amount * mark["price"]),
                "price_date": mark["price_date"],
                "stale_price": mark["stale_price"],
                "stale_days": mark["stale_days"],
            })
        return pd.DataFrame(rows, columns=[
            "instrument", "quantity", "sellable_quantity", "cost_price",
            "last_price", "market_value", "price_date", "stale_price", "stale_days",
        ])

    # Short aliases keep the object convenient for small backtest harnesses.
    mark = mark_to_market


def _canonical_source_hash(row: Mapping[str, Any]) -> str:
    payload = {str(k): ("" if pd.isna(v) else str(v)) for k, v in sorted(row.items())}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _normalise_dividend_key_number(value: Any) -> str:
    """Canonicalize numeric economic fields, treating NaN as zero."""
    number = _number(value, float("nan"))
    if not math.isfinite(number):
        return "0"
    return format(number, ".15g")


def _tushare_dividend_economic_key(
    row: Mapping[str, Any], instrument: str,
) -> tuple[str, ...]:
    """Return the stable key for one economic dividend implementation.

    Announcement metadata is deliberately excluded: Tushare can return the
    same implementation more than once as its announcement is revised.  The
    gross cash entitlement is preferred, with net ``cash_div`` as a fallback
    for sources that do not provide the gross field.
    """
    gross_cash = _number(row.get("cash_div_tax"), float("nan"))
    cash_entitlement = row.get("cash_div_tax") if math.isfinite(gross_cash) else row.get("cash_div")
    return (
        instrument,
        _day(row.get("ex_date")),
        _day(row.get("end_date")),
        _normalise_dividend_key_number(cash_entitlement),
        _normalise_dividend_key_number(row.get("stk_div")),
        _normalise_dividend_key_number(row.get("stk_bo_rate")),
        _normalise_dividend_key_number(row.get("stk_co_rate")),
        _day(row.get("record_date")),
        _day(row.get("pay_date")),
        _day(row.get("div_listdate")),
    )


def _dedupe_tushare_dividend_rows(
    raw_df: pd.DataFrame,
) -> list[tuple[dict[str, Any], str]]:
    """Select one deterministic source row per economic implementation.

    The raw source bundle remains responsible for retaining every source row;
    this selector only controls which row supplies the normalized event's
    lineage fields.  Newer ``ann_date`` wins, then ``imp_ann_date`` and the
    canonical source-row hash provide deterministic tie breaks.
    """
    selected: dict[tuple[str, ...], tuple[tuple[str, str, str], dict[str, Any], str]] = {}
    seen_source_rows: set[str] = set()
    for _, source_row in raw_df.iterrows():
        row = source_row.to_dict()
        source_hash = _canonical_source_hash(row)
        if source_hash in seen_source_rows:
            continue
        seen_source_rows.add(source_hash)
        if str(row.get("div_proc", "")).strip() != "实施":
            continue
        instrument = str(row.get("ts_code") or row.get("instrument") or "").strip()
        if not instrument or not _day(row.get("ex_date")):
            continue
        economic_key = _tushare_dividend_economic_key(row, instrument)
        rank = (
            _day(row.get("ann_date")),
            _day(row.get("imp_ann_date")),
            source_hash,
        )
        prior = selected.get(economic_key)
        if prior is None or rank > prior[0]:
            selected[economic_key] = (rank, row, source_hash)
    return [
        (row, source_hash)
        for _, row, source_hash in sorted(
            selected.values(), key=lambda item: (item[0], item[2])
        )
    ]


def normalize_tushare_dividend(raw_df: pd.DataFrame) -> pd.DataFrame:
    """Normalize Tushare ``dividend`` rows into strict corporate-action events."""
    if raw_df is None or raw_df.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    rows: list[dict[str, Any]] = []
    for row, source_hash in _dedupe_tushare_dividend_rows(raw_df):
        effective = _day(row.get("ex_date"))
        announcement = _day(row.get("imp_ann_date")) or _day(row.get("ann_date"))
        instrument = str(row.get("ts_code") or row.get("instrument") or "").strip()
        if not instrument:
            continue
        if not effective:
            continue
        if announcement and announcement > effective:
            raise ValueError(
                f"implemented dividend known_at/announcement_date is after effective_date: "
                f"{instrument}:{effective}"
            )
        source_id = str(row.get("id") or row.get("source_record_id") or "")
        base = {
            "instrument": instrument, "effective_date": effective,
            "announcement_date": announcement, "settlement_date": "",
            "source": "tushare", "source_record_id": source_id or source_hash,
            "record_date": _day(row.get("record_date")), "ex_date": effective,
            "pay_date": _day(row.get("pay_date")), "div_listdate": _day(row.get("div_listdate")),
            "imp_ann_date": _day(row.get("imp_ann_date")), "source_row_hash": source_hash,
        }
        # Raw-price continuity uses the declared gross cash entitlement.
        # Holding-period dividend tax is intentionally not modeled.  Therefore
        # the net cash_div field must never be substituted for cash_div_tax.
        raw_gross_cash = row.get("cash_div_tax")
        raw_net_cash = row.get("cash_div")
        if raw_gross_cash is not None and str(raw_gross_cash).strip().lower() not in {"", "nan", "nat", "none"}:
            try:
                parsed_gross_cash = float(raw_gross_cash)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid cash_div_tax={raw_gross_cash!r}") from exc
            if not math.isfinite(parsed_gross_cash):
                raise ValueError(f"invalid cash_div_tax={raw_gross_cash!r}")
        gross_cash = _number(raw_gross_cash, float("nan"))
        net_cash = _number(raw_net_cash, 0.0)
        if net_cash > 0 and not math.isfinite(gross_cash):
            raise ValueError(f"cash dividend lacks declared gross cash_div_tax: {instrument}:{effective}")
        if math.isfinite(gross_cash) and gross_cash < 0:
            raise ValueError(f"cash_div_tax must be non-negative: {instrument}:{effective}")
        cash = gross_cash if math.isfinite(gross_cash) else 0.0
        if cash > 0:
            cash_settlement = _day(row.get("pay_date"))
            if not cash_settlement:
                # No pay date means a cash dividend cannot be modeled safely.
                raise ValueError(f"cash dividend lacks pay_date: {instrument}:{effective}")
            _validate_settlement_date(
                "cash_dividend", effective, cash_settlement,
                event_id=f"{instrument}:{effective}:cash",
            )
            rows.append({**base, "event_id": f"{instrument}:{effective}:cash:{source_hash[:16]}",
                         "settlement_date": cash_settlement,
                         "event_type": "cash_dividend", "cash_per_share": cash,
                         "share_multiplier": 1.0})
        share_rates: dict[str, float] = {}
        for field in ("stk_bo_rate", "stk_co_rate"):
            raw_rate = row.get(field)
            if raw_rate is not None and str(raw_rate).strip().lower() not in {"", "nan", "nat", "none"}:
                try:
                    parsed_rate = float(raw_rate)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"invalid {field}={raw_rate!r}") from exc
                if not math.isfinite(parsed_rate):
                    raise ValueError(f"invalid {field}={raw_rate!r}")
            rate = _number(raw_rate, 0.0)
            if rate < 0 or rate > 5.0:
                raise ValueError(f"invalid {field}={raw_rate!r}; expected per-share ratio in [0, 5]")
            share_rates[field] = rate
        bo_rate = share_rates["stk_bo_rate"]
        co_rate = share_rates["stk_co_rate"]
        raw_total_rate = row.get("stk_div")
        has_total_rate = (
            raw_total_rate is not None
            and str(raw_total_rate).strip().lower() not in {"", "nan", "nat", "none"}
        )
        if has_total_rate:
            try:
                parsed_total_rate = float(raw_total_rate)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid stk_div={raw_total_rate!r}") from exc
            if not math.isfinite(parsed_total_rate):
                raise ValueError(f"invalid stk_div={raw_total_rate!r}")
        total_rate = _number(raw_total_rate, 0.0)
        if total_rate < 0 or total_rate > 5.0:
            raise ValueError(f"invalid stk_div={raw_total_rate!r}; expected per-share ratio in [0, 5]")
        component_rate = bo_rate + co_rate
        if component_rate > 0 and has_total_rate and not math.isclose(
            component_rate, total_rate,
            rel_tol=STK_DIV_COMPONENT_TOLERANCE,
            abs_tol=STK_DIV_COMPONENT_TOLERANCE,
        ):
            raise ValueError(
                f"inconsistent stk_div={total_rate} vs "
                f"stk_bo_rate+stk_co_rate={component_rate}: {instrument}:{effective}"
            )
        if component_rate > 0 or total_rate > 0:
            # Prefer the explicit components when available.  A row exposing
            # only stk_div is still actionable; its type is conservatively
            # represented as stock_dividend because Tushare's total field
            # does not distinguish bonus shares from stock dividends.
            share_rate = component_rate if component_rate > 0 else total_rate
            event_type = (
                ("stock_dividend" if bo_rate else "bonus_shares")
                if component_rate > 0 else "stock_dividend"
            )
            list_date = _day(row.get("div_listdate"))
            if not list_date:
                raise ValueError(f"share distribution lacks div_listdate: {instrument}:{effective}")
            # One source row is one economic share event.  Splitting bo/co
            # into sequential multipliers would incorrectly compound them.
            _validate_settlement_date(
                event_type, effective, list_date,
                event_id=f"{instrument}:{effective}:share",
            )
            rows.append({**base, "event_id": f"{instrument}:{effective}:share:{source_hash[:16]}",
                         "settlement_date": list_date, "event_type": event_type,
                         "cash_per_share": 0.0,
                         "share_multiplier": 1.0 + share_rate})
    result = pd.DataFrame(rows, columns=EVENT_COLUMNS)
    if result.empty:
        return result
    if result["event_id"].duplicated().any():
        raise ValueError("duplicate corporate action event_id")
    return result.sort_values(["effective_date", "instrument", "event_id"], kind="mergesort").reset_index(drop=True)


def _validate_artifact_name(artifact_name: str) -> str:
    name = str(artifact_name or "").strip()
    path = Path(name)
    if not name or path.is_absolute() or len(path.parts) != 1 or path.parts[0] in {".", ".."}:
        raise ValueError("artifact_name must be a single non-absolute path component")
    return name


class CorporateActionStore:
    """Read and integrity-check ``research_root/corporate_actions/{name}``."""

    def __init__(self, research_root: str | Path, artifact_name: str = "default", *, verify_hash: bool = True):
        self.artifact_name = _validate_artifact_name(artifact_name)
        self.artifact_dir = Path(research_root) / "corporate_actions" / self.artifact_name
        if self.artifact_dir.is_symlink():
            raise ValueError("corporate action artifact directory must not be a symlink")
        self.events_path = self.artifact_dir / "events.parquet"
        self.manifest_path = self.artifact_dir / "manifest.json"
        if not self.events_path.is_file() or not self.manifest_path.is_file():
            raise FileNotFoundError(f"corporate action artifact incomplete: {self.artifact_dir}")
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if verify_hash:
            expected = self.manifest.get("events_sha256") or self.manifest.get("sha256")
            if not expected:
                expected = ((self.manifest.get("artifacts") or {}).get("events") or {}).get("sha256")
            if not expected:
                expected = (self.manifest.get("identity") or {}).get("events_sha256")
            actual = hashlib.sha256(self.events_path.read_bytes()).hexdigest()
            if expected != actual:
                raise ValueError("corporate action events.parquet SHA256 mismatch")
            expected_manifest = self.manifest.get("manifest_sha256")
            if expected_manifest:
                core = {key: value for key, value in self.manifest.items()
                        if key not in {"manifest_sha256", "identity"}}
                actual_manifest = hashlib.sha256(
                    json.dumps(core, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                if actual_manifest != expected_manifest:
                    raise ValueError("corporate action manifest SHA256 mismatch")
        self.events = pd.read_parquet(self.events_path)
        missing = [c for c in EVENT_COLUMNS if c not in self.events]
        if missing:
            raise ValueError(f"corporate action artifact missing columns: {missing}")
        if self.events["event_id"].duplicated().any():
            raise ValueError("duplicate corporate action event_id")
        unknown = set(self.events["event_type"].astype(str)) - EVENT_TYPES
        if unknown:
            raise ValueError(f"unsupported corporate action event types: {sorted(unknown)}")
        for _, row in self.events.iterrows():
            announcement = _day(row.get("announcement_date"))
            effective = _day(row.get("effective_date"))
            if not effective:
                raise ValueError("corporate action effective_date is required")
            if announcement and announcement > effective:
                raise ValueError("announcement_date must be <= effective_date")
            event_type = str(row.get("event_type"))
            _validate_settlement_date(
                event_type, effective, row.get("settlement_date"),
                event_id=str(row.get("event_id") or ""),
            )
            try:
                cash = float(row.get("cash_per_share", 0.0) or 0.0)
                multiplier = float(row.get("share_multiplier", 0.0) or 0.0)
            except (TypeError, ValueError) as exc:
                raise ValueError("corporate action numeric fields are invalid") from exc
            if not math.isfinite(cash) or cash < 0 or not math.isfinite(multiplier) or multiplier <= 0:
                raise ValueError("corporate action numeric fields are invalid")
            if event_type == "cash_dividend" and cash <= 0:
                raise ValueError("cash dividend cash_per_share must be positive")
            if event_type != "cash_dividend" and multiplier == 1.0:
                raise ValueError("share corporate action must change share_multiplier")
        manifest_name = self.manifest.get("artifact_name") or (self.manifest.get("identity") or {}).get("name")
        if manifest_name != self.artifact_name:
            raise ValueError("corporate action artifact name mismatch")
        identity = self.manifest.get("identity") or {}
        if identity and (identity.get("events_sha256") != self.manifest.get("events_sha256")
                         or identity.get("manifest_sha256") != self.manifest.get("manifest_sha256")):
            raise ValueError("corporate action manifest identity mismatch")
        policy = self.manifest.get("cash_dividend_policy") or {}
        if policy.get("entitlement") != "declared_gross_cash_div_tax" or policy.get("dividend_tax") != "not_modeled":
            raise ValueError("corporate action cash-dividend policy is missing or unsupported")
        expected_raw = self.manifest.get("source_raw_artifact_sha256")
        raw_relative = str(self.manifest.get("source_raw_path") or "")
        if bool(expected_raw) != bool(raw_relative):
            raise ValueError("source_raw_path and source_raw_artifact_sha256 must appear together")
        if expected_raw:
            relative_path = Path(raw_relative)
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise ValueError("source_raw_path must stay inside the artifact directory")
            raw_path = self.artifact_dir / relative_path
            cursor = self.artifact_dir
            has_symlink = False
            for part in relative_path.parts:
                cursor = cursor / part
                has_symlink = has_symlink or cursor.is_symlink()
            if has_symlink or not raw_path.is_file():
                raise ValueError("source_raw_path must be an existing non-symlink file")
            artifact_resolved = self.artifact_dir.resolve()
            raw_resolved = raw_path.resolve()
            try:
                raw_resolved.relative_to(artifact_resolved)
            except ValueError as exc:
                raise ValueError("source_raw_path escapes artifact directory") from exc
            actual_raw = hashlib.sha256(raw_path.read_bytes()).hexdigest()
            if actual_raw != expected_raw:
                raise ValueError("corporate action raw-source SHA256 mismatch")
        expected_rows = self.manifest.get("source_event_rows_sha256")
        raw_values = sorted(str(value) for value in self.events["source_row_hash"].dropna()
                            if str(value).strip() not in {"", "nan"})
        actual_rows = hashlib.sha256("\n".join(raw_values).encode()).hexdigest()
        if expected_rows and expected_rows != actual_rows:
            raise ValueError("corporate action source event-row SHA256 mismatch")

    def for_date(self, trade_date: str) -> list[dict[str, Any]]:
        day = _day(trade_date)
        return self.events[self.events["effective_date"].map(_day) == day].to_dict("records")

    def __iter__(self):
        return iter(self.events.to_dict("records"))


def write_corporate_action_artifact(
    events: pd.DataFrame | Iterable[Mapping[str, Any]],
    research_root: str | Path,
    *,
    artifact_name: str = "default",
    source: str = "normalized",
    source_raw_artifact_sha256: str | None = None,
    source_raw_path: str | None = None,
) -> Path:
    """Write deterministic events.parquet + manifest under a bare artifact."""
    frame = events.copy() if isinstance(events, pd.DataFrame) else pd.DataFrame(list(events))
    for col in EVENT_COLUMNS:
        if col not in frame.columns:
            frame[col] = "" if col not in {"cash_per_share", "share_multiplier"} else (1.0 if col == "share_multiplier" else 0.0)
    frame = frame[EVENT_COLUMNS].copy()
    required_missing = [c for c in ("event_id", "instrument", "effective_date", "event_type")
                        if frame[c].isna().any() or (frame[c].astype(str).str.strip() == "").any()]
    if required_missing:
        raise ValueError(f"corporate action required fields missing: {required_missing}")
    if frame["event_id"].duplicated().any():
        raise ValueError("duplicate corporate action event_id")
    for idx, row in frame.iterrows():
        ann, eff = _day(row["announcement_date"]), _day(row["effective_date"])
        if ann and eff and ann > eff:
            raise ValueError("announcement_date must be <= effective_date")
        if str(row["event_type"]) not in EVENT_TYPES:
            raise ValueError(f"unsupported corporate action: {row['event_type']}")
        _validate_settlement_date(
            str(row["event_type"]), eff, row["settlement_date"],
            event_id=str(row["event_id"]),
        )
    frame = frame.sort_values(["effective_date", "instrument", "event_id"], kind="mergesort").reset_index(drop=True)
    artifact_name = _validate_artifact_name(artifact_name)
    if source_raw_artifact_sha256 is not None:
        raw_digest = str(source_raw_artifact_sha256).lower()
        if len(raw_digest) != 64 or any(ch not in "0123456789abcdef" for ch in raw_digest):
            raise ValueError("source_raw_artifact_sha256 must be a SHA256 hex digest")
        source_raw_artifact_sha256 = raw_digest
    if bool(source_raw_artifact_sha256) != bool(source_raw_path):
        raise ValueError("source_raw_artifact_sha256 and source_raw_path must be provided together")
    source_path: Path | None = None
    if source_raw_path is not None:
        source_path = Path(source_raw_path)
        if source_path.is_symlink() or not source_path.is_file():
            raise ValueError("source_raw_path must be an existing non-symlink file")
        actual_source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
        if actual_source_hash != source_raw_artifact_sha256:
            raise ValueError("source_raw_artifact_sha256 does not match source_raw_path")
    target = Path(research_root) / "corporate_actions" / artifact_name
    if target.exists():
        raise FileExistsError(f"corporate action artifact already exists: {target}")
    target.mkdir(parents=True, exist_ok=False)
    raw_manifest_path = ""
    if source_path is not None:
        raw_dir = target / "source"
        raw_dir.mkdir()
        raw_target = raw_dir / source_path.name
        shutil.copyfile(source_path, raw_target)
        raw_manifest_path = raw_target.relative_to(target).as_posix()
    events_path = target / "events.parquet"
    frame.to_parquet(events_path, index=False)
    digest = hashlib.sha256(events_path.read_bytes()).hexdigest()
    raw_values = sorted(str(value) for value in frame["source_row_hash"].dropna()
                        if str(value).strip() not in {"", "nan"})
    event_rows_sha = hashlib.sha256("\n".join(raw_values).encode()).hexdigest()
    valid_dates = [_day(value) for value in frame["effective_date"] if _day(value)]
    instruments = sorted(set(frame["instrument"].astype(str)))
    manifest_core = {
        "schema_version": "corporate_actions_v1", "source": source,
        "artifact_name": artifact_name, "events_sha256": digest,
        "source_raw_artifact_sha256": source_raw_artifact_sha256 or "",
        "source_raw_path": raw_manifest_path,
        "source_event_rows_sha256": event_rows_sha,
        "row_count": int(len(frame)),
        "columns": EVENT_COLUMNS,
        "cash_dividend_policy": {
            "entitlement": "declared_gross_cash_div_tax",
            "dividend_tax": "not_modeled",
        },
        "coverage": {
            "effective_date_min": min(valid_dates) if valid_dates else "",
            "effective_date_max": max(valid_dates) if valid_dates else "",
            "instrument_count": len(instruments), "instruments": instruments,
        },
    }
    manifest_sha = hashlib.sha256(json.dumps(manifest_core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    manifest = {**manifest_core, "manifest_sha256": manifest_sha,
                "identity": {"name": artifact_name, "events_sha256": digest,
                             "manifest_sha256": manifest_sha}}
    (target / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8")
    return target


__all__ = [
    "BacktestAccount", "BacktestPosition", "ValuationState", "CorporateActionStore",
    "normalize_tushare_dividend", "write_corporate_action_artifact", "EVENT_COLUMNS",
]
