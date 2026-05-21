from __future__ import annotations

from typing import Any

from qsys.execution.models import BrokerOrderRequest, OS_REJECTED


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
) -> PreTradeRiskResult:
    """Check pre-trade risk constraints on a list of order requests.

    Checks performed:
    - Symbol blacklist (ST, suspended, etc.)
    - Buy orders: sufficient available cash
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

        # Price deviation check
        ref_price = reference_prices.get(req.symbol)
        if ref_price and req.price and req.order_type == "limit":
            deviation = abs(req.price - ref_price) / ref_price * 100
            if deviation > max_price_deviation_pct:
                failed.append(
                    (
                        req,
                        f"price deviation {deviation:.1f}% exceeds limit {max_price_deviation_pct}% "
                        f"(ref={ref_price:.2f}, order={req.price:.2f})",
                    )
                )
                continue

        # Cash check for buy orders
        if req.side == "buy":
            rqty = req.quantity
            rprice = req.price or ref_price or 0.0
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
