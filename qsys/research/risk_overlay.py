"""Point-in-time market-risk exposure schedules for research backtests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from qsys.research.manifest import with_standard_metadata, write_manifest


CONFIG_SCHEMA_VERSION = "market_risk_overlay_config_v1"
ARTIFACT_SCHEMA_VERSION = "market_risk_overlay_v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    ).hexdigest()


def _load_config(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"risk-overlay config is not a regular file: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("risk-overlay config must decode to a mapping")
    if payload.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ValueError(
            f"risk-overlay config schema_version must be {CONFIG_SCHEMA_VERSION!r}"
        )
    return payload


def _validated_params(config: dict[str, Any]) -> dict[str, Any]:
    required = {
        "overlay_id", "benchmark_id", "benchmark_csv", "start_date",
        "end_date", "holdout_start", "information_lag_sessions",
        "trend_window_sessions", "volatility_window_sessions",
        "volatility_history_min_periods", "volatility_quantile",
        "combine_rule", "exposure_scale",
    }
    missing = sorted(required - set(config))
    if missing:
        raise ValueError(f"risk-overlay config missing fields: {missing}")
    overlay_id = str(config["overlay_id"])
    if not overlay_id or Path(overlay_id).name != overlay_id:
        raise ValueError("overlay_id must be one safe path component")
    start = pd.Timestamp(str(config["start_date"]))
    end = pd.Timestamp(str(config["end_date"]))
    holdout = pd.Timestamp(str(config["holdout_start"]))
    if not start <= end < holdout:
        raise ValueError("risk overlay requires start_date <= end_date < holdout_start")
    if int(config["information_lag_sessions"]) != 1:
        raise ValueError("information_lag_sessions must equal 1")
    trend_window = int(config["trend_window_sessions"])
    volatility_window = int(config["volatility_window_sessions"])
    volatility_history = int(config["volatility_history_min_periods"])
    volatility_quantile = float(config["volatility_quantile"])
    exposure_scale = float(config["exposure_scale"])
    if trend_window < 2 or volatility_window < 2 or volatility_history < 2:
        raise ValueError("risk-overlay windows and history must be at least 2")
    if not 0.0 < volatility_quantile < 1.0:
        raise ValueError("volatility_quantile must be within (0, 1)")
    if not 0.0 < exposure_scale < 1.0:
        raise ValueError("exposure_scale must be within (0, 1)")
    if config["combine_rule"] != "any":
        raise ValueError("combine_rule must be 'any'")
    return {
        **config,
        "overlay_id": overlay_id,
        "start_date": start,
        "end_date": end,
        "holdout_start": holdout,
        "trend_window_sessions": trend_window,
        "volatility_window_sessions": volatility_window,
        "volatility_history_min_periods": volatility_history,
        "volatility_quantile": volatility_quantile,
        "exposure_scale": exposure_scale,
    }


def _read_benchmark(path: Path) -> pd.DataFrame:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"benchmark input is not a regular file: {path}")
    frame = pd.read_csv(path, usecols=["trade_date", "close"])
    frame["trade_date"] = pd.to_datetime(
        frame["trade_date"].astype(str), errors="coerce"
    )
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    if frame[["trade_date", "close"]].isna().any().any():
        raise ValueError("benchmark contains invalid trade_date or close values")
    if (frame["close"] <= 0).any():
        raise ValueError("benchmark close values must be positive")
    frame = frame.sort_values("trade_date").reset_index(drop=True)
    if frame["trade_date"].duplicated().any():
        raise ValueError("benchmark contains duplicate trade dates")
    return frame


def compute_market_risk_schedule(
    benchmark: pd.DataFrame,
    *,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    trend_window_sessions: int,
    volatility_window_sessions: int,
    volatility_history_min_periods: int,
    volatility_quantile: float,
) -> pd.DataFrame:
    """Compute a schedule whose date T inputs end no later than T-1 close."""
    history = benchmark.loc[benchmark["trade_date"] <= end_date].copy()
    if history.empty or history["trade_date"].max() < end_date:
        raise ValueError("benchmark does not cover risk-overlay end_date")
    close = history.set_index("trade_date")["close"].astype(float)
    previous_close = close.shift(1)
    previous_trend_mean = close.rolling(
        trend_window_sessions, min_periods=trend_window_sessions
    ).mean().shift(1)
    close_return = close.pct_change(fill_method=None)
    previous_realized_volatility = close_return.rolling(
        volatility_window_sessions,
        min_periods=volatility_window_sessions,
    ).std(ddof=1).shift(1)
    previous_volatility_threshold = close_return.rolling(
        volatility_window_sessions,
        min_periods=volatility_window_sessions,
    ).std(ddof=1).expanding(
        min_periods=volatility_history_min_periods
    ).quantile(volatility_quantile).shift(1)

    schedule = pd.DataFrame({
        "trade_date": close.index,
        "asof_date": pd.Series(close.index, index=close.index).shift(1).values,
        "previous_close": previous_close.values,
        "previous_trend_mean": previous_trend_mean.values,
        "previous_realized_volatility": previous_realized_volatility.values,
        "previous_volatility_threshold": previous_volatility_threshold.values,
    })
    schedule = schedule[
        schedule["trade_date"].between(start_date, end_date, inclusive="both")
    ].copy()
    if schedule.empty:
        raise ValueError("risk-overlay date range has no benchmark sessions")
    schedule["trend_gate"] = (
        schedule["previous_trend_mean"].notna()
        & (schedule["previous_close"] < schedule["previous_trend_mean"])
    )
    schedule["volatility_gate"] = (
        schedule["previous_volatility_threshold"].notna()
        & (
            schedule["previous_realized_volatility"]
            > schedule["previous_volatility_threshold"]
        )
    )
    schedule["gate_active"] = (
        schedule["trend_gate"] | schedule["volatility_gate"]
    )
    if not (
        schedule["asof_date"].notna()
        & (schedule["asof_date"] < schedule["trade_date"])
    ).all():
        raise ValueError("risk-overlay PIT contract failed: asof_date >= trade_date")
    return schedule.reset_index(drop=True)


def build_market_risk_overlay(
    config_path: str | Path,
    *,
    research_root: str | Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Materialize a hash-bound JSON schedule and diagnostic CSV."""
    path = Path(config_path).resolve()
    config = _load_config(path)
    params = _validated_params(config)
    benchmark_path = Path(str(params["benchmark_csv"])).resolve()
    benchmark = _read_benchmark(benchmark_path)
    if params["end_date"] >= params["holdout_start"]:
        raise ValueError("risk-overlay input selection would consume holdout")
    schedule = compute_market_risk_schedule(
        benchmark,
        start_date=params["start_date"],
        end_date=params["end_date"],
        trend_window_sessions=params["trend_window_sessions"],
        volatility_window_sessions=params["volatility_window_sessions"],
        volatility_history_min_periods=params["volatility_history_min_periods"],
        volatility_quantile=params["volatility_quantile"],
    )
    schedule_map = {
        pd.Timestamp(row.trade_date).strftime("%Y-%m-%d"): bool(row.gate_active)
        for row in schedule.itertuples(index=False)
    }
    schedule_sha256 = _canonical_hash(schedule_map)
    selected_input = benchmark.loc[
        benchmark["trade_date"] <= params["end_date"],
        ["trade_date", "close"],
    ].copy()
    selected_input["trade_date"] = selected_input["trade_date"].dt.strftime(
        "%Y-%m-%d"
    )
    semantic_identity = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "overlay_id": params["overlay_id"],
        "config_sha256": _sha256(path),
        "benchmark_id": str(params["benchmark_id"]),
        "benchmark_path": str(benchmark_path),
        "benchmark_file_sha256": _sha256(benchmark_path),
        "benchmark_selected_rows_sha256": _canonical_hash(
            selected_input.to_dict(orient="records")
        ),
        "producer_code_sha256": _sha256(Path(__file__)),
        "start_date": params["start_date"].strftime("%Y-%m-%d"),
        "end_date": params["end_date"].strftime("%Y-%m-%d"),
        "holdout_start": params["holdout_start"].strftime("%Y-%m-%d"),
        "holdout_consumed": False,
        "information_lag_sessions": 1,
        "trend_window_sessions": params["trend_window_sessions"],
        "volatility_window_sessions": params["volatility_window_sessions"],
        "volatility_history_min_periods": params[
            "volatility_history_min_periods"
        ],
        "volatility_quantile": params["volatility_quantile"],
        "combine_rule": "any",
        "exposure_scale": params["exposure_scale"],
        "schedule_sha256": schedule_sha256,
    }
    identity_sha256 = _canonical_hash(semantic_identity)
    artifact_dir = (
        Path(research_root).resolve()
        / "risk_overlays"
        / params["overlay_id"]
        / identity_sha256[:16]
    )
    schedule_path = artifact_dir / "schedule.json"
    diagnostic_path = artifact_dir / "schedule.csv"
    manifest_path = artifact_dir / "manifest.json"
    if artifact_dir.exists() and not overwrite:
        if not all(item.is_file() for item in (
            schedule_path, diagnostic_path, manifest_path,
        )):
            raise FileExistsError(f"incomplete risk-overlay artifact: {artifact_dir}")
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing.get("overlay_identity_sha256") != identity_sha256:
            raise FileExistsError(f"risk-overlay artifact identity mismatch: {artifact_dir}")
        loaded_schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
        if _canonical_hash(loaded_schedule) != schedule_sha256:
            raise ValueError("risk-overlay schedule hash mismatch")
        return {
            "schedule": loaded_schedule,
            "identity": {
                **semantic_identity,
                "overlay_identity_sha256": identity_sha256,
                "manifest_path": str(manifest_path),
                "manifest_sha256": _sha256(manifest_path),
            },
            "artifact_dir": str(artifact_dir),
        }

    artifact_dir.mkdir(parents=True, exist_ok=True)
    write_manifest(schedule_path, schedule_map)
    diagnostic = schedule.copy()
    diagnostic["trade_date"] = diagnostic["trade_date"].dt.strftime("%Y-%m-%d")
    diagnostic["asof_date"] = diagnostic["asof_date"].dt.strftime("%Y-%m-%d")
    diagnostic.to_csv(diagnostic_path, index=False)
    manifest = with_standard_metadata({
        **semantic_identity,
        "artifact_type": "market_risk_overlay",
        "overlay_identity_sha256": identity_sha256,
        "schedule_rows": len(schedule_map),
        "gated_days": sum(schedule_map.values()),
        "artifacts": {
            "schedule": {
                "path": schedule_path.name,
                "sha256": _sha256(schedule_path),
                "semantic_sha256": schedule_sha256,
            },
            "diagnostics": {
                "path": diagnostic_path.name,
                "sha256": _sha256(diagnostic_path),
            },
        },
    })
    write_manifest(manifest_path, manifest)
    return {
        "schedule": schedule_map,
        "identity": {
            **semantic_identity,
            "overlay_identity_sha256": identity_sha256,
            "manifest_path": str(manifest_path),
            "manifest_sha256": _sha256(manifest_path),
        },
        "artifact_dir": str(artifact_dir),
    }
