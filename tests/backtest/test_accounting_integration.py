from __future__ import annotations

import hashlib
import json

import pandas as pd
import pytest

from qsys.backtest._execution import execute_trade_day
from qsys.backtest.accounting import (
    BacktestAccount,
    ValuationState,
    write_corporate_action_artifact,
)
from qsys.backtest.strategy_runner import (
    BacktestRunner,
    FACTOR_ROUNDING_REL_TOLERANCE,
    _adjust_posterior_corporate_action_state,
    _adjust_valuation_corporate_action_reference,
    _load_corporate_action_store,
    _prune_factor_completeness_state,
    _update_factor_completeness_guard,
)
from qsys.backtest.posterior_policy import (
    PosteriorPolicyConfig,
    PosteriorPolicyState,
    prepare_posterior_signal_views,
    run_posterior_policy_day,
    build_valuation_execution_orders,
)
from qsys.signal.store import SignalStore


def _write_raw_bound_empty_actions(tmp_path, name="raw_actions"):
    raw = tmp_path / f"{name}.raw"
    raw.write_bytes(b"immutable corporate-action source")
    return write_corporate_action_artifact(
        pd.DataFrame(),
        tmp_path,
        artifact_name=name,
        source_raw_artifact_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
        source_raw_path=str(raw),
    )


def test_missing_close_carries_and_resumes() -> None:
    account = BacktestAccount(10_000)
    account.start_day("2026-01-01")
    account.update_after_deal("A", 100, 10, 0, "buy")
    valuation = ValuationState()
    valuation.update({"A": 10}, "2026-01-01")
    valuation.update({}, "2026-01-02")
    stale = valuation.mark_to_market(account, "2026-01-02").iloc[0]
    assert stale["last_price"] == 10
    assert bool(stale["stale_price"])
    valuation.update({"A": 11}, "2026-01-03")
    resumed = valuation.mark_to_market(account, "2026-01-03").iloc[0]
    assert resumed["last_price"] == 11
    assert not bool(resumed["stale_price"])


def test_raw_cash_and_share_events_preserve_economic_value() -> None:
    account = BacktestAccount(10_000)
    account.start_day("2026-01-01")
    account.update_after_deal("A", 100, 10, 0, "buy")
    account.start_day("2026-01-02")
    account.apply_corporate_action({
        "event_id": "cash", "instrument": "A", "effective_date": "2026-01-02",
        "event_type": "cash_dividend", "cash_per_share": 1.0,
        "share_multiplier": 1.0, "settlement_date": "2026-01-04",
    })
    assert account.get_total_equity({"A": 9}) == pytest.approx(10_000)
    account.apply_corporate_action({
        "event_id": "split", "instrument": "A", "effective_date": "2026-01-02",
        "event_type": "split", "cash_per_share": 0.0,
        "share_multiplier": 2.0, "settlement_date": "",
    })
    assert account.positions["A"].total_amount == 200
    assert account.positions["A"].total_basis == pytest.approx(1_000)


@pytest.mark.parametrize(
    "event,expected_price,expected_shares",
    [
        (
            {"instrument": "A", "event_type": "cash_dividend", "cash_per_share": 1.0},
            9.0,
            100,
        ),
        (
            {"instrument": "A", "event_type": "split", "share_multiplier": 2.0},
            5.0,
            200,
        ),
    ],
)
def test_missing_ex_date_close_adjusts_stale_reference(
    event, expected_price, expected_shares
) -> None:
    account = BacktestAccount(10_000)
    account.start_day("2026-01-01")
    account.update_after_deal("A", 100, 10, 0, "buy")
    marks = ValuationState()
    marks.update({"A": 10}, "2026-01-01")
    _adjust_valuation_corporate_action_reference(marks, [event], {"A"})
    account.start_day("2026-01-02")
    if expected_shares != 100:
        account.apply_corporate_action({
            "event_id": "split", "effective_date": "2026-01-02",
            "settlement_date": "", "share_multiplier": 2.0,
            "event_type": "split", "instrument": "A",
        })
    marks.update({}, "2026-01-02")
    row = marks.mark_to_market(account, "2026-01-02").iloc[0]
    assert row["last_price"] == pytest.approx(expected_price)
    assert row["market_value"] == pytest.approx(expected_price * expected_shares)
    marks.update({"A": expected_price}, "2026-01-03")
    assert not bool(marks.mark_to_market(account, "2026-01-03").iloc[0]["stale_price"])


