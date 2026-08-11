"""Smoke test: 60d delayed configs parse and label maturity check.

F07: rewritten as proper pytest functions — the previous module-level loop
with a bare ``sys.exit(...)`` aborted pytest collection (INTERNALERROR,
'no tests ran'), silently breaking the full-suite PR gate.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

CONFIGS = [
    "60d/abl_v2_baseline_delayed60.yaml",
    "60d/abl_v3a_full_delayed60.yaml",
    "60d/abl_v3a_margin_delayed60.yaml",
    "60d/abl_v3a_shareholder_delayed60.yaml",
    "60d/abl_price_volume_existing_delayed60.yaml",
    "60d/abl_v3b_pv_delayed60.yaml",
    "60d/abl_60d_pure_full_price_volume_delayed60.yaml",
    "60d/abl_60d_pure_structured_price_volume_delayed60.yaml",
    "60d/abl_60d_v3a_full_plus_structured_pv_delayed60.yaml",
]


@pytest.mark.parametrize("name", CONFIGS)
def test_delayed60_config_parses_and_respects_maturity(name: str) -> None:
    """F07: each delayed-60 config parses, declares lag=60 and builds windows
    with a >= 60-trading-day gap between train_end and predict_start."""
    import pandas as pd

    from qsys.data.calendar import get_trading_calendar
    from qsys.research.matrix_job import RollingResearchConfig
    from qsys.research.rolling_window import build_rolling_windows

    p = REPO / "configs" / "research" / name
    assert p.exists(), f"{name}: not found"
    cfg = RollingResearchConfig.from_file(p)
    assert cfg.experiment_id is not None
    assert cfg.feature_list_id is not None
    assert len(cfg.labels) > 0
    lag = cfg.labels[0].get("label_maturity_lag_trading_days", 0)
    assert lag == 60, f"{name}: expected lag=60, got {lag}"

    windows = build_rolling_windows(
        cfg.calendar["start_date"], cfg.calendar["end_date"],
        train_window_days=cfg.calendar.get("train_window_days", 504),
        step_days=cfg.calendar.get("step_days", 20),
        label_maturity_lag_trading_days=lag,
    )
    assert len(windows) > 0, f"{name}: no windows generated"

    cal = get_trading_calendar(cfg.calendar["start_date"], cfg.calendar["end_date"])
    for w in windows:
        pds = pd.Timestamp(w.predict_start)
        tde = pd.Timestamp(w.train_end)
        gap = len([d for d in cal if tde < pd.Timestamp(d) <= pds])
        assert gap >= 60, f"{name} {w.window_id}: gap={gap} < 60 trading days"
