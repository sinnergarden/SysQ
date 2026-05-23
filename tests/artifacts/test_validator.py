"""Tests for qsys.artifacts.validator."""

from __future__ import annotations

from qsys.artifacts.contracts import SignalArtifact, OrderIntentArtifact, ExecutionArtifact, PortfolioSnapshot, RunManifest
from qsys.artifacts.validator import validate


class TestSignalValidator:
    def test_valid_passes(self) -> None:
        art = SignalArtifact(trade_date="2026-05-18", strategy_id="test", instrument="000001.SZ", score=1.0, rank=1)
        assert len(validate(art)) == 0

    def test_missing_trade_date(self) -> None:
        art = SignalArtifact(trade_date="", strategy_id="test", instrument="000001.SZ", score=1.0, rank=1)
        errs = validate(art)
        assert any("trade_date" in e for e in errs)

    def test_missing_strategy_id(self) -> None:
        art = SignalArtifact(trade_date="2026-05-18", strategy_id="", instrument="000001.SZ", score=1.0, rank=1)
        errs = validate(art)
        assert any("strategy_id" in e for e in errs)

    def test_rank_too_low(self) -> None:
        art = SignalArtifact(trade_date="2026-05-18", strategy_id="test", instrument="000001.SZ", score=1.0, rank=0)
        errs = validate(art)
        assert any("rank" in e for e in errs)


class TestOrderIntentValidator:
    def test_valid_passes(self) -> None:
        art = OrderIntentArtifact(trade_date="2026-05-18", strategy_id="test", account_id="test", instrument="000001.SZ", side="BUY", target_weight=0.05, current_weight=0.0)
        assert len(validate(art)) == 0

    def test_invalid_side(self) -> None:
        art = OrderIntentArtifact(trade_date="2026-05-18", strategy_id="test", account_id="test", instrument="000001.SZ", side="INVALID", target_weight=0.05, current_weight=0.0)
        errs = validate(art)
        assert any("side" in e for e in errs)

    def test_missing_instrument(self) -> None:
        art = OrderIntentArtifact(trade_date="2026-05-18", strategy_id="test", account_id="test", instrument="", side="BUY", target_weight=0.05, current_weight=0.0)
        errs = validate(art)
        assert any("instrument" in e for e in errs)


class TestExecutionValidator:
    def test_valid_passes(self) -> None:
        art = ExecutionArtifact(trade_date="2026-05-18", run_id="test", strategy_id="test", account_id="test", instrument="000001.SZ", side="BUY", quantity=100, price=50.0, commission=5.0, status="filled")
        assert len(validate(art)) == 0

    def test_invalid_side(self) -> None:
        art = ExecutionArtifact(trade_date="2026-05-18", run_id="test", strategy_id="test", account_id="test", instrument="000001.SZ", side="HOLD", quantity=100, price=50.0, commission=5.0, status="filled")
        errs = validate(art)
        assert any("side" in e for e in errs)

    def test_negative_quantity(self) -> None:
        art = ExecutionArtifact(trade_date="2026-05-18", run_id="test", strategy_id="test", account_id="test", instrument="000001.SZ", side="BUY", quantity=-1, price=50.0, commission=5.0, status="filled")
        errs = validate(art)
        assert any("quantity" in e for e in errs)

    def test_invalid_status(self) -> None:
        art = ExecutionArtifact(trade_date="2026-05-18", run_id="test", strategy_id="test", account_id="test", instrument="000001.SZ", side="BUY", quantity=100, price=50.0, commission=5.0, status="unknown")
        errs = validate(art)
        assert any("status" in e for e in errs)


class TestSnapshotValidator:
    def test_valid_passes(self) -> None:
        snap = PortfolioSnapshot(trade_date="2026-05-18", account_id="test", strategy_id="test", cash=100.0, market_value=900.0, total_asset=1000.0)
        assert len(validate(snap)) == 0

    def test_negative_total_asset(self) -> None:
        snap = PortfolioSnapshot(trade_date="2026-05-18", account_id="test", strategy_id="test", cash=100.0, market_value=900.0, total_asset=-1.0)
        errs = validate(snap)
        assert any("total_asset" in e for e in errs)


class TestManifestValidator:
    def test_valid_passes(self) -> None:
        m = RunManifest(run_id="test", trade_date="2026-05-18", stage="postclose", strategy_id="test", account_id="test", status="completed")
        assert len(validate(m)) == 0

    def test_invalid_status(self) -> None:
        m = RunManifest(run_id="test", trade_date="2026-05-18", stage="postclose", strategy_id="test", account_id="test", status="unknown")
        errs = validate(m)
        assert any("status" in e for e in errs)

    def test_missing_run_id(self) -> None:
        m = RunManifest(run_id="", trade_date="2026-05-18", stage="postclose", strategy_id="test", account_id="test", status="completed")
        errs = validate(m)
        assert any("run_id" in e for e in errs)