def test_posterior_reference_is_adjusted_for_raw_events() -> None:
    state = PosteriorPolicyState(
        previous_close={"A": 10.0}, peak_close={"A": 12.0},
        cumulative_cash_per_current_share={"A": 2.0},
    )
    _adjust_posterior_corporate_action_state(
        state,
        [{"instrument": "A", "event_type": "split", "share_multiplier": 2.0}],
        {"A"},
    )
    assert state.previous_close["A"] == pytest.approx(5.0)
    assert state.peak_close["A"] == pytest.approx(6.0)
    assert state.cumulative_cash_per_current_share["A"] == pytest.approx(1.0)


def test_combined_cash_and_share_reference_adjustment_is_cash_first() -> None:
    # Deliberately put the alphabetically earlier bonus event first.  Economic
    # ordering is cash entitlement/reference subtraction, then share scaling.
    events = [
        {"event_id": "a_bonus", "instrument": "A", "event_type": "bonus_shares", "share_multiplier": 2.0},
        {"event_id": "z_cash", "instrument": "A", "event_type": "cash_dividend", "cash_per_share": 1.0},
    ]
    state = PosteriorPolicyState(
        previous_close={"A": 10.0}, peak_close={"A": 12.0}
    )
    valuation = ValuationState()
    valuation.update({"A": 10.0}, "2026-01-01")
    _adjust_posterior_corporate_action_state(state, events, {"A"})
    _adjust_valuation_corporate_action_reference(valuation, events, {"A"})
    assert state.previous_close["A"] == pytest.approx(5.0)
    assert state.peak_close["A"] == pytest.approx(6.0)
    assert state.cumulative_cash_per_current_share["A"] == pytest.approx(0.5)
    assert valuation.prices["A"] == pytest.approx(4.5)


def test_cash_ex_date_keeps_total_return_stop_and_peak_continuous() -> None:
    prior_day, ex_day = "2026-06-15", "2026-06-16"
    account = BacktestAccount(1_000.0)
    account.start_day(prior_day)
    account.update_after_deal("A", 100, 10.0, 0.0, "buy")
    account.start_day(ex_day)
    event = {
        "event_id": "cash",
        "instrument": "A",
        "effective_date": ex_day,
        "event_type": "cash_dividend",
        "cash_per_share": 1.0,
        "share_multiplier": 1.0,
        "settlement_date": "2026-06-20",
    }
    account.apply_corporate_action(event)
    state = PosteriorPolicyState(
        entry_index={"A": 0},
        previous_close={"A": 10.0},
        peak_close={"A": 10.0},
    )
    _adjust_posterior_corporate_action_state(state, [event], {"A"})
    valuation = ValuationState()
    valuation.update({"A": 10.0}, prior_day)
    _adjust_valuation_corporate_action_reference(valuation, [event], {"A"})
    signal = pd.DataFrame({"instrument": ["A"], "score": [1.0]})
    config = PosteriorPolicyConfig()
    views = prepare_posterior_signal_views(
        {ex_day: signal}, [ex_day], score_column="score", config=config
    )

    class _ExDateMarket:
        def latest_legal_close_before(self, date, instruments):
            raise AssertionError("held valuation cache must not be overwritten")

        def snapshot(self, date, instruments, price_col="open"):
            return {"A": 9.0}, pd.DataFrame(
                {"is_suspended": False, "is_limit_up": False, "is_limit_down": False},
                index=["A"],
            )

        def observed_close(self, date, instruments):
            return {"A": 9.0}

    result, _, _ = run_posterior_policy_day(
        account=account,
        state=state,
        config=config,
        views=views,
        day_signal=signal,
        trade_date=ex_day,
        trading_index=1,
        is_rebalance=False,
        top_n=1,
        commission=0.0,
        stamp_duty=0.0,
        min_commission=0.0,
        slippage=0.0,
        execution_price_mode="open",
        market_snapshot_fn=None,
        market_data=_ExDateMarket(),
        valuation_state=valuation,
    )
    assert result["hard_stop_exit_count"] == 0
    assert result["position_count"] == 1
    assert state.previous_close["A"] == pytest.approx(10.0)
    assert state.peak_close["A"] == pytest.approx(10.0)


