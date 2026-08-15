from pathlib import Path

import pytest

from qsys.research.matrix_job import RollingResearchConfig
from qsys.research.rolling_window import build_rolling_windows


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("config_name", "horizon", "maturity_lag"),
    [
        ("financial_rc_60d_rolling_5y_v2.yaml", 60, 61),
        ("financial_rc_180d_rolling_5y_v2.yaml", 180, 181),
        ("financial_rc_60d_rolling_5y_to_202607_v3.yaml", 60, 61),
        ("financial_rc_180d_rolling_5y_to_202607_v3.yaml", 180, 181),
    ],
)
def test_financial_rc_rolling_5y_configs_are_independent_and_mature(
    config_name: str,
    horizon: int,
    maturity_lag: int,
) -> None:
    config = RollingResearchConfig.from_file(
        REPO_ROOT / "configs" / "research" / "60d" / config_name
    )

    expected_calendar = {
        "start_date": "2021-01-01",
        "end_date": "2025-12-31",
        "train_window_days": 504,
        "step_days": 20,
    }
    if "to_202607" in config_name:
        expected_calendar.update({
            "start_date": "2021-01-01",
            "end_date": "2026-07-31",
        })
    assert config.calendar == expected_calendar
    assert config.feature_list_id == "v3a_plus_liquidity_financial_rc"
    assert len(config.generators) == 1
    assert config.generators[0]["params"]["labels"] == [
        {"label_id": f"fwd_ret_{horizon}d_raw"}
    ]
    assert config.labels == [
        {
            "label_id": f"fwd_ret_{horizon}d_raw",
            "label_maturity_lag_trading_days": maturity_lag,
        }
    ]

    if "to_202607" in config_name:
        windows = build_rolling_windows(
            config.calendar["start_date"],
            config.calendar["end_date"],
            train_window_days=config.calendar["train_window_days"],
            step_days=config.calendar["step_days"],
            label_maturity_lag_trading_days=maturity_lag,
        )
        assert windows[0].predict_start == "2021-01-04"
        assert windows[-1].predict_end == "2026-07-31"
