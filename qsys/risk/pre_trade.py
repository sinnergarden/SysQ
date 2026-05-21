from __future__ import annotations

from typing import TYPE_CHECKING, Any

from qsys.execution.models import BrokerOrderRequest, OS_REJECTED

if TYPE_CHECKING:
    from qsys.data.snapshot import ExecutionSnapshot


class PreTradeRiskError(ValueError):
    """One or more orders failed pre-trade risk checks."""


class PreTradeRiskResult:
    """Result of pre-trade risk validation."""

    def __init__(
        self,
        *,
        passed: list[BrokerOrderRequest],
        failed: list[tuple[BrokerOrderRequest, str]],
        available_cash: float,
        total_required_cash: float,
    ) -> None:
        self.passed = passed
        self.failed = failed
        self.available_cash = available_cash
        self.total_required_cash = total_required_cash

    @property
    def all_passed(self) -> bool:
        return len(self.failed) == 0

    @property
    def summary(self) -> dict[str, Any]:
        return {
            "passed_count": len(self.passed),
            "failed_count": len(self.failed),
            "available_cash": self.available_cash,
            "total_required_cash": self.total_required_cash,
            "failed_orders": [
                {"intent_id": req.intent_id, "symbol": req.symbol, "reason": reason}
                for req, reason in self.failed
            ],
        }


def check_pre_trade_risk(
    requests: list[BrokerOrderRequest],
    *,
    available_cash: float,
    blacklist: set[str] | None = None,
    max_price_deviation_pct: float = 20.0,
    reference_prices: dict[str, float] | None = None,
    snapshot: ExecutionSnapshot | None = None,
) -> PreTradeRiskResult:
    """Check pre-trade risk constraints on a list of order requests.

    Checks performed:
    - Symbol blacklist (ST, suspended, etc.)
    - Lot size: quantity must be a multiple of 100
    - Buy orders: sufficient available cash (cumulative)
    - Sell orders: quantity does not exceed sellable amount (if snapshot provided)
    - Suspended symbols (if snapshot provided)
    - Buy price at or above limit_up (if snapshot provided)
    - Sell price at or below limit_down (if snapshot provided)
    - Price deviation from reference (if reference_prices provided)
    """
    blacklist = blacklist or set()
    reference_prices = reference_prices or {}

    passed: list[BrokerOrderRequest] = []
    failed: list[tuple[BrokerOrderRequest, str]] = []
    projected_cash = available_cash

    for req in requests:
        # Blacklist check
        if req.symbol in blacklist:
            failed.append((req, f"symbol {req.symbol} is blacklisted"))
            continue

        # Lot size check: must be multiple of 100
        if req.quantity % 100 != 0:
            failed.append(
                (req, f"quantity {req.quantity} is not a multiple of 100 (lot size)")
            )
            continue

        # Snapshot-based checks
        if snapshot is not None:
            # Suspended check
            if snapshot.is_suspended(req.symbol):
                failed.append((req, f"symbol {req.symbol} is suspended"))
                continue

            # Limit-up check for buy orders
            if req.side == "buy" and req.limit_price is not None:
                if snapshot.is_limit_up(req.symbol, req.limit_price):
                    failed.append(
                        (
                            req,
                            f"buy price {req.limit_price:.2f} is at or above limit_up "
                            f"for {req.symbol}",
                        )
                    )
                    continue

            # Limit-down check for sell orders
            if req.side == "sell" and req.limit_price is not None:
                if snapshot.is_limit_down(req.symbol, req.limit_price):
                    failed.append(
                        (
                            req,
                            f"sell price {req.limit_price:.2f} is at or below limit_down "
                            f"for {req.symbol}",
                        )
                    )
                    continue

            # Sellable amount check for sell orders
            if req.side == "sell":
                pos = snapshot.get_position(req.symbol)
                if pos is not None and req.quantity > pos.sellable_amount:
                    failed.append(
                        (
                            req,
                            f"sell quantity {req.quantity} exceeds sellable amount "
                            f"{pos.sellable_amount} for {req.symbol}",
                        )
                    )
                    continue

        # Price deviation check
        ref_price = reference_prices.get(req.symbol)
        if ref_price and req.limit_price and req.order_type == "limit":
            deviation = abs(req.limit_price - ref_price) / ref_price * 100
            if deviation > max_price_deviation_pct:
                failed.append(
                    (
                        req,
                        f"price deviation {deviation:.1f}% exceeds limit {max_price_deviation_pct}% "
                        f"(ref={ref_price:.2f}, order={req.limit_price:.2f})",
                    )
                )
                continue

        # Cash check for buy orders
        if req.side == "buy":
            rqty = req.quantity
            rprice = req.limit_price or ref_price or 0.0
            required_cash = rqty * rprice
            if required_cash > projected_cash:
                failed.append(
                    (
                        req,
                        f"insufficient cash: need {required_cash:.2f}, "
                        f"available {projected_cash:.2f} (after prior buys)",
                    )
                )
                continue
            projected_cash -= required_cash

        passed.append(req)

    total_required = available_cash - projected_cash
    return PreTradeRiskResult(
        passed=passed,
        failed=failed,
        available_cash=available_cash,
        total_required_cash=total_required,
    )