def test_pay_and_list_date_rows_are_exported_and_attributed(tmp_path) -> None:
    dates = ["2026-06-15", "2026-06-16", "2026-06-17"]
    signal = pd.DataFrame([
        {
            "trade_date": day,
            "data_date": (pd.Timestamp(day) - pd.offsets.BDay(1)).strftime("%Y-%m-%d"),
            "instrument": "A",
            "signal_id": "sig",
            "signal_run_id": "run",
            "score": 1.0,
        }
        for day in dates
    ])
    SignalStore(str(tmp_path)).save_signal_run(
        "sig", "run", signal, overwrite=True
    )
    events = pd.DataFrame([
        {
            "event_id": "cash_A",
            "instrument": "A",
            "effective_date": dates[1],
            "event_type": "cash_dividend",
            "cash_per_share": 1.0,
            "share_multiplier": 1.0,
            "announcement_date": dates[0],
            "settlement_date": dates[2],
        },
        {
            "event_id": "stock_A",
            "instrument": "A",
            "effective_date": dates[1],
            "event_type": "stock_dividend",
            "cash_per_share": 0.0,
            "share_multiplier": 1.1,
            "announcement_date": dates[0],
            "settlement_date": dates[2],
        },
        {
            "event_id": "outside_B",
            "instrument": "B",
            "effective_date": dates[1],
            "event_type": "split",
            "cash_per_share": 0.0,
            "share_multiplier": 2.0,
            "announcement_date": dates[0],
            "settlement_date": dates[1],
        },
    ])
    write_corporate_action_artifact(
        events, tmp_path, artifact_name="actions"
    )
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    pd.DataFrame({
        "trade_date": dates,
        "open": [10.0, 90.0 / 11.0, 90.0 / 11.0],
        "close": [10.0, 90.0 / 11.0, 90.0 / 11.0],
        "paused": [0, 0, 0],
        "high_limit": [20.0, 20.0, 20.0],
        "low_limit": [1.0, 1.0, 1.0],
        "amount": [100_000_000.0] * 3,
        "factor": [1.0, 1.0, 1.0],
    }).to_feather(canonical / "A.feather")
    output = tmp_path / "backtest"
    result = BacktestRunner().run_from_signal_cache(
        signal_id="sig",
        signal_run_id="run",
        start_date=dates[0],
        end_date=dates[-1],
        research_root=tmp_path,
        output_dir=output,
        overwrite=True,
        initial_capital=10_000.0,
        top_n=1,
        commission=0.0,
        stamp_duty=0.0,
        min_commission=0.0,
        slippage=0.0,
        rebalance_freq="20d",
        corporate_action_artifact="actions",
        canonical_data_root=canonical,
    )
    ledger = pd.read_csv(output / "corporate_action_ledger.csv")
    attribution = json.loads(
        (output / "accounting_attribution.json").read_text()
    )["corporate_actions"]
    assert result.final_value == pytest.approx(10_000.0)
    assert set(ledger["status"]) == {"applied", "no_position", "settled"}
    assert len(ledger[ledger["status"] == "applied"]) == 2
    assert len(ledger[ledger["status"] == "settled"]) == 2
    assert set(ledger.loc[ledger["status"] == "settled", "date"]) == {dates[2]}
    assert attribution == {
        "cash_dividend": pytest.approx(1_000.0),
        "held_applied_event_count": 2,
        "no_position_event_count": 1,
        "pay_cash": pytest.approx(1_000.0),
        "settlement_count": 2,
        "share_adjustment": pytest.approx(100.0),
        "source_event_count": 3,
    }
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["artifacts"]["executions"]["complete"] is True
    expected_rows = {
        "executions": len(pd.read_csv(output / "executions.csv")),
        "daily_summary": len(pd.read_csv(output / "daily_summary.csv")),
        "metrics": 1,
        "accounting_attribution": 1,
        "corporate_action_ledger": len(ledger),
        "valuation_ledger": len(pd.read_csv(output / "valuation_ledger.csv")),
    }
    accounting_artifacts = manifest["accounting"]["artifacts"]
    assert set(accounting_artifacts) >= set(expected_rows)
    for name, row_count in expected_rows.items():
        artifact = accounting_artifacts[name]
        assert artifact["path"]
        assert artifact["schema_version"]
        assert len(artifact["sha256"]) == 64
        assert artifact["row_count"] == row_count
        assert artifact["complete"] is True


