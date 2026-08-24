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


def test_csi1800_pit_config_only_changes_universe_lineage() -> None:
    csi800 = RollingResearchConfig.from_file(
        REPO_ROOT
        / "configs/research/60d/financial_rc_180d_rolling_5y_to_202607_v3_pit.yaml"
    )
    csi1800 = RollingResearchConfig.from_file(
        REPO_ROOT
        / "configs/research/60d/financial_rc_180d_rolling_5y_to_202607_v3_pit_csi1800.yaml"
    )

    assert csi1800.calendar == csi800.calendar
    assert csi1800.feature_list_id == csi800.feature_list_id
    assert csi1800.transforms == csi800.transforms
    assert csi1800.generators[0]["params"]["n_estimators"] == 300
    assert csi1800.generators[0]["params"]["universe"] == "csi1800_pit_union"
    assert csi1800.generators[0]["params"]["pit_membership"] is True
    assert (
        csi1800.generators[0]["params"]["pit_universe_artifact"]
        == "csi1800_pit_v2"
    )
    assert csi1800.labels == [
        {
            "label_id": "fwd_ret_180d_raw_pit_csi1800",
            "label_maturity_lag_trading_days": 181,
        }
    ]


def test_csi1800_postbootstrap_r1_only_adds_pinned_shareholder_inputs() -> None:
    baseline = RollingResearchConfig.from_file(
        REPO_ROOT
        / "configs/research/60d/financial_rc_180d_rolling_5y_to_202607_v3_pit_csi1800.yaml"
    )
    rerun = RollingResearchConfig.from_file(
        REPO_ROOT
        / "configs/research/60d/financial_rc_180d_rolling_5y_to_202607_v3_pit_csi1800_postbootstrap_r1.yaml"
    )

    assert rerun.calendar == baseline.calendar
    assert rerun.feature_list_id == baseline.feature_list_id
    assert rerun.source_manifest_hash == baseline.source_manifest_hash
    assert rerun.transforms == baseline.transforms
    assert rerun.labels == baseline.labels
    base_params = dict(baseline.generators[0]["params"])
    rerun_params = dict(rerun.generators[0]["params"])
    pinned = {
        key: rerun_params.pop(key)
        for key in (
            "shareholder_holder_path",
            "shareholder_holder_sha256",
            "shareholder_top10_path",
            "shareholder_top10_sha256",
        )
    }
    contract = rerun_params.pop("shareholder_freshness_contract")
    assert rerun_params == base_params
    assert pinned["shareholder_holder_sha256"] == "53e03fa87945a7602f64aa385d5b328d9d2b45375ccca193124c355658e704e1"
    assert pinned["shareholder_top10_sha256"] == "8709f7509e46cd3b8c681099159f9890ee881ab1816ae467e20aa4ae06fe5b4f"
    assert contract["min_coverage"] == 0.95
    assert contract["features"]["holder_num_stale_days"] == {
        "min_coverage": 0.94,
        "max_median_days": 200,
        "max_row_days": 365,
    }
    assert contract["features"]["top10_holder_stale_days"] == {
        "min_coverage": 0.99,
        "max_median_days": 250,
        "max_row_days": 365,
    }
