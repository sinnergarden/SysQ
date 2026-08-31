from __future__ import annotations

import json

import numpy as np
import pandas as pd

from qsys.research.stage_a import StageAEvaluator


def _fixture() -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    dates = [
        "2019-01-02", "2019-12-31", "2020-01-02", "2020-12-31",
        "2021-01-04", "2021-12-31", "2022-01-04", "2022-12-30",
        "2023-01-03", "2023-12-29", "2024-01-02", "2024-12-31",
    ]
    rows = []
    labels = []
    for date_index, date in enumerate(dates):
        for instrument_index in range(12):
            value = instrument_index / 11 + date_index * 1e-4
            rows.append({
                "trade_date": date,
                "instrument": f"S{instrument_index:02d}",
                "positive": value,
                "negative": -value,
                "industry": instrument_index % 3,
                "circ_mv": 100.0 + instrument_index,
            })
            labels.append({
                "trade_date": date,
                "instrument": f"S{instrument_index:02d}",
                "label_value": value,
                "horizon": 2,
            })
    frame = pd.DataFrame(rows)
    label = pd.DataFrame(labels)
    return frame, {"primary": label, "secondary": label.copy()}


def test_stage_a_locks_direction_and_does_not_consume_holdout(tmp_path, monkeypatch):
    frame, labels = _fixture()
    monkeypatch.setattr(
        "qsys.research.stage_a.compute_regime_ic",
        lambda frame: pd.DataFrame(),
    )
    evaluator = StageAEvaluator(
        feature_frame=frame,
        features=["positive", "negative"],
        label_data=labels,
        label_configs=[
            {"label_id": "primary", "role": "primary"},
            {"label_id": "secondary", "role": "secondary"},
        ],
        config={
            "splits": {
                "discovery": {"start": "2019-01-02", "end": "2022-12-30"},
                "confirmation": {"start": "2023-01-03", "end": "2024-12-31"},
                "holdout": {"start": "2025-01-02", "end": "2026-07-31"},
            },
            "feature_families": {"path": ["positive", "negative"]},
            "min_count": 5,
            "top_ks": [5, 20, 50],
            "random_reps": 2,
            "criteria": {
                "minimum_evidence_classes": 1,
                "minimum_hac_t": 0,
                "minimum_nonoverlap_ratio": 0.5,
                "minimum_year_direction_ratio": 1.0,
                "minimum_discovery_years": 4,
                "minimum_confirmation_years": 2,
            },
        },
        output_dir=tmp_path,
    )
    protocol = evaluator.run()

    assert protocol["holdout_consumed"] is False
    assert protocol["loaded_data_end"] == "2024-12-31"
    triage = pd.read_csv(tmp_path / "stage_a_triage.csv")
    assert dict(zip(triage["feature"], triage["locked_direction"])) == {
        "positive": 1,
        "negative": -1,
    }
    evidence = pd.read_csv(tmp_path / "stage_a_evidence.csv")
    confirmation = evidence[evidence["phase"].eq("confirmation")]
    assert set(confirmation["locked_direction"]) == {-1, 1}
    assert (confirmation["rank_ic_mean"] > 0).all()
    assert json.loads((tmp_path / "stage_a_protocol.json").read_text())[
        "feature_trial_count"
    ] == 2


def test_stage_a_caps_promotion_at_provisional(tmp_path, monkeypatch):
    frame, labels = _fixture()
    monkeypatch.setattr(
        "qsys.research.stage_a.compute_regime_ic",
        lambda frame: pd.DataFrame(),
    )
    evaluator = StageAEvaluator(
        feature_frame=frame,
        features=["positive"],
        label_data=labels,
        label_configs=[
            {"label_id": "primary", "role": "primary"},
            {"label_id": "secondary", "role": "secondary"},
        ],
        config={
            "promotion_eligible": False,
            "splits": {
                "discovery": {"start": "2019-01-02", "end": "2022-12-30"},
                "confirmation": {"start": "2023-01-03", "end": "2024-12-31"},
                "holdout": {"start": "2025-01-02", "end": "2026-07-31"},
            },
            "feature_families": {"path": ["positive"]},
            "min_count": 5,
            "top_ks": [5],
            "criteria": {
                "minimum_evidence_classes": 1,
                "minimum_hac_t": 0,
                "minimum_nonoverlap_ratio": 0.5,
                "minimum_year_direction_ratio": 1.0,
                "minimum_discovery_years": 4,
                "minimum_confirmation_years": 2,
            },
        },
        output_dir=tmp_path,
    )

    protocol = evaluator.run()

    triage = pd.read_csv(tmp_path / "stage_a_triage.csv")
    assert triage.loc[0, "research_status"] == "provisional"
    assert protocol["promotion_eligible"] is False
    assert protocol["confirmed_count"] == 0
    assert protocol["provisional_count"] == 1
    assert protocol["statistical_confirmation_count"] == 1
