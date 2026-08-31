from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from qsys.research.risk_overlay import (
    build_market_risk_overlay,
    compute_market_risk_schedule,
)


def _benchmark(periods: int = 500) -> pd.DataFrame:
    dates = pd.bdate_range("2022-01-03", periods=periods)
    returns = 0.0004 + 0.006 * np.sin(np.arange(periods) / 11.0)
    close = 100.0 * np.cumprod(1.0 + returns)
    return pd.DataFrame({"trade_date": dates, "close": close})


def test_market_risk_schedule_uses_only_previous_completed_close() -> None:
    benchmark = _benchmark()
    target = benchmark["trade_date"].iloc[350]
    kwargs = {
        "start_date": benchmark["trade_date"].iloc[300],
        "end_date": benchmark["trade_date"].iloc[400],
        "trend_window_sessions": 120,
        "volatility_window_sessions": 20,
        "volatility_history_min_periods": 252,
        "volatility_quantile": 0.8,
    }
    baseline = compute_market_risk_schedule(benchmark, **kwargs)
    mutated = benchmark.copy()
    mutated.loc[mutated["trade_date"] == target, "close"] *= 0.5
    changed = compute_market_risk_schedule(mutated, **kwargs)

    baseline_row = baseline.set_index("trade_date").loc[target]
    changed_row = changed.set_index("trade_date").loc[target]
    assert baseline_row["gate_active"] == changed_row["gate_active"]
    assert baseline_row["previous_close"] == changed_row["previous_close"]
    assert baseline_row["asof_date"] < target
    next_date = benchmark.loc[
        benchmark["trade_date"] > target, "trade_date"
    ].iloc[0]
    assert (
        baseline.set_index("trade_date").loc[next_date, "previous_close"]
        != changed.set_index("trade_date").loc[next_date, "previous_close"]
    )


def test_build_market_risk_overlay_binds_inputs_and_preserves_holdout(
    tmp_path: Path,
) -> None:
    benchmark = _benchmark()
    benchmark_path = tmp_path / "000906.SH.csv"
    benchmark.assign(
        trade_date=benchmark["trade_date"].dt.strftime("%Y%m%d")
    ).to_csv(benchmark_path, index=False)
    start = benchmark["trade_date"].iloc[300]
    end = benchmark["trade_date"].iloc[400]
    config = {
        "schema_version": "market_risk_overlay_config_v1",
        "overlay_id": "csi800_fixed_gate",
        "benchmark_id": "csi800",
        "benchmark_csv": str(benchmark_path),
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
        "holdout_start": "2025-01-02",
        "information_lag_sessions": 1,
        "trend_window_sessions": 120,
        "volatility_window_sessions": 20,
        "volatility_history_min_periods": 252,
        "volatility_quantile": 0.8,
        "combine_rule": "any",
        "exposure_scale": 0.5,
    }
    config_path = tmp_path / "overlay.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    result = build_market_risk_overlay(
        config_path, research_root=tmp_path / "research"
    )
    artifact_dir = Path(result["artifact_dir"])
    manifest_path = artifact_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    schedule = json.loads((artifact_dir / "schedule.json").read_text())

    assert len(schedule) == 101
    assert max(schedule) == end.strftime("%Y-%m-%d")
    assert manifest["holdout_consumed"] is False
    assert manifest["information_lag_sessions"] == 1
    assert manifest["schedule_sha256"] == hashlib.sha256(
        json.dumps(schedule, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert result["identity"]["manifest_sha256"] == hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()
    reused = build_market_risk_overlay(
        config_path, research_root=tmp_path / "research"
    )
    assert reused["identity"] == result["identity"]


def test_risk_overlay_rejects_holdout_overlap(tmp_path: Path) -> None:
    benchmark = _benchmark()
    benchmark_path = tmp_path / "benchmark.csv"
    benchmark.to_csv(benchmark_path, index=False)
    config = {
        "schema_version": "market_risk_overlay_config_v1",
        "overlay_id": "bad",
        "benchmark_id": "csi800",
        "benchmark_csv": str(benchmark_path),
        "start_date": "2023-01-01",
        "end_date": "2025-01-02",
        "holdout_start": "2025-01-02",
        "information_lag_sessions": 1,
        "trend_window_sessions": 120,
        "volatility_window_sessions": 20,
        "volatility_history_min_periods": 252,
        "volatility_quantile": 0.8,
        "combine_rule": "any",
        "exposure_scale": 0.5,
    }
    config_path = tmp_path / "bad.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match="end_date < holdout_start"):
        build_market_risk_overlay(config_path, research_root=tmp_path / "research")
