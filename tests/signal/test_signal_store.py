"""Tests for qsys.signal.store."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from qsys.signal.store import SignalStore


def _make_valid_frame(
    signal_id: str = "alpha_v1",
    signal_run_id: str = "run_001",
    n: int = 3,
) -> pd.DataFrame:
    base = 15
    return pd.DataFrame({
        "trade_date": [f"2026-06-{base + i:02d}" for i in range(n)],
        "data_date": [f"2026-06-{base + i - 3:02d}" for i in range(n)],
        "instrument": [f"00000{i}.SZ" for i in range(n)],
        "signal_id": [signal_id] * n,
        "signal_run_id": [signal_run_id] * n,
        "score": [float(i) for i in range(n)],
    })


class TestSignalStoreSave:
    def test_save_and_load(self, tmp_path: Path) -> None:
        store = SignalStore(str(tmp_path))
        frame = _make_valid_frame()
        path = store.save_signal_run("alpha_v1", "run_001", frame)
        assert path.exists()
        loaded = store.load_signal_run("alpha_v1", "run_001")
        assert len(loaded) == 3
        assert list(loaded.columns)[:6] == ["trade_date", "data_date", "instrument", "signal_id", "signal_run_id", "score"]

    def test_manifest_written(self, tmp_path: Path) -> None:
        store = SignalStore(str(tmp_path))
        store.save_signal_run("alpha_v1", "run_001", _make_valid_frame())
        mf = store.load_manifest("alpha_v1", "run_001")
        assert mf["signal_id"] == "alpha_v1"
        assert mf["signal_run_id"] == "run_001"
        assert mf["row_count"] == 3
        assert "created_at" in mf
        assert len(mf["predictions_sha256"]) == 64
        assert mf["predictions_sha256"] == store.signal_data_sha256(
            "alpha_v1", "run_001"
        )

    def test_missing_required_column_fails(self, tmp_path: Path) -> None:
        store = SignalStore(str(tmp_path))
        bad = pd.DataFrame({"trade_date": ["2026-05-01"]})
        with pytest.raises(ValueError, match="Missing required columns"):
            store.save_signal_run("x", "y", bad)

    def test_missing_signal_run_id_fails(self, tmp_path: Path) -> None:
        store = SignalStore(str(tmp_path))
        bad = pd.DataFrame({
            "trade_date": ["2026-05-01"],
            "data_date": ["2026-04-30"],
            "instrument": ["i"],
            "signal_id": ["x"],
            "signal_run_id": ["y"],
            # missing score
        })
        with pytest.raises(ValueError, match="Missing required columns"):
            store.save_signal_run("x", "y", bad)

    def test_mismatched_signal_id_fails(self, tmp_path: Path) -> None:
        store = SignalStore(str(tmp_path))
        frame = _make_valid_frame(signal_id="other")
        with pytest.raises(ValueError, match="signal_id"):
            store.save_signal_run("expected", "run_001", frame)

    def test_mismatched_signal_run_id_fails(self, tmp_path: Path) -> None:
        store = SignalStore(str(tmp_path))
        frame = _make_valid_frame(signal_run_id="other_run")
        with pytest.raises(ValueError, match="signal_run_id"):
            store.save_signal_run("alpha_v1", "expected", frame)

    def test_overwrite_false_protects_existing(self, tmp_path: Path) -> None:
        store = SignalStore(str(tmp_path))
        store.save_signal_run("alpha_v1", "run_001", _make_valid_frame())
        with pytest.raises(FileExistsError):
            store.save_signal_run("alpha_v1", "run_001", _make_valid_frame())

    def test_overwrite_true_allows_second_save(self, tmp_path: Path) -> None:
        store = SignalStore(str(tmp_path))
        store.save_signal_run("alpha_v1", "run_001", _make_valid_frame(n=3))
        store.save_signal_run("alpha_v1", "run_001", _make_valid_frame(n=5), overwrite=True)
        loaded = store.load_signal_run("alpha_v1", "run_001")
        assert len(loaded) == 5

    def test_manifest_dict_not_mutated(self, tmp_path: Path) -> None:
        store = SignalStore(str(tmp_path))
        manifest = {"signal_kind": "raw", "model_id": "m1"}
        original = dict(manifest)
        store.save_signal_run("a", "r", _make_valid_frame(signal_id="a", signal_run_id="r"),
                              manifest=manifest)
        assert manifest == original

    def test_null_trade_date_fails(self, tmp_path: Path) -> None:
        store = SignalStore(str(tmp_path))
        frame = _make_valid_frame()
        frame.loc[0, "trade_date"] = None
        with pytest.raises(ValueError, match="trade_date.*null"):
            store.save_signal_run("alpha_v1", "run_001", frame)

    def test_null_score_for_valid_row_fails(self, tmp_path: Path) -> None:
        store = SignalStore(str(tmp_path))
        frame = _make_valid_frame()
        frame.loc[0, "score"] = None
        with pytest.raises(ValueError, match="score.*null"):
            store.save_signal_run("alpha_v1", "run_001", frame)

    def test_csv_file_format(self, tmp_path: Path) -> None:
        store = SignalStore(str(tmp_path))
        frame = _make_valid_frame(signal_id="a", signal_run_id="b")
        path = store.save_signal_run("a", "b", frame, file_format="csv")
        assert path.suffix == ".csv"
        loaded = store.load_signal_run("a", "b")
        assert len(loaded) == 3


class TestSignalStoreNoLookahead:
    def test_no_lookahead_passes(self, tmp_path: Path) -> None:
        store = SignalStore(str(tmp_path))
        # 2026-06-15 (Mon), data_date on 2026-06-12 (Fri) = OK
        frame = pd.DataFrame({
            "trade_date": ["2026-06-15", "2026-06-16"],
            "data_date": ["2026-06-12", "2026-06-15"],
            "instrument": ["000001.SZ", "000002.SZ"],
            "signal_id": ["a", "a"],
            "signal_run_id": ["r", "r"],
            "score": [1.0, 2.0],
        })
        store.save_signal_run("a", "r", frame)
        # should not raise

    def test_no_lookahead_violation_fails(self, tmp_path: Path) -> None:
        store = SignalStore(str(tmp_path))
        frame = pd.DataFrame({
            "trade_date": ["2026-06-15"],
            "data_date": ["2026-06-15"],  # same day = violation
            "instrument": ["000001.SZ"],
            "signal_id": ["a"],
            "signal_run_id": ["r"],
            "score": [1.0],
        })
        with pytest.raises(ValueError, match="lookahead violation"):
            store.save_signal_run("a", "r", frame, check_no_lookahead=True)

    def test_no_lookahead_skipped_when_disabled(self, tmp_path: Path) -> None:
        store = SignalStore(str(tmp_path))
        frame = pd.DataFrame({
            "trade_date": ["2026-06-15"],
            "data_date": ["2026-06-15"],
            "instrument": ["000001.SZ"],
            "signal_id": ["a"],
            "signal_run_id": ["r"],
            "score": [1.0],
        })
        # should not raise when check_no_lookahead=False
        store.save_signal_run("a", "r", frame, check_no_lookahead=False)

    def test_timestamp_dates_pass(self, tmp_path: Path) -> None:
        """pd.Timestamp dates should be normalized to strings for comparison."""
        store = SignalStore(str(tmp_path))
        import datetime
        frame = pd.DataFrame({
            "trade_date": [pd.Timestamp("2026-06-15")],
            "data_date": [pd.Timestamp("2026-06-12")],
            "instrument": ["000001.SZ"],
            "signal_id": ["a"],
            "signal_run_id": ["r"],
            "score": [1.0],
        })
        store.save_signal_run("a", "r", frame)

    def test_calendar_resolution_runs_once_per_distinct_trade_date(self) -> None:
        from qsys.signal.store import _check_no_lookahead_on_frame

        frame = pd.DataFrame(
            {
                "trade_date": ["2026-06-15"] * 10_000,
                "data_date": ["2026-06-12"] * 10_000,
            }
        )
        with patch(
            "qsys.signal.store._resolve_prev_trading_day",
            return_value="2026-06-12",
        ) as resolver:
            _check_no_lookahead_on_frame(frame)
        assert resolver.call_count == 1


class TestSignalStoreLoad:
    def test_load_signal_for_date(self, tmp_path: Path) -> None:
        store = SignalStore(str(tmp_path))
        frame = _make_valid_frame(signal_id="a", signal_run_id="r", n=5)
        store.save_signal_run("a", "r", frame)
        df = store.load_signal_for_date("a", "r", "2026-06-15")
        assert len(df) == 1
        assert df.iloc[0]["trade_date"] == "2026-06-15"

    def test_filter_by_date(self, tmp_path: Path) -> None:
        store = SignalStore(str(tmp_path))
        frame = _make_valid_frame(signal_id="a", signal_run_id="r", n=5)
        store.save_signal_run("a", "r", frame)
        df = store.load_signal_run("a", "r", start_date="2026-06-17")
        assert len(df) == 3

    def test_filter_by_instrument(self, tmp_path: Path) -> None:
        store = SignalStore(str(tmp_path))
        frame = _make_valid_frame(signal_id="a", signal_run_id="r", n=5)
        store.save_signal_run("a", "r", frame)
        df = store.load_signal_run("a", "r", instruments=["000000.SZ"])
        assert len(df) == 1

    def test_load_missing_raises(self, tmp_path: Path) -> None:
        store = SignalStore(str(tmp_path))
        with pytest.raises(FileNotFoundError):
            store.load_signal_run("nonexistent", "run")


class TestSignalStoreList:
    def test_list_signal_runs(self, tmp_path: Path) -> None:
        store = SignalStore(str(tmp_path))
        store.save_signal_run("a", "r1", _make_valid_frame(signal_id="a", signal_run_id="r1"))
        store.save_signal_run("a", "r2", _make_valid_frame(n=5, signal_id="a", signal_run_id="r2"))
        store.save_signal_run("b", "r1", _make_valid_frame(signal_id="b", signal_run_id="r1"))
        df = store.list_signal_runs()
        assert len(df) == 3

    def test_list_filter_by_signal_id(self, tmp_path: Path) -> None:
        store = SignalStore(str(tmp_path))
        store.save_signal_run("a", "r1", _make_valid_frame(signal_id="a", signal_run_id="r1"))
        store.save_signal_run("b", "r1", _make_valid_frame(signal_id="b", signal_run_id="r1"))
        df = store.list_signal_runs(signal_id="a")
        assert len(df) == 1
        assert df.iloc[0]["signal_id"] == "a"

    def test_list_empty(self, tmp_path: Path) -> None:
        store = SignalStore(str(tmp_path))
        df = store.list_signal_runs()
        assert len(df) == 0
