"""Freeze the Stage-C portfolio-extractability assessment and its evidence."""

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


SCHEMA_VERSION = "stage_c_assessment_v1"


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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _resolve(path: str | Path, repo_root: Path) -> Path:
    value = Path(path)
    return value.resolve() if value.is_absolute() else (repo_root / value).resolve()


def _date_key(value: Any) -> str:
    return str(value).replace("-", "")[:8]


def _finite_float(value: Any, label: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _held_quantity(executions: pd.DataFrame, instrument: str, date: str) -> int:
    selected = executions[
        (executions["instrument"].astype(str) == instrument)
        & (executions["trade_date"].astype(str).str[:10] <= date)
        & (executions["status"].astype(str) == "filled")
    ].copy()
    quantities = pd.to_numeric(selected["filled_qty"], errors="raise").astype(int)
    signs = selected["side"].astype(str).map({"buy": 1, "sell": -1})
    if signs.isna().any():
        raise ValueError("exploratory executions contain an unknown side")
    return int((quantities * signs.astype(int)).sum())


class StageCAssessment:
    """Build a hash-bound receipt for a strict Stage-C data-blocked outcome."""

    def __init__(
        self,
        config_path: str | Path,
        *,
        root: str | Path = "data/research",
        repo_root: str | Path | None = None,
    ):
        self.config_path = Path(config_path).resolve()
        self.repo_root = (
            Path(repo_root).resolve()
            if repo_root is not None
            else Path(__file__).resolve().parents[2]
        )
        self.root = Path(root).resolve()
        self.config = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        if self.config.get("schema_version") != "stage_c_assessment_request_v1":
            raise ValueError("unsupported Stage-C assessment request schema")
        self.assessment_id = str(self.config.get("assessment_id") or "").strip()
        if not self.assessment_id:
            raise ValueError("assessment_id is required")
        self.output_dir = self.root / "stage_c_assessments" / self.assessment_id

    @classmethod
    def from_config(cls, path: str | Path, **kwargs: Any) -> "StageCAssessment":
        return cls(path, **kwargs)

    def _stage_b(self) -> tuple[dict[str, Any], Path]:
        path = _resolve(self.config["stage_b_validation_path"], self.repo_root)
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if receipt.get("status") != "pass" or receipt.get("holdout_consumed"):
            raise ValueError("Stage-B validation is not passing with isolated holdout")
        promoted = self.config["promoted_signal"]
        matches = [
            row
            for row in receipt.get("signals", [])
            if row.get("signal_id") == promoted["signal_id"]
            and row.get("signal_run_id") == promoted["signal_run_id"]
            and row.get("predictions_sha256") == promoted["predictions_sha256"]
        ]
        if len(matches) != 1:
            raise ValueError("promoted signal is not uniquely bound to Stage-B validation")
        if str(receipt.get("holdout_start")) != str(self.config["holdout_start"]):
            raise ValueError("Stage-B and Stage-C holdout boundaries differ")
        return receipt, path

    def _accounting_gap(self) -> tuple[dict[str, Any], dict[str, str]]:
        evidence = self.config["accounting_evidence"]
        instrument = str(evidence["instrument"])
        previous_date = _date_key(evidence["previous_trade_date"])
        detection_date = _date_key(evidence["detection_trade_date"])
        market_path = _resolve(evidence["canonical_data_root"], self.repo_root) / (
            f"{instrument}.feather"
        )
        market = pd.read_feather(market_path)
        dates = market["trade_date"].map(_date_key)
        previous = market.loc[dates == previous_date]
        current = market.loc[dates == detection_date]
        if len(previous) != 1 or len(current) != 1:
            raise ValueError("accounting evidence dates are not unique market rows")
        previous_row = previous.iloc[0]
        current_row = current.iloc[0]
        previous_factor = _finite_float(previous_row["factor"], "previous factor")
        current_factor = _finite_float(current_row["factor"], "current factor")
        relative_jump = current_factor / previous_factor - 1.0
        tolerance = _finite_float(
            evidence["factor_rounding_relative_tolerance"], "factor tolerance"
        )
        if abs(relative_jump) <= tolerance:
            raise ValueError("configured factor discontinuity is below guard tolerance")

        artifact_name = str(evidence["corporate_action_artifact"])
        action_dir = self.root / "corporate_actions" / artifact_name
        action_manifest_path = action_dir / "manifest.json"
        events_path = action_dir / "events.parquet"
        action_manifest = json.loads(action_manifest_path.read_text(encoding="utf-8"))
        if action_manifest.get("schema_version") != "corporate_actions_v1":
            raise ValueError("unsupported corporate-action artifact schema")
        events = pd.read_parquet(events_path)
        event_dates = events["effective_date"].map(_date_key)
        covered = events[
            (events["instrument"].astype(str) == instrument)
            & event_dates.between(previous_date, detection_date)
        ]
        if not covered.empty:
            raise ValueError("factor discontinuity is covered by the action artifact")

        observed = {
            "instrument": instrument,
            "previous_trade_date": previous_date,
            "detection_trade_date": detection_date,
            "previous_factor": previous_factor,
            "current_factor": current_factor,
            "factor_relative_jump": relative_jump,
            "factor_rounding_relative_tolerance": tolerance,
            "previous_total_share": _finite_float(
                previous_row["total_share"], "previous total share"
            ),
            "current_total_share": _finite_float(
                current_row["total_share"], "current total share"
            ),
            "covered_event_count": 0,
            "guard_error": str(evidence["guard_error"]),
            "missing_capability": str(evidence["missing_capability"]),
        }
        hashes = {
            "canonical_market_data_sha256": _sha256(market_path),
            "corporate_action_manifest_sha256": _sha256(action_manifest_path),
            "corporate_action_events_sha256": _sha256(events_path),
        }
        observed["corporate_action_source"] = action_manifest.get("source")
        observed["corporate_action_source_raw_artifact_sha256"] = action_manifest.get(
            "source_raw_artifact_sha256"
        )
        return observed, hashes

    def _exploratory_runs(
        self, instrument: str, detection_date: str
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        promoted = self.config["promoted_signal"]
        holdout_start = str(self.config["holdout_start"])
        rows: list[dict[str, Any]] = []
        hashes: dict[str, str] = {}
        for spec in self.config.get("exploratory_runs", []):
            run_dir = _resolve(spec["path"], self.repo_root)
            manifest_path = run_dir / "manifest.json"
            metrics_path = run_dir / "metrics.json"
            daily_path = run_dir / "daily_summary.csv"
            executions_path = run_dir / "executions.csv"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            top_n = int(spec["top_n"])
            if (
                manifest.get("corporate_action_policy") != "not_modeled"
                or int(manifest.get("allocation_params", {}).get("top_n", -1)) != top_n
                or manifest.get("signal_id") != promoted["signal_id"]
                or manifest.get("signal_run_id") != promoted["signal_run_id"]
                or str(manifest.get("effective_end_date")) >= holdout_start
            ):
                raise ValueError(f"exploratory Top{top_n} run contract mismatch")
            sources = manifest.get("signal_sources") or []
            if len(sources) != 1 or sources[0].get("predictions_sha256") != promoted[
                "predictions_sha256"
            ]:
                raise ValueError(f"exploratory Top{top_n} signal lineage mismatch")
            if (
                not manifest.get("pit_execution_universe")
                or int(metrics.get("trading_day_count", 0)) <= 0
                or not math.isclose(
                    float(metrics["total_return"]), float(manifest["total_return"])
                )
                or not math.isclose(
                    float(metrics["final_value"]), float(manifest["final_value"])
                )
            ):
                raise ValueError(f"exploratory Top{top_n} artifact mismatch")
            executions = pd.read_csv(executions_path)
            held_quantity = _held_quantity(executions, instrument, detection_date)
            if held_quantity <= 0:
                raise ValueError(
                    f"exploratory Top{top_n} did not hold the blocker instrument"
                )
            prefix = f"top{top_n}"
            for name, path in (
                ("manifest", manifest_path),
                ("metrics", metrics_path),
                ("daily_summary", daily_path),
                ("executions", executions_path),
            ):
                hashes[f"{prefix}_{name}_sha256"] = _sha256(path)
            rows.append(
                {
                    "top_n": top_n,
                    "status": "exploratory_only",
                    "complete_accounting": False,
                    "held_blocker_quantity_before_detection": held_quantity,
                    "cagr": _finite_float(metrics["cagr"], "CAGR"),
                    "sharpe": _finite_float(metrics["sharpe"], "Sharpe"),
                    "max_drawdown": _finite_float(
                        metrics["max_drawdown"], "max drawdown"
                    ),
                    "turnover_annualized": _finite_float(
                        metrics["turnover_annualized"], "annualized turnover"
                    ),
                    "total_return": _finite_float(
                        metrics["total_return"], "total return"
                    ),
                    "order_count": int(metrics["order_count_total"]),
                    "filled_count": int(metrics["filled_count_total"]),
                    "rejected_count": int(metrics["rejected_count_total"]),
                }
            )
        if sorted(row["top_n"] for row in rows) != sorted(
            int(value) for value in self.config["portfolio_sizes"]
        ):
            raise ValueError("exploratory run set does not match portfolio_sizes")
        return sorted(rows, key=lambda row: row["top_n"]), hashes

    def run(self) -> dict[str, Any]:
        stage_b, stage_b_path = self._stage_b()
        accounting_gap, accounting_hashes = self._accounting_gap()
        exploratory, exploratory_hashes = self._exploratory_runs(
            accounting_gap["instrument"],
            f"{accounting_gap['detection_trade_date'][:4]}-"
            f"{accounting_gap['detection_trade_date'][4:6]}-"
            f"{accounting_gap['detection_trade_date'][6:]}",
        )
        artifact = {
            "schema_version": SCHEMA_VERSION,
            "assessment_id": self.assessment_id,
            "formal_status": "accounting_data_blocked",
            "portfolio_extractability": "not_established",
            "promotion_eligible": False,
            "holdout_start": str(self.config["holdout_start"]),
            "holdout_consumed": False,
            "stage_b": {
                "experiment_id": stage_b["experiment_id"],
                "validation_sha256": _sha256(stage_b_path),
                "promoted_signal": dict(self.config["promoted_signal"]),
            },
            "strict_protocol": dict(self.config["strict_protocol"]),
            "accounting_evidence": accounting_gap,
            "exploratory_portfolio_comparison": exploratory,
            "external_evidence": list(self.config.get("external_evidence", [])),
            "decision": {
                "reason": "complete_accounting_input_and_policy_gap",
                "next_gate": str(self.config["next_gate"]),
            },
        }
        artifact_path = self.output_dir / "stage_c_assessment.json"
        _write_json(artifact_path, artifact)
        input_hashes = {
            "stage_b_validation_sha256": _sha256(stage_b_path),
            **accounting_hashes,
            **exploratory_hashes,
        }
        identity = {
            "schema_version": SCHEMA_VERSION,
            "assessment_id": self.assessment_id,
            "config_sha256": _sha256(self.config_path),
            "producer_code_sha256": _sha256(Path(__file__)),
            "input_hashes": input_hashes,
        }
        manifest = {
            **identity,
            "stage_c_identity_sha256": _canonical_hash(identity),
            "outputs": {
                "stage_c_assessment.json": {"sha256": _sha256(artifact_path)}
            },
        }
        manifest_path = self.output_dir / "manifest.json"
        _write_json(manifest_path, manifest)
        return {
            "assessment_id": self.assessment_id,
            "stage_c_identity_sha256": manifest["stage_c_identity_sha256"],
            "manifest": str(manifest_path),
        }
