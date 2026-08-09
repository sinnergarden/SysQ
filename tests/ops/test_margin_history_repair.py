from __future__ import annotations

from pathlib import Path

import pandas as pd

from qsys.ops.raw_sync import (
    inspect_margin_history_coverage,
    patch_margin_history_frame,
    run_margin_history_repair,
)


class _Store:
    def __init__(self, frames: dict[str, pd.DataFrame]):
        self.frames = {symbol: frame.copy() for symbol, frame in frames.items()}

    def get_calendar(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "cal_date": ["20260806", "20260807", "20260808"],
                "is_open": [1, 1, 0],
            }
        )

    def load_daily(self, symbol: str) -> pd.DataFrame | None:
        frame = self.frames.get(symbol)
        return None if frame is None else frame.copy()

    def save_daily(
        self,
        rows: pd.DataFrame,
        symbol: str,
        existing_df: pd.DataFrame | None = None,
    ) -> None:
        existing = existing_df.copy() if existing_df is not None else pd.DataFrame()
        combined = pd.concat([existing, rows], ignore_index=True)
        self.frames[symbol] = combined.drop_duplicates(
            subset=["trade_date"], keep="last"
        ).sort_values("trade_date").reset_index(drop=True)


class _Collector:
    def _fetch_by_date_range(self, interface, symbols, start_date, end_date):
        assert interface == "margin"
        assert symbols is None
        return pd.DataFrame(
            {
                "ts_code": ["AAA", "AAA", "BBB", "BBB"],
                "trade_date": ["20260806", "20260807", "20260806", "20260807"],
                "rzye": [10.0, 11.0, 20.0, 21.0],
            }
        )

    def _get_interface_rename(self, interface):
        assert interface == "margin"
        return {"rzye": "margin_balance"}


def _frame(close: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": ["2026-08-06", "2026-08-07"],
            "close": [close, close + 1.0],
            "margin_balance": [pd.NA, pd.NA],
        }
    )


def test_patch_margin_history_preserves_non_margin_columns():
    existing = _frame(100.0)
    patch = pd.DataFrame(
        {
            "trade_date": ["20260807"],
            "margin_balance": [12.0],
        }
    )

    updated, changed = patch_margin_history_frame(existing, patch)

    assert changed == 1
    assert updated["close"].tolist() == [100.0, 101.0]
    assert pd.isna(updated.loc[0, "margin_balance"])
    assert updated.loc[1, "margin_balance"] == 12.0


def test_margin_history_repair_fetches_gaps_and_refreshes_qlib(tmp_path: Path):
    store = _Store({"AAA": _frame(100.0), "BBB": _frame(200.0)})
    refresh_calls: list[dict] = []

    def _refresh(*args, **kwargs):
        refresh_calls.append({"args": args, "kwargs": kwargs})
        return {"summary": {"qlib_update_status": "success"}}

    result = run_margin_history_repair(
        symbols=["AAA", "BBB"],
        start_date="2026-08-06",
        end_date="2026-08-07",
        min_active=2,
        apply=True,
        output_dir=tmp_path,
        store=store,
        collector=_Collector(),
        qlib_refresh_fn=_refresh,
    )

    assert result["status"] == "success"
    assert result["gap_dates_after"] == []
    assert result["affected_symbol_count"] == 2
    assert len(refresh_calls) == 1
    assert store.frames["AAA"]["close"].tolist() == [100.0, 101.0]
    coverage = inspect_margin_history_coverage(
        store,
        symbols=["AAA", "BBB"],
        open_dates=["2026-08-06", "2026-08-07"],
    )
    assert coverage["minimum_active"] == 2