def test_market_lineage_union_uses_only_scheduled_rebalance_candidates(
    tmp_path,
) -> None:
    dates = ["2026-06-15", "2026-06-16", "2026-06-17"]
    leaders = ["A", "B", "C"]
    rows = []
    for day, leader in zip(dates, leaders):
        for instrument in ("A", "B", "C"):
            rows.append({
                "trade_date": day,
                "data_date": (pd.Timestamp(day) - pd.offsets.BDay(1)).strftime("%Y-%m-%d"),
                "instrument": instrument,
                "signal_id": "sig",
                "signal_run_id": "run",
                "score": 3.0 if instrument == leader else (2.0 if instrument == "A" else 1.0),
            })
    SignalStore(str(tmp_path)).save_signal_run(
        "sig", "run", pd.DataFrame(rows), overwrite=True
    )
    write_corporate_action_artifact(
        pd.DataFrame(), tmp_path, artifact_name="empty_actions"
    )
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    for instrument in ("A", "C"):
        pd.DataFrame({
            "trade_date": dates,
            "open": [10.0] * 3,
            "close": [10.0] * 3,
            "paused": [0] * 3,
            "high_limit": [20.0] * 3,
            "low_limit": [1.0] * 3,
            "amount": [100_000_000.0] * 3,
            "factor": [1.0] * 3,
        }).to_feather(canonical / f"{instrument}.feather")
    output = tmp_path / "lineage_backtest"
    BacktestRunner().run_from_signal_cache(
        signal_id="sig",
        signal_run_id="run",
        start_date=dates[0],
        end_date=dates[-1],
        research_root=tmp_path,
        output_dir=output,
        overwrite=True,
        top_n=1,
        commission=0.0,
        stamp_duty=0.0,
        min_commission=0.0,
        slippage=0.0,
        rebalance_freq="2d",
        corporate_action_artifact="empty_actions",
        canonical_data_root=canonical,
    )
    identity = json.loads((output / "manifest.json").read_text())[
        "market_source_identity"
    ]
    assert identity["used_instruments"] == ["A", "C"]
    assert identity["requested_missing_instruments"] == []
    ledger_path = output / "corporate_action_ledger.csv"
    assert pd.read_csv(ledger_path).columns.tolist() == [
        "event_id", "date", "event_type", "instrument", "shares_before",
        "shares_after", "cash_delta", "receivable_delta", "basis_before",
        "basis_after", "status",
    ]
    manifest = json.loads((output / "manifest.json").read_text())
    empty_ledger_artifact = manifest["accounting"]["artifacts"][
        "corporate_action_ledger"
    ]
    assert empty_ledger_artifact["row_count"] == 0
    assert empty_ledger_artifact["complete"] is True


def test_complete_accounting_fails_before_output_on_missing_market_source(
    tmp_path,
) -> None:
    day = "2026-06-15"
    SignalStore(str(tmp_path)).save_signal_run(
        "sig",
        "run",
        pd.DataFrame([{
            "trade_date": day,
            "data_date": "2026-06-12",
            "instrument": "MISSING",
            "signal_id": "sig",
            "signal_run_id": "run",
            "score": 1.0,
        }]),
        overwrite=True,
    )
    _write_raw_bound_empty_actions(tmp_path, "empty_actions")
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    output = tmp_path / "must_not_exist"
    with pytest.raises(ValueError, match="canonical market source coverage"):
        BacktestRunner().run_from_signal_cache(
            signal_id="sig",
            signal_run_id="run",
            start_date=day,
            end_date=day,
            research_root=tmp_path,
            output_dir=output,
            overwrite=True,
            top_n=1,
            corporate_action_artifact="empty_actions",
            canonical_data_root=canonical,
            max_participation_rate=0.10,
            liquidity_gate_mode="reject",
            require_complete_accounting=True,
        )
    assert not output.exists()


