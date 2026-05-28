"""Tests for qsys.ops.mtm — data_date resolution."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from qsys.ops.mtm import try_mark_to_market


def _make_trade_data(tmp_path: Path) -> tuple[Path, Path]:
    acct = {"cash": 100000.0, "initial_capital": 100000.0, "initial_cash": 100000.0}
    acct_path = tmp_path / "account.json"
    acct_path.write_text(json.dumps(acct))
    pos = pd.DataFrame([
        {"instrument": "000001.SZ", "quantity": 100, "cost_price": 10.0},
    ])
    pos_path = tmp_path / "positions.csv"
    pos.to_csv(pos_path, index=False)
    return acct_path, pos_path


def _mock_adapter(get_features_fn):
    """Build a mock QlibAdapter that delegates to *get_features_fn*."""
    return type("MockAdapter", (), {
        "init_qlib": lambda self: None,
        "get_features": get_features_fn,
    })()


def _make_multiindex_frame() -> pd.DataFrame:
    """Return a mock qlib result (MultiIndex) for 000001.SZ on 2026-05-26."""
    idx = pd.MultiIndex.from_tuples(
        [("000001.SZ", "2026-05-26")],
        names=["instrument", "datetime"],
    )
    return pd.DataFrame({"$close": [10.5]}, index=idx)


class TestTryMarkToMarketDataDate:
    def test_uses_resolved_data_date(self, tmp_path: Path) -> None:
        """When trade_date has no data but data_date does, MTM succeeds."""
        acct_path, pos_path = _make_trade_data(tmp_path)
        output_dir = tmp_path / "mtm_output"

        def _mock_features(self, instruments, fields, **kwargs):
            if kwargs.get("start_time") == "2026-05-26":
                return _make_multiindex_frame()
            return pd.DataFrame()

        with patch(
            "qsys.data.calendar.resolve_data_date",
            return_value="2026-05-26",
        ), patch(
            "qsys.ops.mtm.QlibAdapter",
            return_value=_mock_adapter(_mock_features),
        ):
            result = try_mark_to_market(
                trade_date="2026-05-27",
                output_dir=output_dir,
                account_path=acct_path,
                positions_path=pos_path,
                project_root=tmp_path,
            )

        assert result is not None
        assert result["market_value"] == 1050.0  # 100 * 10.5
        assert result["priced_count"] == 1

    def test_returns_none_when_data_date_has_no_data(self, tmp_path: Path) -> None:
        """If even the resolved data_date has no close prices, MTM returns None."""
        acct_path, pos_path = _make_trade_data(tmp_path)
        output_dir = tmp_path / "mtm_output2"

        with patch(
            "qsys.data.calendar.resolve_data_date",
            return_value="2026-05-25",
        ), patch(
            "qsys.ops.mtm.QlibAdapter",
            return_value=_mock_adapter(lambda self, i, f, **kw: pd.DataFrame()),
        ):
            result = try_mark_to_market(
                trade_date="2026-05-27",
                output_dir=output_dir,
                account_path=acct_path,
                positions_path=pos_path,
                project_root=tmp_path,
            )

        assert result is None
