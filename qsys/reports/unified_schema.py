from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


def training_contract_payload(
    *,
    training_mode: str,
    train_end_requested: str | None,
    train_end_effective: str | None,
    infer_date: str | None,
    last_train_sample_date: str | None,
    max_label_date_used: str | None,
    is_label_mature_at_infer_time: bool | None,
    mlflow_root: str | None = None,
) -> dict[str, Any]:
    return {
        "training_mode": training_mode,
        "train_end_requested": train_end_requested,
        "train_end_effective": train_end_effective,
        "infer_date": infer_date,
        "last_train_sample_date": last_train_sample_date,
        "max_label_date_used": max_label_date_used,
        "is_label_mature_at_infer_time": is_label_mature_at_infer_time,
        "mlflow_root": mlflow_root,
    }


def unified_run_artifacts(report_dir: str | Path) -> dict[str, Path]:
    root = Path(report_dir)
    return {
        "config_snapshot": root / "config_snapshot.json",
        "training_summary": root / "training_summary.json",
        "signal_metrics": root / "signal_metrics.json",
        "group_returns": root / "group_returns.csv",
        "execution_audit": root / "execution_audit.csv",
        "suspicious_trades": root / "suspicious_trades.csv",
        "metrics": root / "metrics.json",
        "exposure_summary": root / "exposure_summary.json",
        "exposure_timeseries": root / "exposure_timeseries.csv",
        "selection_daily": root / "selection_daily.csv",
        "decisions": root / "decisions.json",
    }


def write_json(path: str | Path, data: Any) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return str(path)


def write_csv(path: str | Path, data: Any, columns: list[str] | None = None) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, pd.DataFrame):
        frame = data.copy()
    elif isinstance(data, list):
        frame = pd.DataFrame(data)
    else:
        path.write_text(str(data), encoding="utf-8")
        return str(path)
    if columns is not None:
        for column in columns:
            if column not in frame.columns:
                frame[column] = pd.NA
        frame = frame[columns]
    frame.to_csv(path, index=False)
    return str(path)