def test_complete_accounting_executes_from_frozen_market_slice(tmp_path) -> None:
    day = "2026-06-15"
    SignalStore(str(tmp_path)).save_signal_run(
        "sig",
        "run",
        pd.DataFrame([{
            "trade_date": day,
            "data_date": "2026-06-12",
            "instrument": "A",
            "signal_id": "sig",
            "signal_run_id": "run",
            "score": 1.0,
        }]),
        overwrite=True,
    )
    _write_raw_bound_empty_actions(tmp_path, "actions")
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    market_dates = pd.bdate_range(end=day, periods=6).strftime("%Y-%m-%d")
    pd.DataFrame({
        "trade_date": market_dates,
        "open": [10.0] * 6,
        "close": [10.0] * 6,
        "paused": [0] * 6,
        "high_limit": [20.0] * 6,
        "low_limit": [1.0] * 6,
        "amount": [100_000_000.0] * 6,
        "factor": [1.0] * 6,
    }).to_feather(canonical / "A.feather")
    frozen = tmp_path / "frozen_market"
    output = tmp_path / "backtest"

    with pytest.raises(ValueError, match="without terminal authorization"):
        BacktestRunner().run_from_signal_cache(
            signal_id="sig",
            signal_run_id="run",
            start_date=day,
            end_date=day,
            research_root=tmp_path,
            output_dir=tmp_path / "blocked_backtest",
            top_n=1,
            corporate_action_artifact="actions",
            canonical_data_root=canonical,
            freeze_canonical_data_to=tmp_path / "blocked_market",
            holdout_start=day,
            max_participation_rate=0.10,
            liquidity_gate_mode="reject",
            require_complete_accounting=True,
        )
    assert not (tmp_path / "blocked_market").exists()
    assert not (tmp_path / "blocked_backtest").exists()

    result = BacktestRunner().run_from_signal_cache(
        signal_id="sig",
        signal_run_id="run",
        start_date=day,
        end_date=day,
        research_root=tmp_path,
        output_dir=output,
        overwrite=True,
        top_n=1,
        commission=0.0,
        stamp_duty=0.0,
        min_commission=0.0,
        slippage=0.0,
        corporate_action_artifact="actions",
        canonical_data_root=canonical,
        freeze_canonical_data_to=frozen,
        holdout_start=day,
        terminal_authorization_ref="unit-test-explicit-authorization",
        max_participation_rate=0.10,
        liquidity_gate_mode="reject",
        require_complete_accounting=True,
    )

    assert result.status == "completed"
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["accounting_params"]["canonical_data_root"] == str(frozen)
    assert manifest["market_source_identity"]["market_slice"][
        "through_date"
    ] == day
    assert manifest["terminal_holdout"] == {
        "holdout_start": day,
        "holdout_consumed": True,
        "terminal_authorization_ref": "unit-test-explicit-authorization",
    }
    assert pd.read_feather(frozen / "A.feather")["trade_date"].max() == day


@pytest.mark.parametrize("missing_kind", ["raw", "manifest_hash"])
def test_complete_accounting_requires_verified_corporate_source_provenance(
    tmp_path, missing_kind
) -> None:
    day = "2026-06-15"
    SignalStore(str(tmp_path)).save_signal_run(
        "sig", "run", pd.DataFrame([{
            "trade_date": day,
            "data_date": "2026-06-12",
            "instrument": "A",
            "signal_id": "sig",
            "signal_run_id": "run",
            "score": 1.0,
        }]), overwrite=True,
    )
    if missing_kind == "raw":
        write_corporate_action_artifact(
            pd.DataFrame(), tmp_path, artifact_name="actions"
        )
    else:
        artifact = _write_raw_bound_empty_actions(tmp_path, "actions")
        manifest_path = artifact / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.pop("manifest_sha256", None)
        manifest.pop("identity", None)
        manifest_path.write_text(json.dumps(manifest, sort_keys=True))
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    pd.DataFrame({
        "trade_date": [day], "open": [10.0], "close": [10.0],
        "paused": [0], "high_limit": [20.0], "low_limit": [1.0],
        "amount": [100_000_000.0], "factor": [1.0],
    }).to_feather(canonical / "A.feather")
    output = tmp_path / "must_not_exist"
    with pytest.raises(ValueError, match="raw-source provenance"):
        BacktestRunner().run_from_signal_cache(
            signal_id="sig", signal_run_id="run",
            start_date=day, end_date=day, research_root=tmp_path,
            output_dir=output, overwrite=True,
            corporate_action_artifact="actions",
            canonical_data_root=canonical,
            max_participation_rate=0.10,
            liquidity_gate_mode="reject",
            require_complete_accounting=True,
        )
    assert not output.exists()


def test_valuation_execution_order_builder_separates_held_value_from_open() -> None:
    account = BacktestAccount(2_000.0)
    account.start_day("2026-06-01")
    account.update_after_deal("B", 100, 10.0, 0.0, "buy")
    target = {"A": 0.5, "B": 0.5}
    orders_by_open = []
    for held_open in (10.0, 100.0):
        orders_by_open.append(build_valuation_execution_orders(
            account,
            target,
            valuation_prices={"B": 10.0},
            execution_prices={"A": 10.0, "B": held_open},
        ))
    assert orders_by_open[0] == orders_by_open[1]
    assert orders_by_open[0] == [{
        "symbol": "A", "amount": 100, "side": "buy", "price": 10.0,
        "order_type": "market",
    }]


