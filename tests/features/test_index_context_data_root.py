from pathlib import Path

import pandas as pd

from qsys.config import cfg
from qsys.feature.groups.growth_confirmation_v0 import _load_forecast
from qsys.feature.groups.index_context import load_index_daily, load_multi_index


def test_load_index_daily_uses_configured_data_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    configured_root = tmp_path / "shared-data"
    index_dir = configured_root / "raw" / "index"
    index_dir.mkdir(parents=True)
    pd.DataFrame(
        {
            "trade_date": ["20250102"],
            "close": [4000.0],
        }
    ).to_csv(index_dir / "000300.SH.csv", index=False)
    monkeypatch.setitem(cfg.dirs, "root", configured_root)

    result = load_index_daily("hs300")

    assert result["trade_date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2025-01-02"
    ]
    assert result["close"].tolist() == [4000.0]


def test_load_multi_index_uses_configured_data_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    configured_root = tmp_path / "shared-data"
    index_dir = configured_root / "raw" / "index"
    index_dir.mkdir(parents=True)
    pd.DataFrame(
        {"trade_date": ["20250102"], "close": [4000.0]}
    ).to_csv(index_dir / "000300.SH.csv", index=False)
    monkeypatch.setitem(cfg.dirs, "root", configured_root)

    result = load_multi_index(codes=["000300.SH"])

    assert result["close_000300.SH"].tolist() == [4000.0]


def test_growth_confirmation_uses_configured_data_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    configured_root = tmp_path / "shared-data"
    tushare_dir = configured_root / "tushare"
    tushare_dir.mkdir(parents=True)
    pd.DataFrame(
        {
            "ts_code": ["600000.SH"],
            "ann_date": ["2025-01-02"],
            "end_date": ["2024-12-31"],
            "type": ["预增"],
        }
    ).to_parquet(tushare_dir / "forecast.parquet", index=False)
    monkeypatch.setitem(cfg.dirs, "root", configured_root)

    result = _load_forecast()

    assert result["ts_code"].tolist() == ["600000.SH"]
    assert result["forecast_ann_date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2025-01-02"
    ]
