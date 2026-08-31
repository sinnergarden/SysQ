from __future__ import annotations

import pandas as pd
import pytest

from qsys.research.lag_sensitivity import AvailabilityLagSensitivity


def test_lag_sensitivity_uses_global_sessions_and_adjusts_staleness(tmp_path):
    dates = [
        "2020-01-02", "2020-01-03", "2020-01-06",
        "2020-01-07", "2020-01-08",
    ]
    frame_rows = []
    label_rows = []
    for index, date in enumerate(dates):
        for instrument, signal, label in (("AAA", 1.0, 1.0), ("BBB", -1.0, -1.0)):
            frame_rows.append({
                "trade_date": date,
                "instrument": instrument,
                "signal": signal,
                "holder_num_stale_days": float(index + 1),
            })
            label_rows.append({
                "trade_date": date,
                "instrument": instrument,
                "label_value": label,
            })

    protocol = AvailabilityLagSensitivity(
        feature_frame=pd.DataFrame(frame_rows),
        features=["signal", "holder_num_stale_days"],
        label_frame=pd.DataFrame(label_rows),
        label_id="primary",
        locked_directions={"signal": 1, "holder_num_stale_days": 1},
        calendar_dates=dates,
        config={"base_lag_sessions": 1, "lags_sessions": [1, 3], "min_count": 2},
        output_dir=tmp_path,
    ).run()

    assert protocol["holdout_consumed"] is False
    assert protocol["shift_offsets_sessions"] == {"1": 0, "3": 2}
    summary = pd.read_csv(tmp_path / "availability_lag_summary.csv")
    signal = summary[summary["feature"].eq("signal")].set_index("lag_sessions")
    assert signal.loc[1, "coverage"] == 1.0
    assert signal.loc[3, "coverage"] == 0.6
    assert signal.loc[1, "rank_ic_mean"] == pytest.approx(1.0)
    assert signal.loc[3, "rank_ic_mean"] == pytest.approx(1.0)

    stale = pd.read_csv(tmp_path / "availability_lag_stale_days.csv")
    overall = stale[stale["year"].eq("all")].set_index("lag_sessions")
    assert overall.loc[1, "median_days"] == 3.0
    # Two delayed sessions cross the weekend: the elapsed adjustment is four
    # or three calendar days, not a fixed +2.
    assert overall.loc[3, "median_days"] == 5.0

    yearly = pd.read_csv(tmp_path / "availability_lag_yearly_rank_ic.csv")
    assert len(yearly) == 4
    assert set(yearly["locked_direction"]) == {1}
