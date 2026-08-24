from __future__ import annotations

import hashlib

import pandas as pd
import pytest

from qsys.backtest.market_data import MarketDataAdapter


def _write(root, instrument: str, rows: list[dict]) -> None:
    pd.DataFrame(rows).to_feather(root / f"{instrument}.feather")


@pytest.fixture
def market_root(tmp_path):
    root = tmp_path / "daily"
    root.mkdir()
    _write(root, "A", [
        {"trade_date": "20260105", "open": 10.0, "close": 10.2, "high_limit": 11.0, "low_limit": 9.0, "paused": 0, "amount": 100.0, "factor": 1.0},
        {"trade_date": "20260106", "open": 11.0, "close": 11.0, "high_limit": 11.0, "low_limit": 9.9, "paused": 0, "amount": 200.0, "factor": 1.0},
        # The amount on the query date must not enter ADV.
        {"trade_date": "20260107", "open": 10.0, "close": 10.1, "high_limit": 11.0, "low_limit": 9.0, "paused": 0, "amount": 999999.0, "factor": 1.1},
    ])
    _write(root, "B", [
        {"trade_date": "20260107", "open": 10.0, "close": 10.0, "high_limit": 11.0, "low_limit": 9.0, "paused": 1, "amount": 100.0, "factor": 1.0},
    ])
    _write(root, "C", [
        {"trade_date": "20260107", "open": None, "close": None, "high_limit": 11.0, "low_limit": 9.0, "paused": 0, "amount": 100.0, "factor": 1.0},
    ])
    return root


def test_snapshot_is_fail_closed_for_missing_row_paused_and_invalid_price(market_root):
    adapter = MarketDataAdapter(root=market_root)
    prices, status = adapter.snapshot("2026-01-07", ["A", "B", "C", "MISSING"], price_col="open")

    assert prices == {"A": 10.0}
    assert list(status.index) == ["A", "B", "C", "MISSING"]
    assert not status.loc["A", "is_suspended"]
    assert status.loc["B", "is_suspended"]
    assert status.loc["C", "is_suspended"]
    assert status.loc["MISSING", "is_suspended"]
    assert status.loc["A", "constraint_status_known"]
    assert not status.loc["MISSING", "constraint_status_known"]
    assert status["is_suspended"].dtype == bool


def test_snapshot_limit_flags_use_selected_execution_price(market_root):
    adapter = MarketDataAdapter(root=market_root)
    prices, status = adapter.snapshot("2026-01-06", ["A"], price_col="open")
    assert prices["A"] == 11.0
    assert bool(status.loc["A", "is_limit_up"])
    assert not bool(status.loc["A", "is_limit_down"])


def test_observed_close_is_exact_day_only(market_root):
    adapter = MarketDataAdapter(root=market_root)
    assert adapter.observed_close("2026-01-07", ["A", "B", "C", "MISSING"]) == {
        "A": 10.1,
    }


def test_adv_uses_strict_prior_amount_and_reports_observations(market_root):
    adapter = MarketDataAdapter(root=market_root)
    adv, obs = adapter.adv_snapshot("2026-01-07", ["A"], window=20, min_periods=2)
    assert obs["A"] == 2
    assert adv["A"] == pytest.approx(150.0)
    assert 999999.0 not in (adv["A"],)

    insufficient, insufficient_obs = adapter.adv_snapshot(
        "2026-01-06", ["A"], window=20, min_periods=5
    )
    assert insufficient_obs["A"] == 1
    assert pd.isna(insufficient["A"])


def test_adv_requires_positive_min_periods_and_handles_no_history(market_root):
    adapter = MarketDataAdapter(root=market_root)
    with pytest.raises(ValueError, match="between 1 and window"):
        adapter.adv_snapshot("2026-01-01", ["A"], window=20, min_periods=0)
    adv, obs = adapter.adv_snapshot("2026-01-01", ["A"], window=20, min_periods=1)
    assert obs == {"A": 0}
    assert pd.isna(adv["A"])


