"""Tests for deterministic order generation.

Proves that OrderGenerator.generate_orders produces the same output
regardless of input dict insertion order (fixes PYTHONHASHSEED issue).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from qsys.trader.diff import OrderGenerator


class FakePosition:
    """Minimal position stub for testing."""
    def __init__(self, symbol: str, total_amount: int = 0):
        self.symbol = symbol
        self.total_amount = total_amount
        self.sellable_amount = total_amount


class FakeAccount:
    """Minimal account stub for testing."""
    def __init__(self, positions: dict | None = None, cash: float = 1_000_000.0):
        self.positions = {k: FakePosition(k, v) for k, v in (positions or {}).items()}
        self.cash = cash

    def get_total_equity(self, prices: dict) -> float:
        mv = sum(prices.get(sym, 0) * pos.total_amount
                 for sym, pos in self.positions.items())
        return self.cash + mv

    def get_market_value(self, prices: dict) -> float:
        return sum(prices.get(sym, 0) * pos.total_amount
                   for sym, pos in self.positions.items())


# ── Determinism tests ──────────────────────────────────────────────────────────


class TestOrderGeneratorDeterminism:
    """Prove that orders are deterministic regardless of input insertion order."""

    def test_same_output_for_different_dict_orders(self):
        """Different insertion orders should produce identical order lists."""
        gen = OrderGenerator()

        prices = {"A": 10.0, "B": 20.0, "C": 30.0, "D": 40.0}

        # Same positions and targets, different insertion order
        targets_1 = {"A": 0.3, "B": 0.2, "C": 0.1, "D": 0.05}
        targets_2 = {"D": 0.05, "C": 0.1, "B": 0.2, "A": 0.3}

        account_1 = FakeAccount(positions={"A": 100, "B": 50, "C": 200, "D": 0})
        account_2 = FakeAccount(positions={"D": 0, "C": 200, "B": 50, "A": 100})

        orders_1 = gen.generate_orders(targets_1, account_1, prices)
        orders_2 = gen.generate_orders(targets_2, account_2, prices)

        # Extract sortable tuples for comparison
        def order_key(o):
            return (o["symbol"], o["side"], o["amount"])

        keyed_1 = [order_key(o) for o in orders_1]
        keyed_2 = [order_key(o) for o in orders_2]

        assert keyed_1 == keyed_2, (
            f"order mismatch: {keyed_1} != {keyed_2}"
        )

    def test_sells_before_buys(self):
        """Sells must be ordered before buys (existing behaviour)."""
        gen = OrderGenerator()
        prices = {"A": 10.0, "B": 20.0}

        # We have A (need to sell) and want B (need to buy)
        targets = {"A": 0.0, "B": 0.5}
        account = FakeAccount(positions={"A": 1000, "B": 0}, cash=50_000.0)

        orders = gen.generate_orders(targets, account, prices)
        sell_seen = False
        buy_seen = False
        for o in orders:
            if o["side"] == "sell":
                sell_seen = True
                assert not buy_seen, "sell found after buy"
            elif o["side"] == "buy":
                buy_seen = True
        assert sell_seen, "expected at least one sell"
        assert buy_seen, "expected at least one buy"

    def test_deterministic_across_multiple_runs(self):
        """Calling generate_orders twice with same inputs gives same result."""
        gen = OrderGenerator()
        prices = {"X": 15.0, "Y": 25.0, "Z": 35.0}
        targets = {"X": 0.2, "Y": 0.15, "Z": 0.1}
        account = FakeAccount(positions={"X": 200, "Y": 100, "Z": 50}, cash=100_000.0)

        orders_1 = gen.generate_orders(targets, account, prices)
        orders_2 = gen.generate_orders(targets, account, prices)

        def order_key(o):
            return (o["symbol"], o["side"], o["amount"], o["price"])

        assert [order_key(o) for o in orders_1] == [order_key(o) for o in orders_2]

    def test_empty_targets_does_not_raise(self):
        """Empty targets dict should work without error."""
        gen = OrderGenerator()
        prices = {"A": 10.0}
        account = FakeAccount(positions={"A": 100}, cash=100_000.0)
        orders = gen.generate_orders({}, account, prices)
        assert isinstance(orders, list)

    def test_empty_account_and_targets_does_not_raise(self):
        """No positions and no targets should produce empty order list."""
        gen = OrderGenerator()
        orders = gen.generate_orders({}, FakeAccount(positions={}), {})
        assert orders == []
