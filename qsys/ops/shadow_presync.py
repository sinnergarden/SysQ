from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from qsys.data.adapter import QlibAdapter
from qsys.ops.instrument_coverage import (
    DEFAULT_MIN_ACTIVE_INSTRUMENTS,
    apply_repair_plan,
    build_instrument_coverage_rows,
    build_repair_plan,
    summarize_universe_registry,
)
from qsys.ops.manifest import finalize_run, format_run_id, initialize_run, update_stage_status
from qsys.ops.qlib_sync import run_targeted_qlib_sync
from qsys.ops.raw_sync import RAW_PLAN_COLUMNS, load_success_symbols_from_plan, run_targeted_raw_update
from qsys.ops.state import atomic_write_json, load_json
from qsys.ops.trade_date import resolve_daily_trade_date
from qsys.ops.universe_sync import build_universe_snapshot


DEFAULT_UNIVERSE = "csi300"


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _set_stage(context, *, stage_name: str, status: str, message: str, artifact_pointers: dict[str, Any] | None = None, started_at: str | None = None) -> None:
    now_text = datetime.now().replace(microsecond=0).isoformat()
    update_stage_status(
        context,
        stage_name=stage_name,
        status=status,
        started_at=started_at or now_text,
        ended_at=now_text,
        message=message,
        artifact_pointers=artifact_pointers or {},
    )


def _load_symbols_from_file(path: str | Path | None) -> list[str]:
    if not path:
        return []
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip()]


def _select_symbols(
    universe_symbols: list[str],
    *,
    max_symbols: int | None = None,
    symbols: list[str] | None = None,
    symbols_file: str | Path | None = None,
) -> list[str]:
    selected = universe_symbols
    explicit = symbols or []
    file_symbols = _load_symbols_from_file(symbols_file)
    if explicit or file_symbols:
        requested = []
        seen = set()
        for symbol in explicit + file_symbols:
            if symbol not in seen:
                requested.append(symbol)
                seen.add(symbol)
        allowed = set(universe_symbols)
        selected = [symbol for symbol in requested if symbol in allowed]
    if max_symbols is not None:
        selected = selected[: max_symbols]
    return selected


def _find_previous_raw_plan(base_dir: Path) -> Path | None:
    latest = load_json(base_dir / "runs" / "latest_shadow_presync.json")
    manifest_path = latest.get("manifest_path") if latest else None
    if not manifest_path:
        return None
    run_dir = Path(str(manifest_path)).parent
    candidate = run_dir / "02_raw" / "raw_update_plan.csv"
    return candidate if candidate.exists() else None


def _load_resume_success_symbols(base_dir: Path, resume: bool) -> set[str]:
    if not resume:
        return set()
    plan_path = _find_previous_raw_plan(base_dir)
    if plan_path is None:
        return set()
    return load_success_symbols_from_plan(plan_path)


def _write_selected_symbols(output_dir: Path, selected_symbols: list[str]) -> Path:
    rows = [{"symbol": symbol} for symbol in selected_symbols]
    return _write_csv(output_dir / "selected_symbols.csv", rows, ["symbol"])