def test_target_rebalance_zero_sellable_exit_is_rejected_and_recorded() -> None:
    account = BacktestAccount(1_000.0)
    trade_date = "2026-06-02"
    account.start_day(trade_date)
    account.update_after_deal("A", 100, 10.0, 0.0, "buy")
    valuation = ValuationState()
    valuation.update({"A": 10.0}, trade_date)
    orders = build_valuation_execution_orders(
        account,
        {"A": 0.0},
        valuation_prices={"A": 10.0},
        execution_prices={"A": 10.0},
    )
    assert orders == [{
        "symbol": "A", "amount": 100, "side": "sell", "price": 10.0,
        "order_type": "market",
    }]
    # Target-rebalance complete accounting deliberately changes a full exit
    # to the currently sellable lot.  T+1 can make that amount zero; the
    # execution kernel must retain and reject the intent, not call matcher.
    orders[0]["amount"] = account.positions["A"].sellable_amount
    rows: list[dict] = []
    result = execute_trade_day(
        account,
        orders,
        {"A": 10.0},
        pd.DataFrame([{
            "is_suspended": False, "is_limit_up": False,
            "is_limit_down": False,
        }], index=["A"]),
        {"A": 10.0},
        trade_date,
        min_commission=0.0,
        execution_collector=rows,
        valuation_state=valuation,
    )
    assert result["rejected_count"] == 1
    assert result["filled_count"] == 0
    assert rows[0]["status"] == "rejected"
    assert "T+1" in rows[0]["rejection_reason"]
    assert account.positions["A"].total_amount == 100


def test_corporate_action_artifact_name_is_bare(tmp_path) -> None:
    with pytest.raises(ValueError, match="bare artifact"):
        _load_corporate_action_store(tmp_path, "nested/actions")


@pytest.mark.parametrize(
    "relative_change",
    [FACTOR_ROUNDING_REL_TOLERANCE / 2, FACTOR_ROUNDING_REL_TOLERANCE],
)
def test_factor_rounding_noise_does_not_fail_completeness_guard(relative_change) -> None:
    previous = {"A": 100.0}
    pending: dict[str, int] = {}
    _update_factor_completeness_guard(
        factors={"A": 100.0 * (1.0 + relative_change)},
        previous_factors=previous,
        event_instruments=set(),
        pending_explained_factor_change=pending,
        trade_date="2026-06-16",
    )
    assert pending == {}


def test_material_factor_jump_without_event_fails_completeness_guard() -> None:
    with pytest.raises(ValueError, match="uncovered corporate-action factor jump"):
        _update_factor_completeness_guard(
            factors={"A": 100.0 * (1.0 + FACTOR_ROUNDING_REL_TOLERANCE + 1e-6)},
            previous_factors={"A": 100.0},
            event_instruments=set(),
            pending_explained_factor_change={},
            trade_date="2026-06-16",
        )


def test_material_factor_jump_with_event_is_covered_and_pending() -> None:
    pending: dict[str, int] = {}
    _update_factor_completeness_guard(
        factors={"A": 100.0 * (1.0 + FACTOR_ROUNDING_REL_TOLERANCE + 1e-6)},
        previous_factors={"A": 100.0},
        event_instruments={"A"},
        pending_explained_factor_change=pending,
        trade_date="2026-06-16",
    )
    assert pending == {"A": 1}


def test_factor_guard_resets_state_across_empty_holding_gap() -> None:
    previous = {"A": 1.0}
    pending = {"A": 1}

    # Exit A.  Any event/factor change while it is out of the portfolio must
    # not be compared with the pre-exit factor when A is bought again.
    _prune_factor_completeness_state(
        held_instruments=set(),
        previous_factors=previous,
        pending_explained_factor_change=pending,
    )
    assert previous == {}
    assert pending == {}
    _update_factor_completeness_guard(
        factors={},
        previous_factors=previous,
        event_instruments=set(),
        pending_explained_factor_change=pending,
        trade_date="2026-05-10",
    )

    # Re-entry seeds the current factor; only a later jump while continuously
    # held is a completeness failure.
    _update_factor_completeness_guard(
        factors={"A": 2.0},
        previous_factors=previous,
        event_instruments=set(),
        pending_explained_factor_change=pending,
        trade_date="2026-05-11",
    )
    with pytest.raises(ValueError, match="uncovered corporate-action factor jump"):
        _update_factor_completeness_guard(
            factors={"A": 2.0 * (1.0 + FACTOR_ROUNDING_REL_TOLERANCE + 1e-6)},
            previous_factors=previous,
            event_instruments=set(),
            pending_explained_factor_change=pending,
            trade_date="2026-05-12",
        )
