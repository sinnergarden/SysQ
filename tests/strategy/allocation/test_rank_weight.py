"""Tests for qsys.strategy.allocation.rank_weight."""

from __future__ import annotations

import pandas as pd
import pytest

from qsys.strategy.allocation.rank_weight import build_rank_weight_targets


def _predictions(n_inst: int = 10, score_base: float = 0.0) -> pd.DataFrame:
    return pd.DataFrame({
        "trade_date": ["2026-06-15"] * n_inst,
        "instrument": [f"000{i:03d}.SZ" for i in range(n_inst)],
        "score": [score_base + float(n_inst - i) for i in range(n_inst)],
    })


class TestTopNSelection:
    def test_selects_top_n(self):
        pred = _predictions(100)
        result = build_rank_weight_targets(pred, top_n=20)
        assert len(result) == 20

    def test_returns_all_when_fewer_than_top_n(self):
        pred = _predictions(5)
        result = build_rank_weight_targets(pred, top_n=20)
        assert len(result) == 5

    def test_score_sorted_descending(self):
        pred = _predictions(10)
        result = build_rank_weight_targets(pred, top_n=5)
        scores = result["score"].tolist()
        assert scores == sorted(scores, reverse=True)


class TestRankColumn:
    def test_rank_starts_at_1(self):
        pred = _predictions(10)
        result = build_rank_weight_targets(pred, top_n=5)
        assert list(result["rank"]) == [1, 2, 3, 4, 5]

    def test_instrument_tiebreaker(self):
        """Tied scores are ordered by instrument ascending."""
        pred = pd.DataFrame({
            "trade_date": ["2026-06-15"] * 10,
            "instrument": ["000005.SZ", "000003.SZ", "000001.SZ",
                           "000004.SZ", "000002.SZ", "000010.SZ",
                           "000006.SZ", "000009.SZ", "000007.SZ", "000008.SZ"],
            "score": [1.0] * 10,
        })
        result = build_rank_weight_targets(pred, top_n=3)
        # Expect alphabetical instrument ordering as tiebreaker
        assert list(result["instrument"]) == ["000001.SZ", "000002.SZ", "000003.SZ"]

    def test_repeated_run_same_order(self):
        pred = _predictions(100)
        r1 = build_rank_weight_targets(pred, top_n=20)
        r2 = build_rank_weight_targets(pred, top_n=20)
        assert r1["instrument"].tolist() == r2["instrument"].tolist()


class TestWeightNormalization:
    def test_weights_sum_to_one(self):
        pred = _predictions(10)
        result = build_rank_weight_targets(pred, normalize=True)
        assert abs(result["target_weight"].sum() - 1.0) < 0.001

    def test_weights_without_normalization(self):
        pred = _predictions(10)
        result = build_rank_weight_targets(pred, normalize=False)
        total = result["target_weight"].sum()
        assert total > 1.0


class TestMaxWeight:
    def test_cap_never_exceeded(self):
        """No target_weight may exceed max_weight + tolerance."""
        pred = _predictions(100, score_base=100)
        cap = 0.1
        result = build_rank_weight_targets(pred, top_n=20, max_weight=cap)
        assert result["target_weight"].max() <= cap + 0.001

    def test_cap_lower_than_n_produces_sum_below_one(self):
        """When max_weight * n < 1.0, total weight may be < 1.0."""
        pred = _predictions(100, score_base=100)
        cap = 0.04  # 20 * 0.04 = 0.8 < 1.0
        result = build_rank_weight_targets(pred, top_n=20, max_weight=cap)
        assert result["target_weight"].max() <= cap + 0.001
        assert result["target_weight"].sum() <= cap * 20 + 0.001
        assert result["target_weight"].sum() < 0.85

    def test_cap_still_near_one_when_feasible(self):
        """When max_weight * n >= 1.0, sum is close to 1.0."""
        pred = _predictions(100, score_base=100)
        cap = 0.1  # 20 * 0.1 = 2.0 >= 1.0
        result = build_rank_weight_targets(pred, top_n=20, max_weight=cap)
        assert abs(result["target_weight"].sum() - 1.0) < 0.001


class TestWeightDecayValidation:
    def test_unsupported_weight_decay_raises(self):
        pred = _predictions(5)
        with pytest.raises(ValueError, match="weight_decay"):
            build_rank_weight_targets(pred, weight_decay="exponential")


class TestTradeDateResolution:
    def test_missing_trade_date_raises(self):
        pred = _predictions(5).drop(columns=["trade_date"])
        with pytest.raises(ValueError, match="trade_date could not be resolved"):
            build_rank_weight_targets(pred)


class TestMetadataColumns:
    def test_strategy_id_column(self):
        pred = _predictions(10)
        result = build_rank_weight_targets(pred, strategy_id="alpha_v1")
        assert "strategy_id" in result.columns
        assert result["strategy_id"].iloc[0] == "alpha_v1"

    def test_signal_id_column(self):
        pred = _predictions(10)
        result = build_rank_weight_targets(pred, signal_id="test_sig")
        assert result["signal_id"].iloc[0] == "test_sig"

    def test_signal_run_id_column(self):
        pred = _predictions(10)
        result = build_rank_weight_targets(pred, signal_run_id="run_001")
        assert result["signal_run_id"].iloc[0] == "run_001"

    def test_allocation_method_column(self):
        pred = _predictions(10)
        result = build_rank_weight_targets(pred, allocation_method="custom")
        assert result["allocation_method"].iloc[0] == "custom"

    def test_instrument_column_parameter(self):
        pred = _predictions(10).rename(columns={"instrument": "ts_code"})
        result = build_rank_weight_targets(pred, instrument_column="ts_code", top_n=5)
        assert len(result) == 5
        assert "instrument" in result.columns

    def test_score_column_parameter(self):
        pred = _predictions(10).rename(columns={"score": "pred"})
        result = build_rank_weight_targets(pred, score_column="pred", top_n=5)
        assert len(result) == 5


class TestErrorHandling:
    def test_missing_score_column_fails(self):
        pred = pd.DataFrame({"instrument": ["000001.SZ"], "other": [1.0]})
        with pytest.raises(ValueError, match="Score column"):
            build_rank_weight_targets(pred)

    def test_missing_instrument_column_fails(self):
        pred = pd.DataFrame({"score": [1.0], "other": ["x"]})
        with pytest.raises(ValueError, match="Instrument column"):
            build_rank_weight_targets(pred)

    def test_empty_input_returns_empty(self):
        pred = pd.DataFrame(columns=["trade_date", "instrument", "score"])
        result = build_rank_weight_targets(pred)
        assert len(result) == 0
        assert "trade_date" in result.columns


class TestTradeDateFallback:
    def test_trade_date_preserved(self):
        pred = _predictions(5)
        result = build_rank_weight_targets(pred, trade_date="2026-06-20")
        assert (result["trade_date"] == "2026-06-20").all()

    def test_trade_date_from_prediction(self):
        pred = _predictions(5)
        result = build_rank_weight_targets(pred)
        assert (result["trade_date"] == "2026-06-15").all()
