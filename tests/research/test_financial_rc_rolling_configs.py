from pathlib import Path

import pytest

from qsys.research.matrix_job import RollingResearchConfig


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("config_name", "horizon", "maturity_lag"),
    [
        ("financial_rc_60d_rolling_5y_v2.yaml", 60, 61),
        ("financial_rc_180d_rolling_5y_v2.yaml", 180, 181),
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

    assert config.calendar == {
        "start_date": "2021-01-01",
        "end_date": "2025-12-31",
        "train_window_days": 504,
        "step_days": 20,
    }
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
