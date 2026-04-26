#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qsys.data.adapter import QlibAdapter
from qsys.ops import read_latest_shadow_model, resolve_daily_trade_date
from qsys.research.mainline import MAINLINE_OBJECTS, resolve_mainline_feature_config
from qsys.research.readiness import build_model_input_frame
from qsys.strategy.generator import SignalGenerator


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _read_instrument_file(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["instrument", "start_date", "end_date"])
    df = pd.read_csv(path, sep="\t", header=None, names=["instrument", "start_date", "end_date"])
    for col in ["start_date", "end_date"]:
        df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def _active_universe_rows(df: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    dt = pd.Timestamp(trade_date)
    mask = (df["start_date"] <= dt) & (df["end_date"] >= dt)
    return df.loc[mask].copy()


def _build_universe_snapshot(*, adapter: QlibAdapter, universe: str, trade_date: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    inst_dir = adapter.qlib_dir / "instruments"
    universe_path = inst_dir / f"{universe}.txt"
    all_path = inst_dir / "all.txt"
    universe_df = _read_instrument_file(universe_path)
    all_df = _read_instrument_file(all_path)
    active_df = _active_universe_rows(universe_df, trade_date)
    all_active_df = _active_universe_rows(all_df, trade_date)

    snapshot_rows = []
    for row in universe_df.itertuples(index=False):
        snapshot_rows.append(
            {
                "instrument": str(row.instrument),
                "start_date": row.start_date.strftime("%Y-%m-%d") if pd.notna(row.start_date) else None,
                "end_date": row.end_date.strftime("%Y-%m-%d") if pd.notna(row.end_date) else None,
                "is_active_on_trade_date": bool(pd.notna(row.start_date) and pd.notna(row.end_date) and row.start_date <= pd.Timestamp(trade_date) <= row.end_date),
            }
        )

    coverage_status = "ok"
    coverage_notes: list[str] = []
    if len(active_df) < len(universe_df):
        coverage_status = "universe_instrument_stale"
        coverage_notes.append(f"{len(universe_df) - len(active_df)} instruments inactive on trade_date in {universe}.txt")
    if len(all_active_df) < len(all_df):
        coverage_status = "all_instrument_stale" if coverage_status == "ok" else coverage_status
        coverage_notes.append(f"{len(all_df) - len(all_active_df)} instruments inactive on trade_date in all.txt")

    payload = {
        "universe": universe,
        "instrument_count": int(len(universe_df)),
        "active_count_on_trade_date": int(len(active_df)),
        "all_instrument_count": int(len(all_df)),
        "all_active_count_on_trade_date": int(len(all_active_df)),
        "coverage_status": coverage_status,
        "notes": coverage_notes,
    }
    return payload, snapshot_rows, active_df["instrument"].astype(str).tolist()


def _feature_frame_profile(feature_frame: pd.DataFrame, model_input_frame: pd.DataFrame, expected_features: list[str], requested_instruments: list[str]) -> tuple[dict[str, Any], list[str], dict[str, float]]:
    feature_instruments: list[str] = []
    if feature_frame is not None and not feature_frame.empty and isinstance(feature_frame.index, pd.MultiIndex):
        feature_instruments = sorted(feature_frame.index.get_level_values("instrument").astype(str).unique().tolist())
    row_non_null: dict[str, float] = {}
    if feature_frame is not None and not feature_frame.empty and isinstance(feature_frame.index, pd.MultiIndex):
        temp = feature_frame.notna().mean(axis=1)
        row_non_null = {str(idx[0]): float(val) for idx, val in temp.items()}

    rows_all_nan_count = int(sum(1 for value in row_non_null.values() if value == 0.0))
    rows_low_coverage_count = int(sum(1 for value in row_non_null.values() if value < 0.7))
    dropped = sorted(set(requested_instruments) - set(feature_instruments))
    profile = {
        "requested_instrument_count": int(len(requested_instruments)),
        "feature_frame_row_count": int(len(feature_frame)) if feature_frame is not None else 0,
        "feature_frame_instrument_count": int(len(feature_instruments)),
        "feature_column_count": int(len(feature_frame.columns)) if feature_frame is not None else 0,
        "usable_feature_column_count": int(len(model_input_frame.columns)) if model_input_frame is not None else 0,
        "rows_all_nan_count": rows_all_nan_count,
        "rows_low_coverage_count": rows_low_coverage_count,
    }
    return profile, dropped, row_non_null


def _prediction_profile(scores: pd.Series, model_path: Path, active_instruments: list[str], feature_instruments: list[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    frame = scores.rename("score").reset_index()
    if "instrument" not in frame.columns:
        if "index" in frame.columns:
            frame = frame.rename(columns={"index": "instrument"})
        elif "ts_code" in frame.columns:
            frame = frame.rename(columns={"ts_code": "instrument"})
    if "datetime" in frame.columns:
        frame = frame.drop(columns=["datetime"])
    frame["instrument"] = frame["instrument"].astype(str)
    frame["score"] = pd.to_numeric(frame["score"], errors="coerce")
    frame = frame.dropna(subset=["score"]).sort_values("instrument").reset_index(drop=True)
    predicted_instruments = frame["instrument"].tolist()
    dropped_rows: list[dict[str, Any]] = []
    for instrument in sorted(set(active_instruments) - set(feature_instruments)):
        dropped_rows.append({"instrument": instrument, "stage": "feature_frame", "reason": "universe_inactive_or_missing_feature_row"})
    for instrument in sorted(set(feature_instruments) - set(predicted_instruments)):
        dropped_rows.append({"instrument": instrument, "stage": "prediction", "reason": "prediction_missing_after_model"})

    drop_reason_counts: dict[str, int] = {}
    for row in dropped_rows:
        drop_reason_counts[row["reason"]] = drop_reason_counts.get(row["reason"], 0) + 1

    meta = {}
    meta_path = model_path / "meta.yaml"
    if meta_path.exists():
        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
    profile = {
        "model_path": str(model_path),
        "model_loaded": bool(model_path.exists()),
        "model_type": meta.get("name"),
        "input_rows_before_model": int(len(feature_instruments)),
        "rows_after_filter": int(len(feature_instruments)),
        "prediction_count": int(len(frame)),
        "prediction_instruments": predicted_instruments,
        "drop_reason_top": sorted(drop_reason_counts.items(), key=lambda item: (-item[1], item[0]))[:10],
    }
    return profile, dropped_rows


def run_audit(*, base_dir: Path, mainline_object_name: str, universe: str = "csi300") -> dict[str, Any]:
    date_resolution = resolve_daily_trade_date(None, universe=universe)
    trade_date = str(date_resolution.get("resolved_trade_date") or date_resolution["requested_date"])
    latest_model = read_latest_shadow_model(base_dir)
    if not latest_model:
        raise RuntimeError("No usable latest shadow model pointer found")

    model_path = Path(str(latest_model["model_path"]))
    feature_config = list(resolve_mainline_feature_config(mainline_object_name) or [])
    adapter = QlibAdapter()
    adapter.init_qlib()

    universe_payload, universe_rows, active_instruments = _build_universe_snapshot(adapter=adapter, universe=universe, trade_date=trade_date)
    feature_frame = adapter.get_features(universe, feature_config, start_time=trade_date, end_time=trade_date)
    model_input_frame = build_model_input_frame(feature_frame=feature_frame, model_path=model_path)
    feature_profile, feature_dropped, row_non_null = _feature_frame_profile(
        feature_frame,
        model_input_frame,
        feature_config,
        active_instruments,
    )

    generator = SignalGenerator(model_path)
    scores = generator.predict(feature_frame)
    feature_instruments = sorted(feature_frame.index.get_level_values("instrument").astype(str).unique().tolist()) if isinstance(feature_frame.index, pd.MultiIndex) else []
    prediction_profile, dropped_rows = _prediction_profile(scores, model_path, active_instruments, feature_instruments)

    predictions = scores.rename("score").reset_index()
    if "instrument" not in predictions.columns and "index" in predictions.columns:
        predictions = predictions.rename(columns={"index": "instrument"})
    if "datetime" in predictions.columns:
        predictions = predictions.drop(columns=["datetime"])
    predictions["instrument"] = predictions["instrument"].astype(str)
    predictions["score"] = pd.to_numeric(predictions["score"], errors="coerce")
    predictions = predictions.dropna(subset=["score"]).sort_values("instrument").reset_index(drop=True)

    summary = {
        "mainline_object_name": mainline_object_name,
        "trade_date": trade_date,
        "universe": universe,
        "instrument_count": universe_payload["instrument_count"],
        "active_count_on_trade_date": universe_payload["active_count_on_trade_date"],
        "feature_frame_row_count": feature_profile["feature_frame_row_count"],
        "feature_frame_instrument_count": feature_profile["feature_frame_instrument_count"],
        "feature_column_count": feature_profile["feature_column_count"],
        "usable_feature_column_count": feature_profile["usable_feature_column_count"],
        "prediction_count": prediction_profile["prediction_count"],
        "min_prediction_count": 50,
        "coverage_status": universe_payload["coverage_status"],
        "root_cause": "universe_instrument_coverage_mismatch" if universe_payload["active_count_on_trade_date"] < 50 else "unknown",
        "recommendation": "Refresh qlib/raw coverage and rebuild universe instrument files before allowing daily inference to proceed.",
        "go_no_go": "Go" if prediction_profile["prediction_count"] >= 50 else "No-Go",
    }

    return {
        "date_resolution": date_resolution,
        "latest_model_pointer": latest_model,
        "selected_model_snapshot": {
            "model_path": str(model_path),
            "model_exists": model_path.exists(),
            "meta": yaml.safe_load((model_path / "meta.yaml").read_text(encoding="utf-8")) if (model_path / "meta.yaml").exists() else {},
        },
        "universe_rows": universe_rows,
        "universe_payload": universe_payload,
        "model_feature_config": {
            "mainline_object_name": mainline_object_name,
            "feature_count": len(feature_config),
            "feature_config": feature_config,
        },
        "feature_frame_profile": {
            **feature_profile,
            "row_non_null_ratio": row_non_null,
            "dropped_before_feature_frame": feature_dropped,
        },
        "prediction_profile": prediction_profile,
        "predictions": predictions,
        "dropped_instruments": dropped_rows,
        "summary": summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit inference coverage for daily shadow inference.")
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--mainline", default="feature_173")
    parser.add_argument("--universe", default="csi300")
    parser.add_argument("--output-dir", default="experiments/ops_diagnostics/inference_coverage_audit")
    args = parser.parse_args()

    base_dir = Path(args.base_dir).resolve()
    output_dir = (base_dir / args.output_dir).resolve()
    result = run_audit(base_dir=base_dir, mainline_object_name=args.mainline, universe=args.universe)

    _write_json(output_dir / "date_resolution.json", result["date_resolution"])
    _write_json(output_dir / "latest_model_pointer.json", result["latest_model_pointer"])
    _write_json(output_dir / "selected_model_snapshot.json", result["selected_model_snapshot"])
    _write_csv(output_dir / "universe_snapshot.csv", result["universe_rows"], ["instrument", "start_date", "end_date", "is_active_on_trade_date"])
    _write_json(output_dir / "model_feature_config.json", result["model_feature_config"])
    _write_json(output_dir / "feature_frame_profile.json", result["feature_frame_profile"])
    _write_json(output_dir / "prediction_profile.json", result["prediction_profile"])
    _write_csv(output_dir / "dropped_instruments.csv", result["dropped_instruments"], ["instrument", "stage", "reason"])
    _write_json(output_dir / "inference_coverage_summary.json", result["summary"])
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
