"""Tests for qsys.research.evaluation."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from qsys.research.evaluation import (
    SignalEvaluator,
    compute_coverage,
    compute_daily_ic,
    compute_daily_rank_ic,
    compute_group_returns,
    join_signal_label,
)
from qsys.label.store import LabelStore
from qsys.signal.store import SignalStore


def _signal(n_dates: int = 5, n_inst: int = 10,
            signal_id: str = "test_sig", signal_run_id: str = "test_run") -> pd.DataFrame:
    rows = []
    for di in range(n_dates):
        for ii in range(n_inst):
            rows.append({
                "trade_date": f"2026-06-{15 + di:02d}",
                "data_date": f"2026-06-{14 + di - 2:02d}",
                "instrument": f"000{ii:03d}.SZ",
                "signal_id": signal_id,
                "signal_run_id": signal_run_id,
                "score": float(ii) / n_inst,
            })
    return pd.DataFrame(rows)


def _labels(n_dates: int = 5, n_inst: int = 10, label_id: str = "test_label") -> pd.DataFrame:
    import random
    rng = random.Random(42)
    rows = []
    for di in range(n_dates):
        for ii in range(n_inst):
            rows.append({
                "trade_date": f"2026-06-{15 + di:02d}",
                "instrument": f"000{ii:03d}.SZ",
                "label_id": label_id,
                "horizon": 5,
                "label_value": float(ii) / n_inst * 0.1 + 0.001 * di + rng.uniform(-0.005, 0.005),
            })
    return pd.DataFrame(rows)


class TestJoinSignalLabel:
    def test_joins_on_date_and_instrument(self) -> None:
        sig = _signal(n_dates=2, n_inst=3)
        lbl = _labels(n_dates=2, n_inst=3)
        joined = join_signal_label(sig, lbl)
        assert len(joined) == 6
        assert "label_value" in joined.columns

    def test_excludes_invalid_signal_rows(self) -> None:
        sig = _signal(n_dates=1, n_inst=5)
        sig["is_valid"] = [False, True, True, False, True]
        lbl = _labels(n_dates=1, n_inst=5)
        joined = join_signal_label(sig, lbl)
        assert len(joined) == 3

    def test_excludes_invalid_label_rows(self) -> None:
        sig = _signal(n_dates=1, n_inst=5)
        lbl = _labels(n_dates=1, n_inst=5)
        lbl["is_valid"] = [True, False, True, True, True]
        joined = join_signal_label(sig, lbl)
        assert len(joined) == 4

    def test_excludes_null_score(self) -> None:
        sig = _signal(n_dates=1, n_inst=5)
        sig.loc[0, "score"] = None
        lbl = _labels(n_dates=1, n_inst=5)
        joined = join_signal_label(sig, lbl)
        assert len(joined) == 4

    def test_missing_score_column_raises(self) -> None:
        sig = pd.DataFrame({"trade_date": ["d"], "instrument": ["i"]})
        lbl = pd.DataFrame({"trade_date": ["d"], "instrument": ["i"], "label_value": [0.1]})
        with pytest.raises(ValueError, match="Score column"):
            join_signal_label(sig, lbl)

    def test_missing_label_value_raises(self) -> None:
        sig = pd.DataFrame({"trade_date": ["d"], "instrument": ["i"], "score": [0.5]})
        lbl = pd.DataFrame({"trade_date": ["d"], "instrument": ["i"]})
        with pytest.raises(ValueError, match="label_value"):
            join_signal_label(sig, lbl)

    def test_score_column_parameter(self) -> None:
        sig = _signal().rename(columns={"score": "pred"})
        lbl = _labels()
        joined = join_signal_label(sig, lbl, score_column="pred")
        assert len(joined) > 0
        assert "pred" in joined.columns


class TestComputeDailyIC:
    def test_positive_ic(self) -> None:
        joined = _signal(n_dates=3, n_inst=20)
        lbl = _labels(n_dates=3, n_inst=20)
        joined = join_signal_label(joined, lbl)
        ic_df = compute_daily_ic(joined, min_count=5)
        assert len(ic_df) == 3
        assert ic_df["ic"].notna().all()
        assert ic_df["ic"].iloc[0] > 0

    def test_min_count_produces_nan(self) -> None:
        joined = _signal(n_dates=1, n_inst=3)
        lbl = _labels(n_dates=1, n_inst=3)
        joined = join_signal_label(joined, lbl)
        ic_df = compute_daily_ic(joined, min_count=10)
        assert pd.isna(ic_df["ic"].iloc[0])

    def test_returns_date_and_n_columns(self) -> None:
        joined = _signal(n_dates=2, n_inst=10)
        lbl = _labels(n_dates=2, n_inst=10)
        joined = join_signal_label(joined, lbl)
        ic_df = compute_daily_ic(joined)
        assert list(ic_df.columns) == ["date", "ic", "n"]


class TestComputeDailyRankIC:
    def test_positive_rank_ic(self) -> None:
        joined = _signal(n_dates=3, n_inst=20)
        lbl = _labels(n_dates=3, n_inst=20)
        joined = join_signal_label(joined, lbl)
        ic_df = compute_daily_rank_ic(joined, min_count=5)
        assert len(ic_df) == 3
        assert ic_df["rank_ic"].notna().all()
        assert ic_df["rank_ic"].iloc[0] > 0

    def test_min_count_produces_nan(self) -> None:
        joined = _signal(n_dates=1, n_inst=2)
        lbl = _labels(n_dates=1, n_inst=2)
        joined = join_signal_label(joined, lbl)
        ic_df = compute_daily_rank_ic(joined, min_count=10)
        assert pd.isna(ic_df["rank_ic"].iloc[0])


class TestGroupReturns:
    def test_groups_1_to_n(self) -> None:
        joined = _signal(n_dates=2, n_inst=100)
        lbl = _labels(n_dates=2, n_inst=100)
        joined = join_signal_label(joined, lbl)
        grp = compute_group_returns(joined, n_groups=5)
        assert sorted(grp["group_id"].unique()) == [1, 2, 3, 4, 5]

    def test_group_1_lowest_score_group_n_highest(self) -> None:
        joined = _signal(n_dates=1, n_inst=100)
        lbl = _labels(n_dates=1, n_inst=100)
        joined = join_signal_label(joined, lbl)
        grp = compute_group_returns(joined, n_groups=5)
        g1 = grp[grp["group_id"] == 1]["mean_return"].values[0]
        g5 = grp[grp["group_id"] == 5]["mean_return"].values[0]
        assert g1 < g5

    def test_has_count_column(self) -> None:
        joined = _signal(n_dates=1, n_inst=50)
        lbl = _labels(n_dates=1, n_inst=50)
        joined = join_signal_label(joined, lbl)
        grp = compute_group_returns(joined, n_groups=5)
        assert "count" in grp.columns

    def test_small_n_does_not_crash(self) -> None:
        joined = _signal(n_dates=1, n_inst=3)
        lbl = _labels(n_dates=1, n_inst=3)
        joined = join_signal_label(joined, lbl)
        grp = compute_group_returns(joined, n_groups=5)
        assert len(grp) > 0


class TestCoverage:
    def test_full_coverage(self) -> None:
        sig = _signal(n_dates=2, n_inst=10)
        lbl = _labels(n_dates=2, n_inst=10)
        joined = join_signal_label(sig, lbl)
        cov = compute_coverage(sig, joined)
        assert cov["coverage"].notna().all()
        assert cov["coverage"].max() == 1.0

    def test_partial_coverage(self) -> None:
        sig = _signal(n_dates=2, n_inst=10)
        lbl = _labels(n_dates=2, n_inst=5)
        joined = join_signal_label(sig, lbl)
        cov = compute_coverage(sig, joined)
        assert cov["coverage"].iloc[0] < 1.0

    def test_zero_coverage(self) -> None:
        sig = _signal(n_dates=1, n_inst=10)
        lbl = pd.DataFrame(columns=["trade_date", "instrument", "label_id", "horizon", "label_value"])
        joined = join_signal_label(sig, lbl)
        cov = compute_coverage(sig, joined)
        assert cov["coverage"].iloc[0] == 0.0


class TestSignalEvaluator:
    def test_evaluate_writes_artifacts(self, tmp_path: Path) -> None:
        sstore = SignalStore(str(tmp_path))
        sstore.save_signal_run("test_sig", "test_run", _signal(), check_no_lookahead=False)
        lstore = LabelStore(str(tmp_path))
        lstore.save_labels("test_label", _labels())

        evaluator = SignalEvaluator(str(tmp_path))
        result = evaluator.evaluate(
            signal_id="test_sig", signal_run_id="test_run", label_id="test_label",
            overwrite=True,
        )
        assert result.n_obs > 0

        output_dir = tmp_path / "signals" / "test_sig" / "test_run" / "eval" / "test_label"
        assert (output_dir / "summary.json").exists()
        assert (output_dir / "ic_daily.parquet").exists() or (output_dir / "ic_daily.csv").exists()
        assert (output_dir / "group_returns.parquet").exists() or (output_dir / "group_returns.csv").exists()
        assert (output_dir / "coverage.parquet").exists() or (output_dir / "coverage.csv").exists()

    def test_overwrite_false_raises(self, tmp_path: Path) -> None:
        sstore = SignalStore(str(tmp_path))
        sstore.save_signal_run("s", "r", _signal(signal_id="s", signal_run_id="r"),
                              check_no_lookahead=False)
        lstore = LabelStore(str(tmp_path))
        lstore.save_labels("test_label", _labels())

        evaluator = SignalEvaluator(str(tmp_path))
        evaluator.evaluate(signal_id="s", signal_run_id="r", label_id="test_label", overwrite=True)
        with pytest.raises(FileExistsError):
            evaluator.evaluate(signal_id="s", signal_run_id="r", label_id="test_label")

    def test_overwrite_true_succeeds(self, tmp_path: Path) -> None:
        sstore = SignalStore(str(tmp_path))
        sstore.save_signal_run("test_sig", "test_run", _signal(), check_no_lookahead=False)
        lstore = LabelStore(str(tmp_path))
        lstore.save_labels("test_label", _labels())

        evaluator = SignalEvaluator(str(tmp_path))
        evaluator.evaluate(signal_id="test_sig", signal_run_id="test_run", label_id="test_label", overwrite=True)
        evaluator.evaluate(signal_id="test_sig", signal_run_id="test_run", label_id="test_label", overwrite=True)

    def test_empty_join_graceful(self, tmp_path: Path) -> None:
        sstore = SignalStore(str(tmp_path))
        sstore.save_signal_run("test_sig", "test_run", _signal(), check_no_lookahead=False)
        lstore = LabelStore(str(tmp_path))
        lbl = _labels()
        lbl["trade_date"] = "2027-01-01"
        lstore.save_labels("test_label", lbl)

        evaluator = SignalEvaluator(str(tmp_path))
        result = evaluator.evaluate(
            signal_id="test_sig", signal_run_id="test_run", label_id="test_label", overwrite=True,
        )
        assert result.n_obs == 0
        assert result.n_days == 0

    def test_ic_stats_in_result(self, tmp_path: Path) -> None:
        sstore = SignalStore(str(tmp_path))
        sstore.save_signal_run("s", "r",
                               _signal(n_dates=5, n_inst=30, signal_id="s", signal_run_id="r"),
                               check_no_lookahead=False)
        lstore = LabelStore(str(tmp_path))
        lstore.save_labels("test_label", _labels(n_dates=5, n_inst=30))

        evaluator = SignalEvaluator(str(tmp_path))
        result = evaluator.evaluate(
            signal_id="s", signal_run_id="r", label_id="test_label", overwrite=True,
        )
        assert result.ic_mean is not None
        assert result.rank_ic_mean is not None
        assert result.coverage_mean is not None
