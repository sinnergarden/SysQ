"""Tests for qsys.artifacts.adapters — building artifacts from real data."""

from __future__ import annotations

from pathlib import Path

import pytest
from qsys.artifacts.adapters import (
    adapt_predictions,
    adapt_order_intents,
    adapt_executions,
    adapt_portfolio_snapshot,
    build_run_manifest,
    read_plan_meta,
    read_execution_summary,
)
from qsys.artifacts.validator import validate
from qsys.artifacts.contracts import NOT_AVAILABLE

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def test_adapt_predictions_multi_row() -> None:
    """adapt_predictions returns one SignalArtifact per CSV row."""
    csv_path = PROJECT_ROOT / "experiments/alpha_v1_shadow_predictions/predictions_2026-05-18.csv"
    if not csv_path.exists():
        pytest.skip("predictions CSV not available")

    arts = list(adapt_predictions(str(csv_path), strategy_id="alpha_v1"))
    assert len(arts) > 1, "expected multiple signal artifacts"

    # All pass validation
    for art in arts:
        errs = validate(art)
        assert len(errs) == 0, f"validation failed for {art.instrument}: {errs}"

    # Scores differ (not all the same row repeated)
    scores = {a.instrument: a.score for a in arts}
    assert len(scores) == len(arts), "artifacts should be distinct by instrument"

    # Ranks are 1-based and sequential
    ranks = sorted(a.rank for a in arts)
    assert ranks[0] == 1
    assert ranks[-1] == len(arts)

    # Missing fields use not_available
    assert arts[0].candidate_id == NOT_AVAILABLE
    assert arts[0].model_version == NOT_AVAILABLE
    assert arts[0].signal_version == NOT_AVAILABLE

    # None fields preserved
    assert arts[0].raw_prediction is not None  # score is used as raw_prediction
    assert arts[0].target_weight_raw is None


def test_adapt_order_intents_multi_row() -> None:
    """adapt_order_intents returns one OrderIntentArtifact per CSV row."""
    csv_path = PROJECT_ROOT / "experiments/alpha_v1_daily/2026-05-18/plan/order_intents.csv"
    if not csv_path.exists():
        pytest.skip("order_intents CSV not available")

    arts = list(adapt_order_intents(
        str(csv_path), strategy_id="alpha_v1", account_id="shadow_alpha_v1",
    ))
    assert len(arts) > 1, "expected multiple order intent artifacts"

    for art in arts:
        errs = validate(art)
        assert len(errs) == 0, f"validation failed for {art.instrument}: {errs}"

    # delta_quantity matches side
    for art in arts:
        if art.side == "BUY":
            assert art.delta_quantity is None or art.delta_quantity > 0
        elif art.side == "SELL":
            assert art.delta_quantity is None or art.delta_quantity < 0

    # Instruments are distinct
    instruments = {a.instrument for a in arts}
    assert len(instruments) == len(arts)


def test_adapt_executions_multi_row() -> None:
    """adapt_executions returns one ExecutionArtifact per CSV row."""
    csv_path = PROJECT_ROOT / "experiments/alpha_v1_daily/2026-05-18/execution/ledger_rows.csv"
    if not csv_path.exists():
        pytest.skip("ledger_rows CSV not available")

    arts = list(adapt_executions(
        str(csv_path), strategy_id="alpha_v1", account_id="shadow_alpha_v1",
    ))
    assert len(arts) > 1, "expected multiple execution artifacts"

    for art in arts:
        errs = validate(art)
        assert len(errs) == 0, f"validation failed for {art.instrument}: {errs}"

    # Sides are BUY or SELL
    for art in arts:
        assert art.side in ("BUY", "SELL")

    # Quantities and prices are positive
    for art in arts:
        assert art.quantity > 0
        assert art.price > 0


def test_adapt_portfolio_snapshot_single() -> None:
    """adapt_portfolio_snapshot returns a single PortfolioSnapshot."""
    mtm_path = PROJECT_ROOT / "experiments/alpha_v1_daily/2026-05-18/mtm/mtm_snapshot.json"
    if not mtm_path.exists():
        pytest.skip("mtm_snapshot.json not available")

    snap = adapt_portfolio_snapshot(
        str(mtm_path), trade_date="2026-05-18",
        account_id="shadow_alpha_v1", strategy_id="alpha_v1",
        turnover=914091.99,
    )
    assert snap is not None
    errs = validate(snap)
    assert len(errs) == 0, f"validation failed: {errs}"

    # Key fields populated
    assert snap.total_asset > 0
    assert snap.cash >= 0
    assert snap.market_value >= 0
    assert snap.trade_date == "2026-05-18"
    assert snap.account_id == "shadow_alpha_v1"


def test_build_run_manifest() -> None:
    """build_run_manifest returns a valid RunManifest."""
    manifest = build_run_manifest(
        run_id="alpha_v1_execute_2026-05-18",
        trade_date="2026-05-18",
        stage="postclose",
        strategy_id="alpha_v1",
        account_id="shadow_alpha_v1",
        status="completed",
        output_artifacts=[
            {"path": "ledger_rows.adr7.json", "type": "ExecutionArtifact"},
        ],
    )
    errs = validate(manifest)
    assert len(errs) == 0, f"validation failed: {errs}"
    assert manifest.run_id == "alpha_v1_execute_2026-05-18"
    assert manifest.status == "completed"
    assert len(manifest.output_artifacts) == 1
    assert manifest.output_artifacts[0]["type"] == "ExecutionArtifact"


def test_read_plan_meta() -> None:
    """read_plan_meta extracts known fields."""
    meta_path = PROJECT_ROOT / "experiments/alpha_v1_daily/2026-05-18/plan/plan_meta.json"
    if not meta_path.exists():
        pytest.skip("plan_meta.json not available")

    meta = read_plan_meta(str(meta_path))
    assert meta["strategy_id"] == "alpha_v1"
    assert meta["top_n"] == 20
    assert meta["total_value_before"] == 1_000_000.0


def test_read_execution_summary() -> None:
    """read_execution_summary extracts known fields."""
    summary_path = PROJECT_ROOT / "experiments/alpha_v1_daily/2026-05-18/execution/execution_summary.json"
    if not summary_path.exists():
        pytest.skip("execution_summary.json not available")

    summary = read_execution_summary(str(summary_path))
    assert summary["run_id"] == "alpha_v1_execute_2026-05-18"
    assert summary["status"] == "success"
    assert summary["turnover"] > 0
