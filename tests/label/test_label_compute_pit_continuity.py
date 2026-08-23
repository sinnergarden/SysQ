"""PIT label lookahead regression tests (multi-span membership).

Guards the fix to ``compute_forward_return``: for a multi-span PIT registry,
the forward-return shift must be computed on CONTINUOUS trading history
(trading-day offset) and membership filtering applied only AFTER the label has
matured.  Before the fix the registry span-clip removed the gap rows first, so
``shift(-horizon)`` jumped across the membership gap to a far-future price —
a cross-span lookahead (e.g. 603256.SH label of +25.2% vs true -9.85%).

Scenario used below:
    instrument 000001.SZ, continuous history 2020-01-02 → 2020-12-31,
    membership spans [2020-01-02, 2020-03-31] and [2020-06-01, 2020-12-31]
    (gap = 2020-04-01..2020-05-31).  Prices rise by 1.0 per trading day.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import qsys.label.compute as compute_mod


_SPAN1_END = "2020-03-31"
_SPAN2_START = "2020-06-01"
_INSTRUMENT = "000001.SZ"


def _business_dates(start: str = "2020-01-02", end: str = "2020-12-31") -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, end)]


def _price_series(dates: list[str], base: float = 100.0) -> list[float]:
    return [base + i for i in range(len(dates))]


class _PitFakeAdapter:
    """QlibAdapter stand-in: returns CONTINUOUS history (no span clipping).

    ``registry_dir`` holds ``instruments/multi_span_test.txt`` which the
    resolver reads to obtain the multi-span membership.  ``get_features`` is
    called with the instrument LIST (continuous path) and returns the full
    synthetic OHLC-frame, honoring start/end.
    """

    def __init__(self, registry_dir: Path, dates: list[str], prices: dict[str, list[float]]) -> None:
        self.qlib_dir = registry_dir
        self.dates = dates
        self._price = {inst: {d: p for d, p in zip(dates, px)} for inst, px in prices.items()}

    def init_qlib(self) -> None:
        pass

    def get_features(self, universe, fields, start_time=None, end_time=None,
                     freq="day", inst_processors=None, *, margin_lag_sessions=0):
        insts = [universe] if isinstance(universe, str) else list(universe)
        rows = []
        for inst in insts:
            for d in self.dates:
                if start_time is not None and d < str(start_time).split()[0]:
                    continue
                if end_time is not None and d > str(end_time).split()[0]:
                    continue
                rows.append((d, inst, self._price[inst][d], 1.0))
        idx = pd.MultiIndex.from_tuples([(r[0], r[1]) for r in rows],
                                        names=["datetime", "instrument"])
        return pd.DataFrame({
            "$close": [r[2] for r in rows],
            "$factor": [r[3] for r in rows],
        }, index=idx)


def _write_registry(registry_dir: Path, spans: list[tuple[str, str, str]]) -> None:
    inst_dir = registry_dir / "instruments"
    inst_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"{i}\t{s}\t{e}" for i, s, e in spans]
    (inst_dir / "multi_span_test.txt").write_text("\n".join(lines) + "\n")


def _patch_adapter(monkeypatch, registry_dir: Path, dates: list[str],
                   prices: dict[str, list[float]]) -> None:
    import qsys.data.adapter as adapter_mod
    monkeypatch.setattr(adapter_mod, "QlibAdapter",
                        lambda: _PitFakeAdapter(registry_dir, dates, prices))


def _shift_target_date(dates: list[str], t: str, horizon: int) -> str:
    """Return the date ``horizon`` trading rows after ``t`` in continuous history."""
    return dates[dates.index(t) + horizon]


class TestPitContinuity:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path) -> None:
        self.dates = _business_dates()
        self.prices = _price_series(self.dates)
        _write_registry(tmp_path, [
            (_INSTRUMENT, self.dates[0], _SPAN1_END),
            (_INSTRUMENT, _SPAN2_START, self.dates[-1]),
        ])
        self.registry_dir = tmp_path

    def test_multi_span_label_does_not_cross_gap(self, monkeypatch) -> None:
        """Label at a span-1 date uses the +horizon continuous price, not a
        post-gap (far-future) price, and gap rows are dropped from output."""
        horizon = 5
        _patch_adapter(monkeypatch, self.registry_dir, self.dates,
                       {_INSTRUMENT: self.prices})
        out = compute_mod.compute_forward_return(
            "multi_span_test", horizon, "2020-01-01", "2020-12-31",
            norm_type="", clip_val=None,
            label_id_override="fwd_ret_5d_raw_pit_test",
        )
        # T sits 5 trading days before span1 end: the continuous shift target
        # (T+5 rows) lands inside the gap, whereas the buggy span-clipped
        # shift would jump to span2's first date (2020-06-01).
        t = self.dates[self.dates.index(_SPAN1_END) - horizon]
        row = out[(out["trade_date"] == t) & (out["instrument"] == _INSTRUMENT)]
        assert not row.empty, f"expected a label row at {t}"

        continuous_target = _shift_target_date(self.dates, t, horizon)
        expected = self.prices[self.dates.index(continuous_target)] / \
            self.prices[self.dates.index(t)] - 1.0
        assert np.isclose(row["label_value"].iloc[0], expected, atol=1e-6), \
            f"label at {t} must use continuous {continuous_target}, got {row['label_value'].iloc[0]}"

        # Span-clipped shift would have targeted span2's first date — verify
        # the value is NOT that (and that gap rows are not present).
        buggy_target = _SPAN2_START
        buggy_value = self.prices[self.dates.index(buggy_target)] / \
            self.prices[self.dates.index(t)] - 1.0
        assert not np.isclose(row["label_value"].iloc[0], buggy_value), \
            "label must not reference a post-gap price"

        gap_dates = set(self.dates[self.dates.index(_SPAN1_END) + 1:
                                   self.dates.index(_SPAN2_START)])
        present_gaps = set(out[out["instrument"] == _INSTRUMENT]["trade_date"]) & gap_dates
        assert not present_gaps, f"gap rows must be dropped, got {sorted(present_gaps)[:5]}"

    def test_compares_against_single_instrument_continuous(self, monkeypatch) -> None:
        """PIT labels (member dates only) must equal the single-instrument
        continuous-history labels on the overlapping dates."""
        horizon = 5
        _patch_adapter(monkeypatch, self.registry_dir, self.dates,
                       {_INSTRUMENT: self.prices})
        pit = compute_mod.compute_forward_return(
            "multi_span_test", horizon, "2020-01-01", "2020-12-31",
            norm_type="", clip_val=None, label_id_override="pit",
        )
        cont = compute_mod.compute_forward_return(
            [_INSTRUMENT], horizon, "2020-01-01", "2020-12-31",
            norm_type="", clip_val=None, label_id_override="cont",
        )
        merged = pit.merge(cont, on=["trade_date", "instrument"],
                           suffixes=("_pit", "_cont"))
        assert not merged.empty
        assert np.allclose(merged["label_value_pit"], merged["label_value_cont"], atol=1e-6)
        # Continuous path covers strictly more dates (includes the gap).
        assert cont["trade_date"].nunique() > pit["trade_date"].nunique()

    def test_label_date_plus_horizon_trading_days(self, monkeypatch) -> None:
        """For a member date T, label = close[T+180 trading days]/close[T]-1 —
        the mapping is a trading-day offset on continuous history."""
        horizon = 180
        _patch_adapter(monkeypatch, self.registry_dir, self.dates,
                       {_INSTRUMENT: self.prices})
        out = compute_mod.compute_forward_return(
            "multi_span_test", horizon, "2020-01-01", "2020-12-31",
            norm_type="", clip_val=None, label_id_override="pit180",
        )
        # A member date comfortably inside span1 with T+180 rows available.
        t = self.dates[10]  # 2020-01-16-ish, well inside span1
        target = _shift_target_date(self.dates, t, horizon)
        expected = self.prices[self.dates.index(target)] / \
            self.prices[self.dates.index(t)] - 1.0
        row = out[(out["trade_date"] == t) & (out["instrument"] == _INSTRUMENT)]
        assert not row.empty
        assert np.isclose(row["label_value"].iloc[0], expected, atol=1e-6)
        # Sanity: the trading-day offset is NOT a calendar offset over the gap.
        cal_offset = pd.Timestamp(t) + pd.Timedelta(days=horizon)
        assert str(cal_offset.date()) != target, "180 trading days != 180 calendar days"
