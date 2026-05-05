import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from qsys.ops.full_universe_backfill import build_full_universe_backfill_plan, run_full_universe_backfill


class _FakeStore:
    def __init__(self, stock_df, frames=None):
        self.stock_df = stock_df
        self.frames = frames or {}

    def get_stock_list(self):
        return self.stock_df.copy()

    def load_daily(self, symbol):
        return self.frames.get(symbol)

    def get_global_latest_date(self):
        return "2026-04-17"


class _FakeCollector:
    def __init__(self):
        self.calls = []

    def update_universe_history(self, universe=None, start_date=None, end_date=None, **kwargs):
        self.calls.append((universe, start_date, end_date))


class _FakeAdapter:
    def get_last_qlib_date(self):
        return pd.Timestamp("2026-04-17")

    def refresh_qlib_date(self):
        return None


def test_build_full_universe_backfill_plan_splits_missing_and_stale_batches():
    stock_df = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
            "list_date": ["20100101", "20100101", "20100101"],
        }
    )
    frames = {
        "000001.SZ": pd.DataFrame({"trade_date": ["20260417"]}),
        "000002.SZ": pd.DataFrame({"trade_date": ["20260410"]}),
    }
    rows, plan = build_full_universe_backfill_plan(
        store=_FakeStore(stock_df, frames),
        target_date="2026-04-17",
        batch_size=1,
        stale_lookback_days=5,
    )
    assert len(rows) == 3
    assert sum(1 for row in rows if row["needs_backfill"]) == 2
    assert [row["batch_type"] for row in plan] == ["missing_raw", "stale_raw"]
    assert plan[0]["symbols"] == "000003.SZ"
    assert plan[1]["symbols"] == "000002.SZ"


def test_run_full_universe_backfill_applies_batches_and_triggers_selected_qlib_refresh():
    stock_df = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ"],
            "list_date": ["20100101", "20100101"],
        }
    )
    frames = {
        "000001.SZ": pd.DataFrame({"trade_date": ["20260417"]}),
    }
    collector = _FakeCollector()
    refresh_result = {"summary": {"qlib_update_status": "success"}, "rows": []}
    with tempfile.TemporaryDirectory() as tmpdir, patch(
        "qsys.ops.full_universe_backfill.StockDataStore", return_value=_FakeStore(stock_df, frames)
    ), patch("qsys.ops.full_universe_backfill.TushareCollector", return_value=collector), patch(
        "qsys.ops.full_universe_backfill.QlibAdapter", return_value=_FakeAdapter()
    ), patch("qsys.ops.full_universe_backfill.refresh_selected_symbols_from_raw", return_value=refresh_result):
        result = run_full_universe_backfill(Path(tmpdir), apply=True, batch_size=10)
    summary = result["summary"]
    assert summary["status"] == "success"
    assert summary["affected_symbol_count"] == 1
    assert collector.calls == [("000002.SZ", "20100101", "20260417")]
    assert summary["qlib_refresh"] == refresh_result