def run_shadow_presync(
    base_dir: str | Path,
    *,
    run_id: str | None = None,
    universe: str = DEFAULT_UNIVERSE,
    target_date: str | None = None,
    lookback_days: int = 20,
    apply: bool = False,
    triggered_by: str = "manual",
    max_symbols: int | None = None,
    symbols: list[str] | None = None,
    symbols_file: str | Path | None = None,
    raw_only: bool = False,
    qlib_only: bool = False,
    skip_qlib_sync: bool = False,
    skip_instrument_repair: bool = False,
    resume: bool = False,
) -> dict[str, Any]:
    if universe != DEFAULT_UNIVERSE:
        raise ValueError(f"Unsupported universe for shadow presync: {universe}")
    if raw_only and qlib_only:
        raise ValueError("raw_only and qlib_only cannot both be true")

    base_dir = Path(base_dir)
    now = datetime.now()
    resolved_run_id = run_id or format_run_id("presync", now)
    adapter = QlibAdapter()
    adapter.init_qlib()
    previous_qlib_ts = adapter.get_last_qlib_date()
    previous_qlib_last_date = previous_qlib_ts.strftime("%Y-%m-%d") if previous_qlib_ts is not None else None
    date_resolution = resolve_daily_trade_date(target_date, universe=universe, allow_fallback_to_latest=True)
    resolved_trade_date = date_resolution.get("resolved_trade_date") or date_resolution.get("requested_date")

    context = initialize_run(
        base_dir,
        run_type="presync",
        run_id=resolved_run_id,
        trade_date=resolved_trade_date,
        mainline_object_name="feature_173",
        bundle_id="bundle_feature_173",
        model_name="",
        model_snapshot_path="",
        latest_model_pointer=str(base_dir / "models" / "latest_shadow_model.json"),
        data_snapshot={
            "triggered_by": triggered_by,
            "requested_date": date_resolution.get("requested_date"),
            "resolved_trade_date": resolved_trade_date,
            "previous_qlib_last_date": previous_qlib_last_date,
            "apply": apply,
            "universe": universe,
            "lookback_days": lookback_days,
            "max_symbols": max_symbols,
            "symbols": list(symbols or []),
            "symbols_file": str(symbols_file) if symbols_file else None,
            "raw_only": raw_only,
            "qlib_only": qlib_only,
            "skip_qlib_sync": skip_qlib_sync,
            "skip_instrument_repair": skip_instrument_repair,
            "resume": resume,
        },
        fallback_summary={"used": date_resolution.get("status") != "success"},
        notes=["Targeted shadow presync is csi300-only and stays dry-run unless --apply is set."],
    )
    manifest = load_json(context.manifest_path)
    manifest["date_resolution"] = date_resolution
    atomic_write_json(context.manifest_path, manifest)

    universe_dir = context.run_dir / "01_universe"
    raw_dir = context.run_dir / "02_raw"
    qlib_dir = context.run_dir / "03_qlib"
    instrument_dir = context.run_dir / "04_instrument"

    universe_started = datetime.now().replace(microsecond=0).isoformat()
    universe_symbols, universe_summary, universe_snapshot_path, universe_summary_path = build_universe_snapshot(
        adapter=adapter,
        universe=universe,
        as_of_date=resolved_trade_date,
        output_dir=universe_dir,
    )
    selected_symbols = _select_symbols(universe_symbols, max_symbols=max_symbols, symbols=symbols, symbols_file=symbols_file)
    selected_symbols_path = _write_selected_symbols(universe_dir, selected_symbols)
    universe_status = "success" if universe_symbols else "failed"
    _set_stage(
        context,
        stage_name="universe_sync",
        status=universe_status,
        started_at=universe_started,
        message="Universe snapshot captured." if universe_symbols else "Universe snapshot is empty.",
        artifact_pointers={
            "universe_snapshot_path": str(universe_snapshot_path),
            "universe_summary_path": str(universe_summary_path),
            "selected_symbols_path": str(selected_symbols_path),
        },
    )

    resume_success_symbols = _load_resume_success_symbols(base_dir, resume)

    raw_started = datetime.now().replace(microsecond=0).isoformat()
    if qlib_only:
        raw_summary = {
            "universe": universe,
            "target_symbol_count": len(universe_symbols),
            "selected_symbol_count": len(selected_symbols),
            "symbols_attempted": 0,
            "symbols_updated": 0,
            "symbols_failed": 0,
            "symbols_unchanged": 0,
            "symbols_with_raw_on_target": 0,
            "status": "skipped",
        }
        raw_plan_path = _write_csv(raw_dir / "raw_update_plan.csv", [], RAW_PLAN_COLUMNS)
        raw_summary_path = _write_json(raw_dir / "raw_update_summary.json", raw_summary)
        affected_symbols = selected_symbols
        raw_stage_status = "success"
    else:
        raw_summary, raw_plan_path, raw_summary_path, affected_symbols = run_targeted_raw_update(
            symbols=universe_symbols,
            selected_symbols=set(selected_symbols),
            resume_success_symbols=resume_success_symbols,
            target_date=resolved_trade_date,
            lookback_days=lookback_days,
            apply=apply,
            output_dir=raw_dir,
            universe=universe,
        )
        raw_stage_status = "success" if raw_summary["status"] in {"success", "partial", "skipped"} else "failed"
    _set_stage(
        context,
        stage_name="raw_update",
        status=raw_stage_status,
        started_at=raw_started,
        message=f"Targeted raw update {raw_summary['status']}.",
        artifact_pointers={"raw_update_plan_path": str(raw_plan_path), "raw_update_summary_path": str(raw_summary_path)},
    )

    qlib_started = datetime.now().replace(microsecond=0).isoformat()
    if raw_only:
        qlib_summary = {
            "previous_qlib_last_date": previous_qlib_last_date,
            "post_sync_qlib_last_date": previous_qlib_last_date,
            "affected_symbol_count": 0,
            "symbols_attempted": 0,
            "symbols_synced": 0,
            "symbols_failed": 0,
            "symbols_validated": 0,
            "backup_status": "skipped",
            "rollback_status": "not_needed",
            "qlib_update_status": "skipped",
            "convert_mode": "skipped",
            "reason": "raw-only mode skips qlib sync",
        }
        affected_symbols_path = _write_csv(qlib_dir / "affected_symbols.csv", [], ["symbol", "selected_for_apply"])
        qlib_symbol_sync_path = _write_csv(qlib_dir / "qlib_symbol_sync.csv", [], ["symbol", "original_feature_path", "raw_last_date", "qlib_last_date_before", "qlib_last_date_after", "sync_status", "validated_on_target_date", "backup_path", "backup_status", "error"])
        qlib_summary_path = _write_json(qlib_dir / "qlib_sync_summary.json", qlib_summary)
        qlib_stage_status = "success"
    else:
        qlib_summary, affected_symbols_path, qlib_summary_path, qlib_symbol_sync_path = run_targeted_qlib_sync(
            adapter=adapter,
            previous_qlib_last_date=previous_qlib_last_date,
            affected_symbols=selected_symbols if qlib_only else affected_symbols,
            apply=apply,
            output_dir=qlib_dir,
            skip_sync=skip_qlib_sync,
            base_dir=base_dir,
            target_date=resolved_trade_date,
        )
        qlib_stage_status = "success" if qlib_summary["qlib_update_status"] in {"success", "skipped", "skipped_requires_manual_rebuild"} else "failed"
    _set_stage(
        context,
        stage_name="qlib_sync",
        status=qlib_stage_status,
        started_at=qlib_started,
        message=f"Qlib sync {qlib_summary['qlib_update_status']}.",
        artifact_pointers={
            "qlib_sync_summary_path": str(qlib_summary_path),
            "affected_symbols_path": str(affected_symbols_path),
            "qlib_symbol_sync_path": str(qlib_symbol_sync_path),
        },
    )

    instrument_started = datetime.now().replace(microsecond=0).isoformat()
    post_sync_trade_date = qlib_summary.get("post_sync_qlib_last_date") or resolved_trade_date
    coverage_rows = build_instrument_coverage_rows(adapter, universe=universe, last_qlib_date=post_sync_trade_date)
    coverage_summary = summarize_universe_registry(adapter, universe=universe, trade_date=post_sync_trade_date).to_dict()
    repair_plan = build_repair_plan(universe=universe, last_qlib_date=post_sync_trade_date, coverage_rows=coverage_rows)
    repair_result = {
        "universe": universe,
        "applied": False,
        "updated_end_date_count": 0,
        "last_qlib_date": post_sync_trade_date,
        "repair_targets": repair_plan["stale_but_feature_available"],
    }
    if apply and not raw_only and not qlib_only and not skip_instrument_repair and repair_plan["stale_but_feature_available_count"] > 0:
        repair_result = apply_repair_plan(adapter, universe=universe, last_qlib_date=post_sync_trade_date, coverage_rows=coverage_rows)
    elif raw_only:
        repair_result["reason"] = "raw-only mode skips instrument repair"
    elif qlib_only:
        repair_result["reason"] = "qlib-only mode skips instrument repair"
    elif skip_instrument_repair:
        repair_result["reason"] = "instrument repair explicitly skipped"
    instrument_summary_path = _write_json(instrument_dir / "instrument_coverage_summary.json", coverage_summary)
    repair_result_path = _write_json(instrument_dir / "repair_result.json", repair_result)
    _set_stage(
        context,
        stage_name="instrument_sync",
        status="success",
        started_at=instrument_started,
        message="Instrument coverage checked and safely repaired when possible.",
        artifact_pointers={"instrument_coverage_summary_path": str(instrument_summary_path), "repair_result_path": str(repair_result_path)},
    )

    active_instruments_after = int(coverage_summary.get("active_on_trade_date", 0) or 0)
    ready_for_daily_shadow = active_instruments_after >= DEFAULT_MIN_ACTIVE_INSTRUMENTS
    overall_status = "success" if ready_for_daily_shadow else ("partial" if universe_status == "success" else "failed")
    archive_started = datetime.now().replace(microsecond=0).isoformat()
    _set_stage(context, stage_name="archive_report", status="success", started_at=archive_started, message="Presync summary archived.")
    finalize_run(
        context,
        daily_summary={
            "requested_date": date_resolution.get("requested_date"),
            "resolved_trade_date": resolved_trade_date,
            "universe": universe,
            "overall_status": overall_status,
            "universe_status": universe_status,
            "selected_symbol_count": len(selected_symbols),
            "selected_symbols": selected_symbols,
            "raw_update_status": raw_summary["status"],
            "qlib_update_status": qlib_summary["qlib_update_status"],
            "instrument_coverage_status": coverage_summary.get("coverage_status"),
            "previous_qlib_last_date": previous_qlib_last_date,
            "post_sync_qlib_last_date": qlib_summary.get("post_sync_qlib_last_date"),
            "active_instruments_after": active_instruments_after,
            "min_active_instruments": DEFAULT_MIN_ACTIVE_INSTRUMENTS,
            "ready_for_daily_shadow": ready_for_daily_shadow,
            "triggered_by": triggered_by,
            "apply": apply,
            "date_resolution": date_resolution,
            "raw_only": raw_only,
            "qlib_only": qlib_only,
            "skip_qlib_sync": skip_qlib_sync,
            "skip_instrument_repair": skip_instrument_repair,
            "resume": resume,
        },
        notes=[f"shadow_presync completed with overall_status={overall_status}."],
        fallback_summary={"used": date_resolution.get("status") != "success"},
    )
    latest_payload = load_json(context.latest_pointer_path)
    latest_payload["ready_for_daily_shadow"] = ready_for_daily_shadow
    latest_payload["overall_status"] = overall_status
    latest_payload["presync_summary_path"] = str(context.summary_path)
    atomic_write_json(context.latest_pointer_path, latest_payload)
    return {
        "run_id": context.run_id,
        "run_dir": str(context.run_dir),
        "manifest_path": str(context.manifest_path),
        "summary_path": str(context.summary_path),
        "overall_status": overall_status,
        "presync_summary": load_json(context.summary_path),
    }