def test_adv_filters_paused_unknown_and_same_day_after_selecting_prior_tail(tmp_path):
    root = tmp_path / "daily"
    root.mkdir()
    rows = []
    for day in range(1, 22):
        rows.append({
            "trade_date": f"202601{day:02d}",
            "amount": float(day),
            "paused": "false",
        })
    # The first two rows in the 20-day prior tail carry stale positive amounts
    # but are not legal observations: one is paused and one is unknown.
    rows[1]["amount"] = 10000.0
    rows[1]["paused"] = "true"
    rows[2]["amount"] = 20000.0
    rows[2]["paused"] = "unknown"
    # This valid row is older than the selected tail and must not be pulled in
    # merely because the two invalid rows are excluded.
    rows[0]["amount"] = 99999.0
    # Same-day amount must also be excluded by the strict prior-date boundary.
    rows.append({"trade_date": "20260122", "amount": 1_000_000.0, "paused": "false"})
    _write(root, "MIXED", rows)

    adapter = MarketDataAdapter(root=root)
    adv, observations = adapter.adv_snapshot(
        "2026-01-22", ["MIXED"], window=20, min_periods=18
    )
    assert observations == {"MIXED": 18}
    assert adv["MIXED"] == pytest.approx(12.5)

    insufficient, insufficient_observations = adapter.adv_snapshot(
        "2026-01-22", ["MIXED"], window=20, min_periods=19
    )
    assert insufficient_observations == {"MIXED": 18}
    assert pd.isna(insufficient["MIXED"])


def test_factor_snapshot_only_returns_exact_day_valid_factor(market_root):
    adapter = MarketDataAdapter(root=market_root)
    assert adapter.factor_snapshot("2026-01-07", ["A", "B", "MISSING"]) == {
        "A": 1.1,
        "B": 1.0,
    }


def test_source_identity_is_lazy_and_hashes_actual_files(market_root):
    adapter = MarketDataAdapter(root=market_root)
    assert adapter.source_identity()["used_files"] == []
    adapter.observed_close("2026-01-07", ["A", "MISSING"])
    identity = adapter.source_identity()
    expected_sha = hashlib.sha256((market_root / "A.feather").read_bytes()).hexdigest()
    assert identity["used_instruments"] == ["A"]
    assert identity["used_files"] == ["A.feather"]
    assert identity["requested_missing_instruments"] == ["MISSING"]
    assert identity["files"] == [{"instrument": "A", "file": "A.feather", "sha256": expected_sha}]
    assert identity["sha256"] == adapter.source_identity()["sha256"]


def test_source_identity_explicit_instruments_loads_present_files(market_root):
    adapter = MarketDataAdapter(data_root=market_root)
    identity = adapter.source_identity(["B", "A", "MISSING"])
    assert identity["used_instruments"] == ["A", "B"]
    assert identity["used_files"] == ["A.feather", "B.feather"]
    assert identity["requested_missing_instruments"] == ["MISSING"]
    # Identity binding hashes bytes only; it must not materialise and retain
    # the much larger canonical DataFrames.
    assert adapter._cache == {}
    hashed_a = adapter._digest_cache["A"]
    assert hashed_a.sha256 == hashlib.sha256(
        (market_root / "A.feather").read_bytes()
    ).hexdigest()

    # The first actual market observation parses lazily and reuses the digest.
    assert adapter.observed_close("2026-01-07", ["A"]) == {"A": 10.1}
    assert "A" in adapter._cache
    assert adapter._digest_cache["A"] is hashed_a


def test_lazy_parse_projects_only_accounting_columns(tmp_path):
    root = tmp_path / "daily"
    root.mkdir()
    _write(root, "WIDE", [{
        "trade_date": "20260107", "open": 10.0, "close": 10.1,
        "paused": 0, "high_limit": 11.0, "low_limit": 9.0,
        "amount": 100.0, "factor": 1.0,
        "irrelevant_feature": "must not be retained",
    }])
    adapter = MarketDataAdapter(root=root)
    adapter.snapshot("2026-01-07", ["WIDE"], price_col="open")
    assert "irrelevant_feature" not in adapter._cache["WIDE"].frame.columns
    assert set(adapter._cache["WIDE"].frame.columns) == {
        "trade_date", "open", "close", "paused", "high_limit",
        "low_limit", "amount", "factor", "__qsys_day",
    }


def test_duplicate_canonical_date_fails_closed(tmp_path):
    root = tmp_path / "daily"
    root.mkdir()
    _write(root, "DUP", [
        {"trade_date": "20260107", "close": 10.0, "paused": 0},
        {"trade_date": "2026-01-07", "close": 11.0, "paused": 0},
    ])
    with pytest.raises(ValueError, match="duplicate canonical rows"):
        MarketDataAdapter(root=root).observed_close("2026-01-07", ["DUP"])


