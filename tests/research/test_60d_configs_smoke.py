#!/usr/bin/env python3
"""Smoke test: 60d delayed configs parse and label maturity check."""
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from qsys.research.matrix_job import RollingResearchConfig
from qsys.research.rolling_window import build_rolling_windows

configs = [
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

pass_c = 0
for name in configs:
    p = REPO / "configs" / "research" / name
    if not p.exists():
        print(f"❌ {name}: not found"); continue
    cfg = RollingResearchConfig.from_file(p)
    assert cfg.experiment_id is not None
    assert cfg.feature_list_id is not None
    assert len(cfg.labels) > 0
    lag = cfg.labels[0].get("label_maturity_lag_trading_days", 0)
    assert lag == 60, f"{name}: expected lag=60, got {lag}"
    # Verify window generation
    windows = build_rolling_windows(
        cfg.calendar["start_date"], cfg.calendar["end_date"],
        train_window_days=cfg.calendar.get("train_window_days", 504),
        step_days=cfg.calendar.get("step_days", 20),
        label_maturity_lag_trading_days=lag,
    )
    assert len(windows) > 0, f"{name}: no windows generated"
    # Check every window's train_end <= predict_start - 60 trading days
    import pandas as pd
    from qsys.data.calendar import get_trading_calendar
    cal = get_trading_calendar(cfg.calendar["start_date"], cfg.calendar["end_date"])
    for w in windows:
        pds = pd.Timestamp(w.predict_start)
        tde = pd.Timestamp(w.train_end)
        gap = len([d for d in cal if tde < pd.Timestamp(d) <= pds])
        assert gap >= 60, f"{name} {w.window_id}: gap={gap} < 60 trading days"
    pass_c += 1
    print(f"✅ {name}: {cfg.experiment_id}, {len(windows)} windows, lag={lag}")

print(f"\n{pass_c}/{len(configs)} passed")
sys.exit(0 if pass_c == len(configs) else 1)
