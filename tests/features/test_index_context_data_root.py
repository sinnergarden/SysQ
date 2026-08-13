from pathlib import Path

import pandas as pd

from qsys.config import cfg
from qsys.feature.groups.index_context import load_index_daily


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
