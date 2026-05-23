"""Tests for research report dataclasses — serialisation, JSON roundtrip, JSONL append."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qsys.research.reports import (
    BacktestResult,
    EvaluationReport,
    PromotionRecord,
    append_promotion_record,
    read_report,
    write_report,
)


# ── EvaluationReport ───────────────────────────────────────────────────────────


class TestEvaluationReport:
    def test_minimal(self):
        report = EvaluationReport(
            strategy_id="test",
            stage="research",
            evaluation_id="eval_001",
            start_date="2026-01-01",
            end_date="2026-01-31",
            universe="csi300",
            feature_set="test_features",
        )
        assert report.strategy_id == "test"
        assert report.status == "success"

    def test_with_metrics(self):
        report = EvaluationReport(
            strategy_id="test",
            stage="research",
            evaluation_id="eval_001",
            start_date="2026-01-01",
            end_date="2026-01-31",
            universe="csi300",
            feature_set="test_features",
            metrics={"rank_ic": 0.05, "mse": 0.02},
        )
        assert report.metrics["rank_ic"] == 0.05


# ── BacktestResult ─────────────────────────────────────────────────────────────


class TestBacktestResult:
    def test_minimal(self):
        result = BacktestResult(
            strategy_id="test",
            backtest_id="bt_001",
            start_date="2026-01-01",
            end_date="2026-01-31",
            mode="cached_daily_equivalent",
            rebalance_freq="weekly",
        )
        assert result.initial_capital == 1_000_000.0

    def test_with_performance(self):
        result = BacktestResult(
            strategy_id="test",
            backtest_id="bt_001",
            start_date="2026-01-01",
            end_date="2026-01-31",
            mode="strict_daily_equivalent",
            rebalance_freq="daily",
            initial_capital=1_000_000.0,
            final_value=1_050_000.0,
            total_return=0.05,
        )
        assert result.total_return == 0.05


# ── PromotionRecord ────────────────────────────────────────────────────────────


class TestPromotionRecord:
    def test_minimal(self):
        record = PromotionRecord(
            strategy_id="test",
            from_stage="research",
            to_stage="candidate",
            reason="evaluation passed",
        )
        assert record.from_stage == "research"
        assert record.to_stage == "candidate"

    def test_with_evidence(self):
        record = PromotionRecord(
            strategy_id="test",
            from_stage="research",
            to_stage="candidate",
            reason="evaluation passed",
            approved_by="reviewer",
            created_at="2026-05-01T00:00:00",
            evidence={"backtest_path": "/tmp/bt.json"},
        )
        assert record.evidence["backtest_path"] == "/tmp/bt.json"


# ── Serialisation ──────────────────────────────────────────────────────────────


class TestWriteReadReport:
    def test_write_and_read_evaluation(self, tmp_path: Path):
        report = EvaluationReport(
            strategy_id="test",
            stage="research",
            evaluation_id="eval_001",
            start_date="2026-01-01",
            end_date="2026-01-31",
            universe="csi300",
            feature_set="test_features",
        )
        path = tmp_path / "eval.json"
        write_report(report, path)
        assert path.exists()

        loaded = read_report(path)
        assert loaded["strategy_id"] == "test"
        assert loaded["evaluation_id"] == "eval_001"

    def test_write_and_read_backtest(self, tmp_path: Path):
        result = BacktestResult(
            strategy_id="test",
            backtest_id="bt_001",
            start_date="2026-01-01",
            end_date="2026-01-31",
            mode="cached_daily_equivalent",
            rebalance_freq="weekly",
        )
        path = tmp_path / "bt.json"
        write_report(result, path)
        assert path.exists()

        loaded = read_report(path)
        assert loaded["strategy_id"] == "test"
        assert loaded["mode"] == "cached_daily_equivalent"


class TestAppendPromotionRecord:
    def test_appends_jsonl(self, tmp_path: Path):
        path = tmp_path / "promotions.jsonl"
        r1 = PromotionRecord(
            strategy_id="s1",
            from_stage="research",
            to_stage="candidate",
            reason="pass",
        )
        r2 = PromotionRecord(
            strategy_id="s2",
            from_stage="research",
            to_stage="candidate",
            reason="pass",
        )
        append_promotion_record(r1, path)
        append_promotion_record(r2, path)

        lines = path.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["strategy_id"] == "s1"

    def test_creates_parent_dir(self, tmp_path: Path):
        path = tmp_path / "subdir" / "promotions.jsonl"
        record = PromotionRecord(
            strategy_id="s1",
            from_stage="research",
            to_stage="candidate",
            reason="test",
        )
        append_promotion_record(record, path)
        assert path.exists()
