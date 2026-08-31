"""Smoke tests for the 60-session label-maturity configs."""

from pathlib import Path

import pandas as pd
import pytest

from qsys.data.calendar import get_trading_calendar
from qsys.research.matrix_job import RollingResearchConfig
from qsys.research.rolling_window import build_rolling_windows


REPO = Path(__file__).resolve().parents[2]
CONFIGS = (
    "60d/abl_v2_baseline_delayed60.yaml",
    "60d/abl_v3a_full_delayed60.yaml",
    "60d/abl_v3a_margin_delayed60.yaml",
    "60d/abl_v3a_shareholder_delayed60.yaml",
    "60d/abl_price_volume_existing_delayed60.yaml",
    "60d/abl_v3b_pv_delayed60.yaml",
    "60d/abl_60d_pure_full_price_volume_delayed60.yaml",
    "60d/abl_60d_pure_structured_price_volume_delayed60.yaml",
    "60d/abl_60d_v3a_full_plus_structured_pv_delayed60.yaml",
)


@pytest.mark.parametrize("name", CONFIGS)
def test_60d_config_uses_complete_label_maturity_gap(name: str) -> None:
    path = REPO / "configs" / "research" / name
    assert path.is_file(), f"{name}: not found"
    config = RollingResearchConfig.from_file(path)
    assert config.experiment_id
    assert config.feature_list_id
    assert config.labels
    lag = config.labels[0].get("label_maturity_lag_trading_days", 0)
    assert lag == 61, f"{name}: expected lag=61, got {lag}"

    windows = build_rolling_windows(
        config.calendar["start_date"],
        config.calendar["end_date"],
        train_window_days=config.calendar.get("train_window_days", 504),
        step_days=config.calendar.get("step_days", 20),
        label_maturity_lag_trading_days=lag,
    )
    assert windows, f"{name}: no windows generated"
    calendar = get_trading_calendar(
        min(window.train_end for window in windows),
        config.calendar["end_date"],
    )
    for window in windows:
        predict_start = pd.Timestamp(window.predict_start)
        train_end = pd.Timestamp(window.train_end)
        gap = sum(
            train_end < pd.Timestamp(date) <= predict_start
            for date in calendar
        )
        assert gap > lag, f"{name} {window.window_id}: gap={gap} <= {lag}"
