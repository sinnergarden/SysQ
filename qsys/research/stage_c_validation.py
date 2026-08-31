"""Independent validation for a frozen Stage-C assessment."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


def _resolve(path: str | Path, repo_root: Path) -> Path:
    value = Path(path)
    return value.resolve() if value.is_absolute() else (repo_root / value).resolve()


def _date_key(value: Any) -> str:
    return str(value).replace("-", "")[:8]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _holding_before(executions: pd.DataFrame, instrument: str, date: str) -> int:
    frame = executions[
        (executions["instrument"].astype(str) == instrument)
        & (executions["trade_date"].astype(str).str[:10] <= date)
        & (executions["status"].astype(str) == "filled")
    ]
    quantity = pd.to_numeric(frame["filled_qty"], errors="raise").astype(int)
    direction = frame["side"].astype(str).map({"buy": 1, "sell": -1})
    if direction.isna().any():
        raise ValueError("unknown execution side")
    return int((quantity * direction.astype(int)).sum())


def validate_stage_c_assessment(
    config_path: str | Path,
    *,
    root: str | Path = "data/research",
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    config_path = Path(config_path).resolve()
    repo_root = (
        Path(repo_root).resolve()
        if repo_root is not None
        else Path(__file__).resolve().parents[2]
    )
    root = Path(root).resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assessment_id = str(config["assessment_id"])
    output_dir = root / "stage_c_assessments" / assessment_id
    manifest_path = output_dir / "manifest.json"
    artifact_path = output_dir / "stage_c_assessment.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))

    if manifest.get("schema_version") != "stage_c_assessment_v1":
        raise ValueError("invalid Stage-C manifest schema")
    if manifest.get("assessment_id") != assessment_id:
        raise ValueError("Stage-C assessment id mismatch")
    if manifest.get("config_sha256") != _sha256(config_path):
        raise ValueError("Stage-C config hash mismatch")
    producer_path = repo_root / "qsys/research/stage_c.py"
    if manifest.get("producer_code_sha256") != _sha256(producer_path):
        raise ValueError("Stage-C producer code hash mismatch")
    identity = {
        key: manifest[key]
        for key in (
            "schema_version", "assessment_id", "config_sha256",
            "producer_code_sha256", "input_hashes",
        )
    }
    if manifest.get("stage_c_identity_sha256") != _canonical_hash(identity):
        raise ValueError("Stage-C identity mismatch")
    if manifest["outputs"]["stage_c_assessment.json"]["sha256"] != _sha256(
        artifact_path
    ):
        raise ValueError("Stage-C assessment output hash mismatch")
    if (
        artifact.get("formal_status") != "accounting_data_blocked"
        or artifact.get("portfolio_extractability") != "not_established"
        or artifact.get("promotion_eligible")
        or artifact.get("holdout_consumed")
    ):
        raise ValueError("Stage-C terminal-state contract mismatch")

    stage_b_path = _resolve(config["stage_b_validation_path"], repo_root)
    stage_b = json.loads(stage_b_path.read_text(encoding="utf-8"))
    if (
        _sha256(stage_b_path) != manifest["input_hashes"]["stage_b_validation_sha256"]
        or stage_b.get("status") != "pass"
        or stage_b.get("holdout_consumed")
        or str(stage_b.get("holdout_start")) != str(config["holdout_start"])
    ):
        raise ValueError("Stage-B lineage or holdout contract mismatch")
    promoted = config["promoted_signal"]
    matches = [
        row for row in stage_b.get("signals", [])
        if row.get("signal_id") == promoted["signal_id"]
        and row.get("signal_run_id") == promoted["signal_run_id"]
        and row.get("predictions_sha256") == promoted["predictions_sha256"]
    ]
    if len(matches) != 1:
        raise ValueError("Stage-C signal is not confirmed by Stage-B")

    evidence = config["accounting_evidence"]
    instrument = str(evidence["instrument"])
    previous_date = _date_key(evidence["previous_trade_date"])
    detection_date = _date_key(evidence["detection_trade_date"])
    market_path = _resolve(evidence["canonical_data_root"], repo_root) / (
        f"{instrument}.feather"
    )
    if (
        _sha256(market_path)
        != manifest["input_hashes"]["canonical_market_data_sha256"]
    ):
        raise ValueError("canonical market data changed")
    market = pd.read_feather(market_path)
    keys = market["trade_date"].map(_date_key)
    previous = market.loc[keys == previous_date]
    current = market.loc[keys == detection_date]
    if len(previous) != 1 or len(current) != 1:
        raise ValueError("market evidence row mismatch")
    previous_factor = float(previous.iloc[0]["factor"])
    current_factor = float(current.iloc[0]["factor"])
    jump = current_factor / previous_factor - 1.0
    tolerance = float(evidence["factor_rounding_relative_tolerance"])
    if not math.isfinite(jump) or abs(jump) <= tolerance:
        raise ValueError("factor discontinuity is not material")
    recorded = artifact["accounting_evidence"]
    for key, value in (
        ("previous_factor", previous_factor),
        ("current_factor", current_factor),
        ("factor_relative_jump", jump),
        ("previous_total_share", float(previous.iloc[0]["total_share"])),
        ("current_total_share", float(current.iloc[0]["total_share"])),
    ):
        if not math.isclose(float(recorded[key]), value, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError(f"recorded accounting evidence differs: {key}")

    action_dir = root / "corporate_actions" / str(evidence["corporate_action_artifact"])
    action_manifest_path = action_dir / "manifest.json"
    events_path = action_dir / "events.parquet"
    if (
        _sha256(action_manifest_path)
        != manifest["input_hashes"]["corporate_action_manifest_sha256"]
        or _sha256(events_path)
        != manifest["input_hashes"]["corporate_action_events_sha256"]
    ):
        raise ValueError("corporate-action input changed")
    action_manifest = json.loads(action_manifest_path.read_text(encoding="utf-8"))
    events = pd.read_parquet(events_path)
    event_dates = events["effective_date"].map(_date_key)
    matching_events = events[
        (events["instrument"].astype(str) == instrument)
        & event_dates.between(previous_date, detection_date)
    ]
    if (
        action_manifest.get("schema_version") != "corporate_actions_v1"
        or not matching_events.empty
    ):
        raise ValueError("corporate-action coverage-gap claim is not reproducible")

    comparison = {int(row["top_n"]): row for row in artifact[
        "exploratory_portfolio_comparison"
    ]}
    if sorted(comparison) != sorted(int(value) for value in config["portfolio_sizes"]):
        raise ValueError("portfolio-size comparison mismatch")
    detection_iso = f"{detection_date[:4]}-{detection_date[4:6]}-{detection_date[6:]}"
    for spec in config["exploratory_runs"]:
        top_n = int(spec["top_n"])
        run_dir = _resolve(spec["path"], repo_root)
        input_files = {
            "manifest": run_dir / "manifest.json",
            "metrics": run_dir / "metrics.json",
            "daily_summary": run_dir / "daily_summary.csv",
            "executions": run_dir / "executions.csv",
        }
        for name, path in input_files.items():
            if _sha256(path) != manifest["input_hashes"][f"top{top_n}_{name}_sha256"]:
                raise ValueError(f"Top{top_n} exploratory input changed: {name}")
        run_manifest = json.loads(input_files["manifest"].read_text(encoding="utf-8"))
        metrics = json.loads(input_files["metrics"].read_text(encoding="utf-8"))
        sources = run_manifest.get("signal_sources") or []
        if (
            run_manifest.get("corporate_action_policy") != "not_modeled"
            or int(run_manifest.get("allocation_params", {}).get("top_n", -1)) != top_n
            or run_manifest.get("signal_id") != promoted["signal_id"]
            or run_manifest.get("signal_run_id") != promoted["signal_run_id"]
            or len(sources) != 1
            or sources[0].get("predictions_sha256")
            != promoted["predictions_sha256"]
            or not run_manifest.get("pit_execution_universe")
            or str(run_manifest.get("effective_end_date")) >= str(config["holdout_start"])
            or int(metrics.get("trading_day_count", 0)) <= 0
            or not math.isclose(
                float(metrics["total_return"]),
                float(run_manifest["total_return"]),
            )
            or not math.isclose(
                float(metrics["final_value"]),
                float(run_manifest["final_value"]),
            )
        ):
            raise ValueError(f"Top{top_n} is not a holdout-isolated exploratory run")
        executions = pd.read_csv(input_files["executions"])
        held = _holding_before(executions, instrument, detection_iso)
        observed = comparison[top_n]
        if held <= 0 or int(observed["held_blocker_quantity_before_detection"]) != held:
            raise ValueError(f"Top{top_n} blocker holding is not reproducible")
        for key in ("cagr", "sharpe", "max_drawdown", "turnover_annualized"):
            if not math.isclose(
                float(observed[key]), float(metrics[key]), rel_tol=1e-12, abs_tol=1e-12
            ):
                raise ValueError(f"Top{top_n} metric mismatch: {key}")

    result = {
        "schema_version": "stage_c_assessment_validation_v1",
        "assessment_id": assessment_id,
        "stage_c_identity_sha256": manifest["stage_c_identity_sha256"],
        "manifest_sha256": _sha256(manifest_path),
        "formal_status": "accounting_data_blocked",
        "portfolio_extractability": "not_established",
        "holdout_consumed": False,
        "checks": {
            "stage_b_lineage": "pass",
            "market_factor_discontinuity": "pass",
            "corporate_action_coverage_gap": "pass",
            "exploratory_input_hashes_and_metrics": "pass",
            "blocker_holding_reconstruction": "pass",
            "holdout_isolation": "pass",
        },
        "validated": True,
        "validator_code_sha256": _sha256(Path(__file__)),
    }
    _write_json(output_dir / "validation.json", result)
    return result