def test_latest_legal_close_asof_seeds_first_buy_without_future_leakage(tmp_path):
    root = tmp_path / "daily"
    root.mkdir()
    _write(root, "NEW", [
        {"trade_date": "20260105", "open": 9.9, "close": 10.0, "paused": 0,
         "high_limit": 11.0, "low_limit": 9.0},
        # First buy date: execution open is legal, but there is no legal close
        # with which to initialise end-of-day valuation.
        {"trade_date": "20260106", "open": 10.5, "close": None, "paused": 0,
         "high_limit": 11.0, "low_limit": 9.0},
        # A future close must never seed the 2026-01-06 valuation.
        {"trade_date": "20260107", "open": 99.0, "close": 100.0, "paused": 0,
         "high_limit": 110.0, "low_limit": 90.0},
    ])
    adapter = MarketDataAdapter(root=root)

    execution, status = adapter.snapshot("2026-01-06", ["NEW"], price_col="open")
    assert execution == {"NEW": 10.5}
    assert not status.loc["NEW", "is_suspended"]
    assert adapter.observed_close("2026-01-06", ["NEW"]) == {}
    assert adapter.latest_legal_close_asof(
        "2026-01-06", ["NEW"], strict_before=True
    ) == {
        "NEW": {"price": 10.0, "price_date": "2026-01-05"},
    }


def test_latest_legal_close_asof_skips_paused_and_invalid_rows(tmp_path):
    root = tmp_path / "daily"
    root.mkdir()
    _write(root, "STALE", [
        {"trade_date": "20260104", "close": 8.0, "paused": 0},
        {"trade_date": "20260105", "close": 9.0, "paused": 1},
        {"trade_date": "20260106", "close": 0.0, "paused": 0},
    ])
    assert MarketDataAdapter(root=root).latest_legal_close_asof(
        "2026-01-06", ["STALE"]
    ) == {"STALE": {"price": 8.0, "price_date": "2026-01-04"}}


def test_latest_legal_close_strict_before_excludes_same_day_and_future(tmp_path):
    root = tmp_path / "daily"
    root.mkdir()
    _write(root, "PIT", [
        {"trade_date": "20260106", "close": 10.0, "paused": 0},
        {"trade_date": "20260107", "close": 100.0, "paused": 0},
    ])
    adapter = MarketDataAdapter(root=root)
    # Default as-of remains valid for an explicitly post-close caller.
    assert adapter.latest_legal_close_asof("2026-01-06", ["PIT"]) == {
        "PIT": {"price": 10.0, "price_date": "2026-01-06"},
    }
    # Pre-open seeding must use neither the same-day nor future close.
    assert adapter.latest_legal_close_asof(
        "2026-01-06", ["PIT"], strict_before=True
    ) == {}
    assert adapter.latest_legal_close_before("2026-01-06", ["PIT"]) == {}


def test_snapshot_fails_closed_when_constraint_columns_are_missing(tmp_path):
    root = tmp_path / "daily"
    root.mkdir()
    _write(root, "NO_PAUSED", [{
        "trade_date": "20260107", "open": 10.0,
        "high_limit": 11.0, "low_limit": 9.0,
    }])
    _write(root, "NO_UPPER", [{
        "trade_date": "20260107", "open": 10.0, "paused": 0,
        "low_limit": 9.0,
    }])
    _write(root, "NO_LOWER", [{
        "trade_date": "20260107", "open": 10.0, "paused": 0,
        "high_limit": 11.0,
    }])
    prices, status = MarketDataAdapter(root=root).snapshot(
        "2026-01-07", ["NO_PAUSED", "NO_UPPER", "NO_LOWER"], price_col="open"
    )
    assert prices == {}
    assert status["is_suspended"].all()
    assert not status["constraint_status_known"].any()


def test_snapshot_fails_closed_when_constraint_values_are_nan(tmp_path):
    root = tmp_path / "daily"
    root.mkdir()
    _write(root, "NAN_PAUSED", [{
        "trade_date": "20260107", "open": 10.0, "paused": float("nan"),
        "high_limit": 11.0, "low_limit": 9.0,
    }])
    _write(root, "NAN_UPPER", [{
        "trade_date": "20260107", "open": 10.0, "paused": 0,
        "high_limit": float("nan"), "low_limit": 9.0,
    }])
    _write(root, "NAN_LOWER", [{
        "trade_date": "20260107", "open": 10.0, "paused": 0,
        "high_limit": 11.0, "low_limit": float("nan"),
    }])
    prices, status = MarketDataAdapter(root=root).snapshot(
        "2026-01-07", ["NAN_PAUSED", "NAN_UPPER", "NAN_LOWER"], price_col="open"
    )
    assert prices == {}
    assert status["is_suspended"].all()
    assert not status["constraint_status_known"].any()
