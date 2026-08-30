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
        for name in (
            "decile_returns", "topk_daily", "ranking_stability",
            "neutralized_rank_ic",
        ):
            assert (output_dir / f"{name}.parquet").exists() or (
                output_dir / f"{name}.csv"
            ).exists()
        manifest = __import__("json").loads(
            (output_dir / "manifest.json").read_text()
        )
        assert manifest["schema_version"] == 2
        assert len(manifest["evaluation_identity_sha256"]) == 64
        assert manifest["inputs"]["signal"]["data_sha256"]
        assert manifest["inputs"]["label"]["data_sha256"]
        assert manifest["methodology"]["top_ks"] == [5, 20, 50]
        assert manifest["methodology"]["regime"]["information_lag_sessions"] == 1
        assert manifest["outputs"]["topk_daily"]["row_count"] > 0
        assert "NaN" not in (output_dir / "summary.json").read_text()

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

    def test_evaluation_identity_is_stable_across_repeat_run(self, tmp_path: Path) -> None:
        import json

        sstore = SignalStore(str(tmp_path))
        sstore.save_signal_run(
            "s", "r", _signal(signal_id="s", signal_run_id="r"),
            check_no_lookahead=False,
        )
        LabelStore(str(tmp_path)).save_labels("test_label", _labels())
        evaluator = SignalEvaluator(str(tmp_path))
        evaluator.evaluate(
            signal_id="s", signal_run_id="r", label_id="test_label", overwrite=True
        )
        path = tmp_path / "signals" / "s" / "r" / "eval" / "test_label" / "manifest.json"
        first = json.loads(path.read_text())
        evaluator.evaluate(
            signal_id="s", signal_run_id="r", label_id="test_label", overwrite=True
        )
        second = json.loads(path.read_text())
        assert first["evaluation_identity_sha256"] == second["evaluation_identity_sha256"]
        assert first["outputs"]["topk_daily"] == second["outputs"]["topk_daily"]

    def test_formal_evaluation_requires_pit_label_lineage(self, tmp_path: Path) -> None:
        SignalStore(str(tmp_path)).save_signal_run(
            "s", "r", _signal(signal_id="s", signal_run_id="r"),
            check_no_lookahead=False,
        )
        LabelStore(str(tmp_path)).save_labels("test_label", _labels())
        with pytest.raises(ValueError, match="requires PIT label lineage"):
            SignalEvaluator(str(tmp_path)).evaluate(
                signal_id="s", signal_run_id="r", label_id="test_label",
                overwrite=True, require_pit_lineage=True,
            )

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

    def test_ic_distribution_fields_in_result(self, tmp_path: Path) -> None:
        sstore = SignalStore(str(tmp_path))
        sstore.save_signal_run("s", "r",
                               _signal(n_dates=10, n_inst=30, signal_id="s", signal_run_id="r"),
                               check_no_lookahead=False)
        lstore = LabelStore(str(tmp_path))
        lstore.save_labels("test_label", _labels(n_dates=10, n_inst=30))

        evaluator = SignalEvaluator(str(tmp_path))
        result = evaluator.evaluate(
            signal_id="s", signal_run_id="r", label_id="test_label", overwrite=True,
        )
        assert result.ic_positive_ratio is not None
        assert 0 <= result.ic_positive_ratio <= 1
        assert result.ic_extreme_ratio is not None

    def test_decay_and_regime_artifacts_written(self, tmp_path: Path) -> None:
        sstore = SignalStore(str(tmp_path))
        sstore.save_signal_run("s", "r",
                               _signal(n_dates=10, n_inst=30, signal_id="s", signal_run_id="r"),
                               check_no_lookahead=False)
        lstore = LabelStore(str(tmp_path))
        lstore.save_labels("test_label", _labels(n_dates=10, n_inst=30))

        evaluator = SignalEvaluator(str(tmp_path))
        evaluator.evaluate(
            signal_id="s", signal_run_id="r", label_id="test_label", overwrite=True,
        )
        output_dir = tmp_path / "signals" / "s" / "r" / "eval" / "test_label"
        assert (output_dir / "decay.parquet").exists() or (output_dir / "decay.csv").exists()
        assert (output_dir / "regime_ic.parquet").exists() or (output_dir / "regime_ic.csv").exists()

    def test_regime_ic_in_summary(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """With valid index CSV, regime IC appears in summary."""
        # A declining 80-session history is known by T-1 and classifies the
        # evaluation dates as downtrend.  Same-day return is irrelevant.
        regime_dates = pd.bdate_range(end="2026-06-19", periods=80)
        idx_df = pd.DataFrame({
            "trade_date": regime_dates,
            "close": __import__("numpy").linspace(200.0, 100.0, len(regime_dates)),
        })
        monkeypatch.setattr(
            "qsys.feature.groups.index_context.load_index_daily",
            lambda *a, **kw: idx_df,
        )

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
        assert result.regime_ic is not None, f"regime_ic is None; regime_df empty or except triggered"
        assert "downtrend" in result.regime_ic


class TestIcDistributionStats:
    """Tests for _ic_distribution_stats."""

    def test_positive_ratio(self) -> None:
        from qsys.research.evaluation import _ic_distribution_stats
        s = pd.Series([0.1, -0.05, 0.2, 0.0, 0.15])
        stats = _ic_distribution_stats(s)
        # 3 positive out of 5 (0.0 is not positive)
        assert stats["positive_ratio"] == 0.6

    def test_quantiles(self) -> None:
        from qsys.research.evaluation import _ic_distribution_stats
        s = pd.Series([float(i) for i in range(1, 101)])
        stats = _ic_distribution_stats(s)
        q = stats["quantiles"]
        assert abs(q["50%"] - 50.5) < 1.0

    def test_extreme_ratio(self) -> None:
        from qsys.research.evaluation import _ic_distribution_stats
        # mean=10, std≈31.6; 100 is > 2σ from mean, zeros aren't
        s = pd.Series([0.0, 0.0, 0.0, 100.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        stats = _ic_distribution_stats(s)
        assert stats["extreme_ratio"] == 0.1

    def test_short_series_none(self) -> None:
        from qsys.research.evaluation import _ic_distribution_stats
        s = pd.Series([float("nan")])
        stats = _ic_distribution_stats(s)
        assert stats["positive_ratio"] is None

    def test_one_value_none(self) -> None:
        from qsys.research.evaluation import _ic_distribution_stats
        s = pd.Series([0.1])
        stats = _ic_distribution_stats(s)
        assert stats["positive_ratio"] is None


class TestIcDecay:
    """Tests for compute_ic_decay."""

    def test_decay_pattern(self) -> None:
        from qsys.research.evaluation import compute_ic_decay
        rng = __import__("numpy").random.default_rng(42)
        dates = pd.bdate_range("2026-01-05", periods=12).strftime("%Y-%m-%d")
        instruments = [f"S{i:03d}" for i in range(40)]
        signal_rows = []
        label_rows = []
        for date in dates:
            scores = rng.normal(size=len(instruments))
            for instrument, score in zip(instruments, scores):
                signal_rows.append({
                    "trade_date": date, "instrument": instrument,
                    "score": score,
                })
                label_rows.append({
                    "trade_date": date, "instrument": instrument,
                    "label_value": score,
                })
        decay = compute_ic_decay(
            pd.DataFrame(signal_rows), pd.DataFrame(label_rows), lags=(0, 1, 5)
        )
        assert decay.loc[decay["lag_sessions"] == 0, "rank_ic_mean"].iloc[0] == pytest.approx(1.0)
        assert abs(decay.loc[decay["lag_sessions"] == 5, "rank_ic_mean"].iloc[0]) < 0.2

    def test_short_series_empty(self) -> None:
        from qsys.research.evaluation import compute_ic_decay
        decay = compute_ic_decay(pd.DataFrame(), pd.DataFrame())
        assert decay.empty

    def test_known_lags(self) -> None:
        from qsys.research.evaluation import compute_ic_decay
        signal = _signal(n_dates=5, n_inst=20)
        labels = _labels(n_dates=5, n_inst=20)
        decay = compute_ic_decay(signal, labels, lags=(0, 2))
        assert decay["lag_sessions"].tolist() == [0, 2]
        assert decay.loc[0, "n_days"] == 5

    def test_decay_columns(self) -> None:
        from qsys.research.evaluation import compute_ic_decay
        decay = compute_ic_decay(_signal(), _labels(), lags=(0,))
        assert list(decay.columns) == [
            "lag_sessions", "n_obs", "n_days", "ic_mean", "ic_std", "icir",
            "rank_ic_mean", "rank_ic_std", "rank_icir",
        ]


class TestComputeRegimeIC:
    """Tests for compute_regime_ic."""

    def test_regime_classification(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With known index returns, regime split is correct."""
        from qsys.research.evaluation import compute_regime_ic
        # Two-session trends are shifted one full session before use.
        dates = pd.bdate_range("2026-01-02", periods=10)
        idx_df = pd.DataFrame({
            "trade_date": dates,
            "close": [100.0, 110.0, 121.0, 133.0, 146.0, 130.0, 115.0, 100.0, 90.0, 80.0],
        })
        monkeypatch.setattr(
            "qsys.feature.groups.index_context.load_index_daily",
            lambda *a, **kw: idx_df,
        )
        ic_df = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "ic": [0.1, -0.05, 0.2, -0.1, 0.1, 0.2, -0.2, -0.1, 0.05, -0.05],
        })
        regime_df = compute_regime_ic(
            ic_df,
            trend_lookback_sessions=2,
            uptrend_threshold=0.05,
            downtrend_threshold=-0.05,
            information_lag_sessions=1,
        )
        assert not regime_df.empty, "regime_df should not be empty with valid index data"
        regimes = set(regime_df["regime"])
        assert "uptrend" in regimes
        assert "downtrend" in regimes

    def test_empty_ic(self) -> None:
        from qsys.research.evaluation import compute_regime_ic
        ic_df = pd.DataFrame()
        regime_df = compute_regime_ic(ic_df)
        assert regime_df.empty


def test_overlap_robustness_reports_every_horizon_offset() -> None:
    from qsys.research.evaluation import compute_overlap_robustness

    result = compute_overlap_robustness(pd.Series(range(30)), horizon=5)
    assert result["block_bootstrap"]["block_length"] == 5
    assert result["newey_west"]["max_lag"] == 4
    assert [row["offset"] for row in result["non_overlapping_offsets"]] == list(range(5))


def test_topk_hit_and_stability_contracts() -> None:
    from qsys.research.evaluation import compute_rank_stability, compute_topk_metrics

    joined = join_signal_label(
        _signal(n_dates=3, n_inst=60),
        _labels(n_dates=3, n_inst=60),
    )
    topk, summary = compute_topk_metrics(joined, random_reps=20)
    assert set(topk["top_k"]) == {5, 20, 50}
    assert summary["contract"] == "eligible_executable_label_topk_v1"
    assert summary["by_k"]["5"]["hit_recall_at_k"] > 0.5
    stability, stability_summary = compute_rank_stability(joined)
    assert not stability.empty
    assert set(stability_summary["by_k"]) == {"5", "20", "50"}
