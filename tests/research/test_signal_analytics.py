"""Tests for SignalAnalytics — DuckDB-powered cross-signal queries."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from qsys.research.signal_analytics import SignalAnalytics


def _populate_research_root(tmp_path: Path) -> None:
    """Create fixture signal + label parquet files."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    # ── Signals ──
    sig_run_dir = tmp_path / "signals" / "sig_a" / "run_001"
    sig_run_dir.mkdir(parents=True)
    dates = ["2026-01-05", "2026-01-06", "2026-01-07"]
    insts = [f"000{i:04d}.SZ" for i in range(20)]
    rows_sig = []
    for td in dates:
        for inst in insts:
            rows_sig.append({
                "trade_date": td, "data_date": td, "instrument": inst,
                "signal_id": "sig_a", "signal_run_id": "run_001",
                "score": 1.0 if inst == "0000000.SZ" else 0.5,
            })
    pq.write_table(pa.Table.from_pylist(rows_sig), str(sig_run_dir / "predictions.parquet"))

    # ── Labels ──
    lbl_dir = tmp_path / "labels" / "l1"
    lbl_dir.mkdir(parents=True)
    rows_lbl = []
    for td in dates:
        for inst in insts:
            rows_lbl.append({
                "trade_date": td, "instrument": inst,
                "label_id": "l1", "horizon": 5,
                "label_value": 0.02 if inst == "0000000.SZ" else -0.01,
            })
    pq.write_table(pa.Table.from_pylist(rows_lbl), str(lbl_dir / "labels.parquet"))


class TestSignalAnalytics:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path: Path) -> None:
        _populate_research_root(tmp_path)
        self.sa = SignalAnalytics(str(tmp_path))

    def test_list_signals(self) -> None:
        df = self.sa.list_signals()
        assert len(df) == 1
        assert df["signal_id"].iloc[0] == "sig_a"

    def test_list_labels(self) -> None:
        df = self.sa.list_labels()
        assert len(df) == 1
        assert df["label_id"].iloc[0] == "l1"

    def test_compute_ic_matrix(self) -> None:
        result = self.sa.compute_ic_matrix()
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 1
        assert result["signal_id"].iloc[0] == "sig_a"
        assert result["label_id"].iloc[0] == "l1"
        # sig_a has higher score for 0000000.SZ, which has positive label → positive IC
        assert result["ic_mean"].iloc[0] is not None
        assert result["ic_mean"].iloc[0] > 0

    def test_compute_rank_ic_matrix(self) -> None:
        result = self.sa.compute_rank_ic_matrix()
        assert len(result) == 1
        assert result["rank_ic_mean"].iloc[0] is not None
        assert result["rank_ic_mean"].iloc[0] > 0

    def test_daily_ic(self) -> None:
        df = self.sa.daily_ic("sig_a")
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 3  # 3 dates
        assert list(df.columns) == ["trade_date", "ic", "n"]

    def test_ic_matrix_with_date_filter(self) -> None:
        result = self.sa.compute_ic_matrix(start_date="2026-01-06", end_date="2026-01-07")
        assert len(result) == 1
        assert result["ic_mean"].iloc[0] is not None

    def test_min_count_filters_insufficient_dates(self) -> None:
        """high min_count should exclude all pairs."""
        result_loose = self.sa.compute_ic_matrix(min_count=5)
        assert len(result_loose) == 1

        result_strict = self.sa.compute_ic_matrix(min_count=999)
        assert len(result_strict) == 0

    def test_query(self) -> None:
        df = self.sa.query("SELECT 1 AS a")
        assert df["a"].iloc[0] == 1

    def test_empty_root(self, tmp_path: Path) -> None:
        sa = SignalAnalytics(str(tmp_path / "empty"))
        assert len(sa.list_signals()) == 0
        assert len(sa.list_labels()) == 0
        assert len(sa.compute_ic_matrix()) == 0

    def test_close_and_reopen(self) -> None:
        self.sa.close()
        sa2 = SignalAnalytics(str(self.sa.root))
        df = sa2.list_signals()
        assert len(df) == 1
        sa2.close()
