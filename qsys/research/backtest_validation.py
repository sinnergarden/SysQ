"""Read-only validation for canonical complete-accounting backtests."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from qsys.backtest.accounting import CorporateActionStore
from qsys.backtest.market_data import MarketDataAdapter
from qsys.research.pit_universe import PitUniverseStore
from qsys.signal.store import SignalStore


CONFIG_SCHEMA_VERSION = "complete_accounting_backtest_validation_config_v1"
REQUIRED_ARTIFACTS = {
    "daily_summary": "backtest_daily_summary_v1",
    "executions": "backtest_executions_v2",
    "corporate_action_ledger": "corporate_action_ledger_v1",
    "valuation_ledger": "valuation_ledger_v1",
    "accounting_attribution": "accounting_attribution_v1",
    "metrics": "metrics_v1",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve(value: str | Path, repo_root: Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (repo_root / path).resolve()


def _regular_file(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is not a regular file: {path}")


def _artifact_path(backtest_dir: Path, descriptor: dict[str, Any], label: str) -> Path:
    relative = Path(str(descriptor.get("path") or ""))
    if relative.is_absolute() or len(relative.parts) != 1 or relative.name in {"", ".", ".."}:
        raise ValueError(f"{label} artifact path must be a bare filename")
    path = backtest_dir / relative
    _regular_file(path, label)
    return path


def _require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{label} missing columns: {missing}")


def _dates(values: pd.Series, label: str) -> pd.Series:
    parsed = pd.to_datetime(values.astype(str), errors="coerce").dt.normalize()
    if parsed.isna().any():
        raise ValueError(f"{label} contains invalid dates")
    return parsed


def _finite_numeric(frame: pd.DataFrame, columns: set[str], label: str) -> None:
    for column in sorted(columns):
        values = pd.to_numeric(frame[column], errors="coerce")
        if values.isna().any() or not values.map(math.isfinite).all():
            raise ValueError(f"{label}.{column} contains null or non-finite values")


def _close(left: Any, right: Any, label: str, *, tolerance: float = 1e-6) -> None:
    if not math.isclose(
        float(left), float(right), rel_tol=1e-10, abs_tol=tolerance
    ):
        raise ValueError(f"{label} mismatch: {left!r} != {right!r}")


def _validate_artifact_bindings(
    backtest_dir: Path,
    manifest: dict[str, Any],
) -> dict[str, Path]:
    top = manifest.get("artifacts") or {}
    accounting = manifest.get("accounting") or {}
    nested = accounting.get("artifacts") or {}
    paths: dict[str, Path] = {}
    for name, schema in REQUIRED_ARTIFACTS.items():
        descriptor = top.get(name)
        if not isinstance(descriptor, dict) or descriptor != nested.get(name):
            raise ValueError(f"{name} artifact binding is absent or inconsistent")
        if descriptor.get("schema_version") != schema or descriptor.get("complete") is not True:
            raise ValueError(f"{name} is not a complete {schema} artifact")
        path = _artifact_path(backtest_dir, descriptor, name)
        if _sha256(path) != descriptor.get("sha256"):
            raise ValueError(f"{name} SHA256 mismatch")
        paths[name] = path
    return paths


def _validate_daily_and_metrics(
    manifest: dict[str, Any],
    paths: dict[str, Path],
    *,
    identity_tolerance: float,
) -> tuple[pd.DataFrame, dict[str, Any], float]:
    daily = pd.read_csv(paths["daily_summary"])
    required = {
        "trade_date", "cash_before", "receivable_before", "market_value_before",
        "total_value_before", "cash_after", "receivable_after", "market_value_after",
        "total_value_after", "order_count", "filled_count", "rejected_count",
        "accounting_identity_error",
    }
    _require_columns(daily, required, "daily_summary")
    if daily.empty:
        raise ValueError("daily_summary is empty")
    trade_dates = _dates(daily["trade_date"], "daily_summary.trade_date")
    if trade_dates.duplicated().any() or not trade_dates.is_monotonic_increasing:
        raise ValueError("daily_summary trade dates must be unique and increasing")
    numeric = required - {"trade_date"}
    _finite_numeric(daily, numeric, "daily_summary")
    if (daily[["cash_before", "receivable_before", "market_value_before",
               "cash_after", "receivable_after", "market_value_after",
               "total_value_before", "total_value_after"]] < 0).any().any():
        raise ValueError("daily_summary contains negative balance-sheet values")
    before = daily["cash_before"] + daily["receivable_before"] + daily["market_value_before"]
    after = daily["cash_after"] + daily["receivable_after"] + daily["market_value_after"]
    if (before - daily["total_value_before"]).abs().max() > identity_tolerance:
        raise ValueError("daily_summary before-state accounting identity mismatch")
    if (after - daily["total_value_after"]).abs().max() > identity_tolerance:
        raise ValueError("daily_summary after-state accounting identity mismatch")
    max_identity_error = float(daily["accounting_identity_error"].abs().max())
    if max_identity_error > identity_tolerance:
        raise ValueError("daily accounting identity error exceeds tolerance")

    start = trade_dates.iloc[0].strftime("%Y-%m-%d")
    end = trade_dates.iloc[-1].strftime("%Y-%m-%d")
    if (
        start != str(manifest.get("start_date"))
        or end != str(manifest.get("end_date"))
        or start != str(manifest.get("effective_start_date"))
        or end != str(manifest.get("effective_end_date"))
    ):
        raise ValueError("daily_summary endpoints differ from manifest")
    declared_dates = [str(value)[:10] for value in manifest.get("trading_dates", [])]
    actual_dates = trade_dates.dt.strftime("%Y-%m-%d").tolist()
    if declared_dates != actual_dates or int(manifest.get("trading_day_count", -1)) != len(daily):
        raise ValueError("daily_summary trading calendar differs from manifest")

    metrics = json.loads(paths["metrics"].read_text(encoding="utf-8"))
    for key in (
        "initial_capital", "final_value", "total_return", "trading_day_count",
        "order_count_total", "filled_count_total", "rejected_count_total",
    ):
        if key not in metrics:
            raise ValueError(f"metrics missing {key}")
    _close(daily.iloc[-1]["total_value_after"], metrics["final_value"], "final value")
    _close(metrics["initial_capital"], manifest["initial_capital"], "initial capital")
    _close(metrics["final_value"], manifest["final_value"], "manifest final value")
    _close(metrics["total_return"], manifest["total_return"], "manifest total return")
    _close(
        metrics["total_return"],
        float(metrics["final_value"]) / float(metrics["initial_capital"]) - 1.0,
        "computed total return",
    )
    if int(metrics["trading_day_count"]) != len(daily):
        raise ValueError("metrics trading_day_count mismatch")
    for metric, column in (
        ("order_count_total", "order_count"),
        ("filled_count_total", "filled_count"),
        ("rejected_count_total", "rejected_count"),
    ):
        if int(metrics[metric]) != int(daily[column].sum()):
            raise ValueError(f"metrics {metric} mismatch")
    return daily, metrics, max_identity_error


def _validate_ledgers(
    paths: dict[str, Path],
    descriptors: dict[str, Any],
    daily: pd.DataFrame,
    metrics: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    executions = pd.read_csv(paths["executions"])
    _require_columns(
        executions,
        {"execution_id", "trade_date", "instrument", "side", "status", "filled_qty",
         "commission", "tax", "total_fee", "rejection_reason"},
        "executions",
    )
    if executions["execution_id"].duplicated().any():
        raise ValueError("executions contains duplicate execution_id")
    if set(executions["status"].astype(str)) - {"filled", "rejected"}:
        raise ValueError("executions contains unsupported status")
    if set(executions["side"].astype(str)) - {"buy", "sell"}:
        raise ValueError("executions contains unsupported side")
    execution_dates = _dates(executions["trade_date"], "executions.trade_date")
    allowed_dates = set(pd.to_datetime(daily["trade_date"]).dt.normalize())
    if not set(execution_dates).issubset(allowed_dates):
        raise ValueError("executions contains dates outside daily_summary")
    filled = executions["status"].astype(str).eq("filled")
    quantity = pd.to_numeric(executions["filled_qty"], errors="coerce")
    if quantity.isna().any() or (quantity < 0).any() or (filled & quantity.le(0)).any():
        raise ValueError("executions contains invalid filled quantities")
    if len(executions) != int(metrics["order_count_total"]):
        raise ValueError("execution row count differs from metrics")
    if int(filled.sum()) != int(metrics["filled_count_total"]):
        raise ValueError("filled execution count differs from metrics")
    if int((~filled).sum()) != int(metrics["rejected_count_total"]):
        raise ValueError("rejected execution count differs from metrics")

    valuation = pd.read_csv(paths["valuation_ledger"])
    _require_columns(
        valuation,
        {"trade_date", "instrument", "quantity", "sellable_quantity", "cost_price",
         "last_price", "market_value", "price_date", "stale_price", "stale_days"},
        "valuation_ledger",
    )
    if valuation[["trade_date", "instrument"]].duplicated().any():
        raise ValueError("valuation_ledger contains duplicate date/instrument rows")
    _finite_numeric(
        valuation,
        {"quantity", "sellable_quantity", "cost_price", "last_price", "market_value", "stale_days"},
        "valuation_ledger",
    )
    valuation_dates = _dates(valuation["trade_date"], "valuation_ledger.trade_date")
    price_dates = _dates(valuation["price_date"], "valuation_ledger.price_date")
    if not set(valuation_dates).issubset(allowed_dates) or (price_dates > valuation_dates).any():
        raise ValueError("valuation ledger has invalid date lineage")
    expected_value = valuation["quantity"].astype(float) * valuation["last_price"].astype(float)
    if (expected_value - valuation["market_value"].astype(float)).abs().max() > 1e-4:
        raise ValueError("valuation ledger market_value mismatch")

    actions = pd.read_csv(paths["corporate_action_ledger"])
    _require_columns(
        actions,
        {"event_id", "date", "event_type", "instrument", "shares_before", "shares_after",
         "cash_delta", "receivable_delta", "basis_before", "basis_after", "status"},
        "corporate_action_ledger",
    )
    if not actions.empty:
        action_dates = _dates(actions["date"], "corporate_action_ledger.date")
        if not set(action_dates).issubset(allowed_dates):
            raise ValueError("corporate-action ledger contains dates outside daily_summary")
        if set(actions["status"].astype(str)) - {"applied", "no_position", "settled"}:
            raise ValueError("corporate-action ledger contains unsupported status")

    attribution = json.loads(paths["accounting_attribution"].read_text(encoding="utf-8"))
    if attribution.get("schema_version") != "accounting_attribution_v1":
        raise ValueError("accounting attribution schema mismatch")
    action_summary = attribution.get("corporate_actions") or {}
    status_counts = actions["status"].astype(str).value_counts().to_dict()
    for key, status in (
        ("held_applied_event_count", "applied"),
        ("no_position_event_count", "no_position"),
        ("settlement_count", "settled"),
    ):
        if int(action_summary.get(key, -1)) != int(status_counts.get(status, 0)):
            raise ValueError(f"accounting attribution {key} mismatch")

    for name, frame in (
        ("daily_summary", daily), ("executions", executions),
        ("corporate_action_ledger", actions), ("valuation_ledger", valuation),
    ):
        if int(descriptors[name]["row_count"]) != len(frame):
            raise ValueError(f"{name} row_count mismatch")
    if int(descriptors["metrics"]["row_count"]) != 1:
        raise ValueError("metrics row_count mismatch")
    if int(descriptors["accounting_attribution"]["row_count"]) != 1:
        raise ValueError("accounting_attribution row_count mismatch")
    return executions, valuation, actions, attribution


def _validate_holdout(
    manifest: dict[str, Any], config: dict[str, Any], daily: pd.DataFrame
) -> tuple[bool, str | None]:
    holdout_start = str(config.get("holdout_start") or "").strip()
    if not holdout_start:
        raise ValueError("validation config requires holdout_start")
    holdout = pd.Timestamp(holdout_start).normalize()
    consumed = bool(pd.to_datetime(daily["trade_date"]).max().normalize() >= holdout)
    declared = manifest.get("terminal_holdout") or {}
    if str(declared.get("holdout_start")) != holdout.strftime("%Y-%m-%d"):
        raise ValueError("terminal holdout boundary mismatch")
    if bool(declared.get("holdout_consumed")) != consumed:
        raise ValueError("terminal holdout consumption flag mismatch")
    authorization = str(declared.get("terminal_authorization_ref") or "").strip() or None
    expected_authorization = str(config.get("terminal_authorization_ref") or "").strip() or None
    if consumed and not authorization:
        raise ValueError("terminal holdout was consumed without authorization")
    if expected_authorization != authorization:
        raise ValueError("terminal authorization differs from validation config")
    return consumed, authorization


def _validate_lineage(
    manifest: dict[str, Any], research_root: Path,
    executions: pd.DataFrame, repo_root: Path,
) -> tuple[int, int, str]:
    signal_store = SignalStore(research_root)
    sources = manifest.get("signal_sources") or []
    if not sources:
        raise ValueError("backtest manifest has no SignalRun lineage")
    signal_rows = 0
    for source in sources:
        signal_id = str(source.get("signal_id") or "")
        run_id = str(source.get("signal_run_id") or "")
        signal_manifest_path = signal_store.paths.signal_manifest(signal_id, run_id)
        _regular_file(signal_manifest_path, "SignalRun manifest")
        data_candidates = [
            signal_store.paths.signal_file(signal_id, run_id, fmt=extension)
            for extension in ("parquet", "csv")
        ]
        data_paths = [path for path in data_candidates if path.exists()]
        if len(data_paths) != 1:
            raise ValueError("SignalRun must contain exactly one predictions artifact")
        _regular_file(data_paths[0], "SignalRun predictions")
        actual = signal_store.validate_backtest_source(signal_id, run_id)
        if actual != source:
            raise ValueError(f"SignalRun identity mismatch: {signal_id}/{run_id}")
        frame = signal_store.load_signal_run(
            signal_id, run_id,
            start_date=str(manifest["start_date"]), end_date=str(manifest["end_date"]),
        )
        _require_columns(frame, {"trade_date", "data_date", "instrument"}, "SignalRun")
        trade_date = _dates(frame["trade_date"], "SignalRun.trade_date")
        data_date = _dates(frame["data_date"], "SignalRun.data_date")
        if (data_date >= trade_date).any():
            raise ValueError("SignalRun contains lookahead rows")
        signal_rows += len(frame)

    pit = manifest.get("pit_execution_universe") or {}
    artifact_name = str(pit.get("artifact") or "")
    if not artifact_name or Path(artifact_name).name != artifact_name:
        raise ValueError("backtest manifest has no valid PIT execution universe")
    universe_dir = research_root / "universes" / artifact_name
    if universe_dir.is_symlink() or not universe_dir.is_dir():
        raise ValueError("PIT universe artifact must be a regular directory")
    store = PitUniverseStore(universe_dir, verify_hash=True)
    manifest_path = universe_dir / "manifest.json"
    membership_path = universe_dir / "membership.parquet"
    _regular_file(manifest_path, "PIT universe manifest")
    _regular_file(membership_path, "PIT universe membership")
    actual_pit = {
        "artifact": artifact_name,
        **store.provenance.to_dict(),
        "manifest_sha256": _sha256(manifest_path),
    }
    for key, value in actual_pit.items():
        if pit.get(key) != value:
            raise ValueError(f"PIT universe identity mismatch: {key}")
    # A position can be sold after leaving the index; only new filled buys
    # must be members on their execution date.
    filled = executions[
        executions["status"].astype(str).eq("filled")
        & executions["side"].astype(str).eq("buy")
    ]
    for (date, instrument), _ in filled.groupby(["trade_date", "instrument"], sort=False):
        if not store.is_member(str(instrument), str(date)):
            raise ValueError(f"filled execution outside PIT universe: {instrument} on {date}")

    accounting = manifest.get("accounting") or {}
    action_name = str(accounting.get("corporate_action_artifact") or "")
    action_store = CorporateActionStore(research_root, action_name)
    if action_store.manifest != accounting.get("corporate_action_manifest"):
        raise ValueError("corporate-action manifest lineage mismatch")

    market_root = _resolve(str(accounting.get("canonical_data_root") or ""), repo_root)
    if market_root.is_symlink() or not market_root.is_dir():
        raise ValueError("canonical market slice must be a regular directory")
    expected_market = manifest.get("market_source_identity") or {}
    instruments = [str(value) for value in expected_market.get("used_instruments", [])]
    if not instruments or expected_market.get("requested_missing_instruments"):
        raise ValueError("complete accounting market-source coverage is incomplete")
    actual_market = MarketDataAdapter(market_root).source_identity(instruments)
    if actual_market != expected_market:
        raise ValueError("market source identity mismatch")
    market_slice = actual_market.get("market_slice") or {}
    if str(market_slice.get("through_date")) != str(manifest.get("end_date")):
        raise ValueError("market slice does not end at backtest end_date")
    return signal_rows, len(instruments), str(actual_market["aggregate_sha256"])


def validate_complete_accounting_backtest(
    config_path: str | Path,
    *,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """Validate a frozen backtest and return a read-only receipt."""

    config_path = Path(config_path).resolve()
    _regular_file(config_path, "validation config")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ValueError("unsupported complete-accounting validation config")
    repo = Path(repo_root).resolve() if repo_root is not None else Path(__file__).resolve().parents[2]
    backtest_dir = _resolve(config["backtest_dir"], repo)
    research_root = _resolve(config.get("research_root", "data/research"), repo)
    if backtest_dir.is_symlink() or not backtest_dir.is_dir():
        raise ValueError("backtest_dir must be a regular directory")
    if research_root.is_symlink() or not research_root.is_dir():
        raise ValueError("research_root must be a regular directory")
    manifest_path = backtest_dir / "manifest.json"
    _regular_file(manifest_path, "backtest manifest")
    manifest_sha = _sha256(manifest_path)
    if manifest_sha != str(config.get("expected_manifest_sha256") or ""):
        raise ValueError("backtest manifest SHA256 differs from validation config")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("artifact_type") != "backtest_run":
        raise ValueError("artifact is not a canonical backtest_run")
    accounting = manifest.get("accounting") or {}
    params = manifest.get("accounting_params") or {}
    if (
        accounting.get("schema_version") != "accounting_v1"
        or accounting.get("valuation_policy") != "stale_last_legal_close"
        or accounting.get("execution_policy") != "raw_price_event_ledger_v1"
        or accounting.get("liquidity_policy") != "strict_prior_ADV"
        or accounting.get("liquidity_gate_mode") != "reject"
        or accounting.get("t_plus_one") is not True
        or not math.isclose(float(accounting.get("max_participation_rate", -1)), 0.10)
        or params.get("corporate_action_artifact") != accounting.get("corporate_action_artifact")
        or params.get("canonical_data_root") != accounting.get("canonical_data_root")
        or params.get("liquidity_gate_mode") != accounting.get("liquidity_gate_mode")
        or not math.isclose(
            float(params.get("max_participation_rate", -1)),
            float(accounting.get("max_participation_rate", -2)),
        )
        or int(params.get("adv_window", 0)) != int(accounting.get("adv_window", -1))
        or int(params.get("adv_min_periods", 0)) != int(
            accounting.get("adv_min_periods", -1)
        )
        or manifest.get("corporate_action_policy") != "raw_price_event_ledger_v1"
    ):
        raise ValueError("backtest does not satisfy the complete-accounting policy")
    adv_window = int(accounting.get("adv_window", 0))
    adv_min = int(accounting.get("adv_min_periods", 0))
    if adv_window <= 0 or not 1 <= adv_min <= adv_window:
        raise ValueError("invalid strict-prior ADV policy")

    paths = _validate_artifact_bindings(backtest_dir, manifest)
    tolerance = float(config.get("accounting_identity_tolerance", 1e-6))
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("accounting_identity_tolerance must be positive and finite")
    daily, metrics, max_error = _validate_daily_and_metrics(
        manifest, paths, identity_tolerance=tolerance
    )
    executions, _, actions, _ = _validate_ledgers(
        paths, manifest["artifacts"], daily, metrics
    )
    holdout_consumed, authorization = _validate_holdout(manifest, config, daily)
    signal_rows, market_files, market_sha = _validate_lineage(
        manifest, research_root, executions, repo
    )
    return {
        "schema_version": "complete_accounting_backtest_validation_v1",
        "status": "pass",
        "config_sha256": _sha256(config_path),
        "backtest_id": manifest.get("backtest_id"),
        "manifest_sha256": manifest_sha,
        "start_date": manifest.get("start_date"),
        "end_date": manifest.get("end_date"),
        "trading_day_count": len(daily),
        "signal_row_count": signal_rows,
        "execution_row_count": len(executions),
        "corporate_action_ledger_row_count": len(actions),
        "market_file_count": market_files,
        "market_source_identity_sha256": market_sha,
        "max_accounting_identity_error": max_error,
        "holdout_consumed": holdout_consumed,
        "terminal_authorization_ref": authorization,
        "total_return": float(metrics["total_return"]),
        "cagr": float(metrics["cagr"]),
        "sharpe": float(metrics["sharpe"]),
        "max_drawdown": float(metrics["max_drawdown"]),
    }


__all__ = ["validate_complete_accounting_backtest"]
