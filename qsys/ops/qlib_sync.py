from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from qlib.utils import code_to_fname

from qsys.config import cfg
from qsys.data.adapter import QlibAdapter


AFFECTED_SYMBOL_COLUMNS = ["symbol", "selected_for_apply"]
QLIB_SYMBOL_SYNC_COLUMNS = [
    "symbol",
    "original_feature_path",
    "raw_last_date",
    "qlib_last_date_before",
    "qlib_last_date_after",
    "sync_status",
    "validated_on_target_date",
    "backup_path",
    "backup_status",
    "error",
]


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


def can_run_incremental_qlib_sync(adapter: QlibAdapter) -> bool:
    convert_fn = getattr(adapter, "convert_incremental", None)
    if not callable(convert_fn):
        return False
    return False


def _feature_dir_name(symbol: str) -> str:
    return code_to_fname(str(symbol).strip().lower()).lower()


def _raw_last_date(adapter: QlibAdapter, symbol: str) -> str | None:
    raw_path = adapter.raw_dir / f"{symbol}.feather"
    if not raw_path.exists():
        return None
    df = pd.read_feather(raw_path, columns=["trade_date"])
    if df.empty:
        return None
    return pd.to_datetime(df["trade_date"], errors="coerce").max().strftime("%Y-%m-%d")


def _symbol_has_target_feature(adapter: QlibAdapter, symbol: str, target_date: str) -> bool:
    frame = adapter.get_features([symbol], ["$close", "$open", "$volume"], start_time=target_date, end_time=target_date)
    if frame is None or frame.empty:
        return False
    if not isinstance(frame.index, pd.MultiIndex):
        return False
    instruments = set(frame.index.get_level_values("instrument").astype(str).tolist())
    return symbol in instruments


def _instrument_last_date(adapter: QlibAdapter, symbol: str, instrument_file: str = "all") -> str | None:
    path = adapter.qlib_dir / "instruments" / f"{instrument_file}.txt"
    if not path.exists():
        return None
    df = pd.read_csv(path, sep="\t", header=None, names=["instrument", "start_date", "end_date"])
    matched = df[df["instrument"].astype(str).str.upper() == symbol.upper()]
    if matched.empty:
        return None
    return pd.to_datetime(matched["end_date"], errors="coerce").max().strftime("%Y-%m-%d")


def _update_instrument_file(path: Path, symbols: list[str], target_date: str) -> None:
    if not path.exists():
        return
    df = pd.read_csv(path, sep="\t", header=None, names=["instrument", "start_date", "end_date"])
    mask = df["instrument"].astype(str).str.upper().isin({symbol.upper() for symbol in symbols})
    df.loc[mask, "end_date"] = target_date
    df.to_csv(path, sep="\t", header=False, index=False)


def _copytree_replace(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def _prepare_selected_csvs(adapter: QlibAdapter, *, symbols: list[str], target_date: str, output_dir: Path) -> tuple[Path, int]:
    return adapter._prepare_csvs(pd.Timestamp(target_date), selected_symbols=symbols, output_dir=output_dir)


def _run_dump_fix(*, csv_dir: Path, temp_qlib_dir: Path) -> None:
    dump_script = cfg.project_root / "scripts" / "dump_bin.py"
    adapter_cfg = cfg.get_tushare_feature_config().get("adapter", {})
    qlib_fields = adapter_cfg.get("qlib_fields", [])
    include_fields = [f for f in qlib_fields if f != "date"]
    cmd = [
        sys.executable,
        str(dump_script),
        "dump_fix",
        "--data_path",
        str(csv_dir),
        "--qlib_dir",
        str(temp_qlib_dir),
        "--include_fields",
        ",".join(include_fields),
        "--symbol_field_name",
        "symbol",
        "--date_field_name",
        "date",
    ]
    subprocess.run(cmd, check=True)


def refresh_selected_symbols_from_raw(
    base_dir: str | Path,
    symbols: list[str],
    *,
    target_date: str,
    apply: bool,
    output_dir: Path,
    backup: bool = True,
) -> dict[str, Any]:
    base_dir = Path(base_dir)
    adapter = QlibAdapter()
    adapter.init_qlib()
    selected = sorted(set(symbols))
    rows = []
    for symbol in selected:
        rows.append(
            {
                "symbol": symbol,
                "original_feature_path": str(adapter.qlib_dir / "features" / _feature_dir_name(symbol)),
                "raw_last_date": _raw_last_date(adapter, symbol),
                "qlib_last_date_before": _instrument_last_date(adapter, symbol, "all"),
                "qlib_last_date_after": _instrument_last_date(adapter, symbol, "all"),
                "sync_status": "planned" if apply else "skipped",
                "validated_on_target_date": False,
                "backup_path": "",
                "backup_status": "skipped",
                "error": "",
            }
        )

    previous_qlib_last_date = adapter.get_last_qlib_date()
    previous_qlib_last_date_text = previous_qlib_last_date.strftime("%Y-%m-%d") if previous_qlib_last_date is not None else None
    backups_dir = output_dir / "backups"
    tmp_build_dir = output_dir / "tmp_build"
    tmp_csv_dir = tmp_build_dir / "csv"
    temp_qlib_dir = tmp_build_dir / "qlib_temp"
    backup_status = "skipped"
    rollback_status = "not_needed"
    symbols_synced = 0
    symbols_failed = 0
    symbols_validated = 0

    if not apply:
        summary = {
            "qlib_update_status": "skipped",
            "convert_mode": "selected_symbol_refresh",
            "affected_symbol_count": len(selected),
            "symbols_attempted": 0,
            "symbols_synced": 0,
            "symbols_failed": 0,
            "symbols_validated": 0,
            "backup_status": backup_status,
            "rollback_status": rollback_status,
            "previous_qlib_last_date": previous_qlib_last_date_text,
            "post_sync_qlib_last_date": previous_qlib_last_date_text,
            "reason": "dry-run does not mutate qlib",
        }
        return {"summary": summary, "rows": rows}

    if previous_qlib_last_date_text is None or pd.Timestamp(target_date) > pd.Timestamp(previous_qlib_last_date_text):
        for row in rows:
            row["sync_status"] = "skipped_requires_manual_rebuild"
            row["error"] = "target_date_exceeds_current_qlib_calendar"
        summary = {
            "qlib_update_status": "skipped_requires_manual_rebuild",
            "convert_mode": "selected_symbol_refresh",
            "affected_symbol_count": len(selected),
            "symbols_attempted": len(selected),
            "symbols_synced": 0,
            "symbols_failed": 0,
            "symbols_validated": 0,
            "backup_status": backup_status,
            "rollback_status": rollback_status,
            "previous_qlib_last_date": previous_qlib_last_date_text,
            "post_sync_qlib_last_date": previous_qlib_last_date_text,
            "reason": "selected refresh cannot extend global qlib calendar safely",
        }
        return {"summary": summary, "rows": rows}

    csv_dir, converted_count = _prepare_selected_csvs(adapter, symbols=selected, target_date=target_date, output_dir=tmp_csv_dir)
    if converted_count != len(selected):
        missing = {symbol for symbol in selected if not (csv_dir / f"{symbol}.csv").exists()}
        for row in rows:
            if row["symbol"] in missing:
                row["sync_status"] = "failed"
                row["error"] = "selected_csv_missing"
                symbols_failed += 1
        summary = {
            "qlib_update_status": "failed",
            "convert_mode": "selected_symbol_refresh",
            "affected_symbol_count": len(selected),
            "symbols_attempted": len(selected),
            "symbols_synced": 0,
            "symbols_failed": symbols_failed,
            "symbols_validated": 0,
            "backup_status": backup_status,
            "rollback_status": rollback_status,
            "previous_qlib_last_date": previous_qlib_last_date_text,
            "post_sync_qlib_last_date": previous_qlib_last_date_text,
            "reason": "failed to prepare selected csv payloads",
        }
        return {"summary": summary, "rows": rows}

    temp_qlib_dir.mkdir(parents=True, exist_ok=True)
    (temp_qlib_dir / "calendars").mkdir(parents=True, exist_ok=True)
    (temp_qlib_dir / "instruments").mkdir(parents=True, exist_ok=True)
    shutil.copy2(adapter.qlib_dir / "calendars" / "day.txt", temp_qlib_dir / "calendars" / "day.txt")
    shutil.copy2(adapter.qlib_dir / "instruments" / "all.txt", temp_qlib_dir / "instruments" / "all.txt")
    try:
        _run_dump_fix(csv_dir=csv_dir, temp_qlib_dir=temp_qlib_dir)
    except Exception as exc:
        for row in rows:
            row["sync_status"] = "failed"
            row["error"] = str(exc)
        summary = {
            "qlib_update_status": "failed",
            "convert_mode": "selected_symbol_refresh",
            "affected_symbol_count": len(selected),
            "symbols_attempted": len(selected),
            "symbols_synced": 0,
            "symbols_failed": len(selected),
            "symbols_validated": 0,
            "backup_status": backup_status,
            "rollback_status": rollback_status,
            "previous_qlib_last_date": previous_qlib_last_date_text,
            "post_sync_qlib_last_date": previous_qlib_last_date_text,
            "reason": str(exc),
        }
        return {"summary": summary, "rows": rows}

    feature_backups: dict[str, Path] = {}
    instrument_backup_paths: dict[str, Path] = {}
    try:
        if backup:
            backups_dir.mkdir(parents=True, exist_ok=True)
            feature_backup_root = backups_dir / "features"
            feature_backup_root.mkdir(parents=True, exist_ok=True)
            for row in rows:
                symbol = row["symbol"]
                folder = _feature_dir_name(symbol)
                real_dir = adapter.qlib_dir / "features" / folder
                backup_dir = feature_backup_root / folder
                row["backup_path"] = str(backup_dir)
                if real_dir.exists():
                    _copytree_replace(real_dir, backup_dir)
                    row["backup_status"] = "success"
                else:
                    row["backup_status"] = "skipped_missing_source"
                feature_backups[symbol] = backup_dir
            for name in ["all.txt", "csi300.txt"]:
                src = adapter.qlib_dir / "instruments" / name
                dst = backups_dir / name
                if src.exists():
                    shutil.copy2(src, dst)
                    instrument_backup_paths[name] = dst
            backup_status = "success"

        for row in rows:
            symbol = row["symbol"]
            folder = _feature_dir_name(symbol)
            temp_dir = temp_qlib_dir / "features" / folder
            real_dir = adapter.qlib_dir / "features" / folder
            if not temp_dir.exists():
                raise RuntimeError(f"temp feature dir missing for {symbol}")
            _copytree_replace(temp_dir, real_dir)
            row["sync_status"] = "success"
            symbols_synced += 1
        _update_instrument_file(adapter.qlib_dir / "instruments" / "all.txt", selected, target_date)
        _update_instrument_file(adapter.qlib_dir / "instruments" / "csi300.txt", selected, target_date)

        validate_frame = adapter.get_features("csi300", ["$close", "$open", "$volume"], start_time=target_date, end_time=target_date)
        validated_symbols = set()
        if validate_frame is not None and not validate_frame.empty and isinstance(validate_frame.index, pd.MultiIndex):
            validated_symbols = set(validate_frame.index.get_level_values("instrument").astype(str).tolist())
        for row in rows:
            symbol = row["symbol"]
            validated = symbol in validated_symbols and _symbol_has_target_feature(adapter, symbol, target_date)
            row["validated_on_target_date"] = validated
            row["qlib_last_date_after"] = _instrument_last_date(adapter, symbol, "all")
            if validated:
                symbols_validated += 1
            else:
                row["sync_status"] = "failed_validation"
                row["error"] = "missing_target_feature_after_refresh"
        if symbols_validated != len(selected):
            raise RuntimeError("selected symbol refresh validation failed")
    except Exception as exc:
        rollback_status = "failed"
        try:
            for row in rows:
                symbol = row["symbol"]
                folder = _feature_dir_name(symbol)
                real_dir = adapter.qlib_dir / "features" / folder
                backup_dir = feature_backups.get(symbol)
                if backup_dir and backup_dir.exists():
                    _copytree_replace(backup_dir, real_dir)
            for name, backup_path in instrument_backup_paths.items():
                shutil.copy2(backup_path, adapter.qlib_dir / "instruments" / name)
            rollback_status = "success"
        finally:
            for row in rows:
                if not row["error"]:
                    row["error"] = str(exc)
                row["sync_status"] = "failed"
                row["qlib_last_date_after"] = _instrument_last_date(adapter, row["symbol"], "all")
        summary = {
            "qlib_update_status": "failed",
            "convert_mode": "selected_symbol_refresh",
            "affected_symbol_count": len(selected),
            "symbols_attempted": len(selected),
            "symbols_synced": 0,
            "symbols_failed": len(selected),
            "symbols_validated": 0,
            "backup_status": backup_status,
            "rollback_status": rollback_status,
            "previous_qlib_last_date": previous_qlib_last_date_text,
            "post_sync_qlib_last_date": previous_qlib_last_date_text,
            "reason": str(exc),
        }
        return {"summary": summary, "rows": rows}

    post = adapter.get_last_qlib_date()
    post_sync_qlib_last_date = post.strftime("%Y-%m-%d") if post is not None else previous_qlib_last_date_text
    summary = {
        "qlib_update_status": "success",
        "convert_mode": "selected_symbol_refresh",
        "affected_symbol_count": len(selected),
        "symbols_attempted": len(selected),
        "symbols_synced": symbols_synced,
        "symbols_failed": symbols_failed,
        "symbols_validated": symbols_validated,
        "backup_status": backup_status,
        "rollback_status": rollback_status,
        "previous_qlib_last_date": previous_qlib_last_date_text,
        "post_sync_qlib_last_date": post_sync_qlib_last_date,
        "reason": "selected symbol refresh completed",
    }
    return {"summary": summary, "rows": rows}


def run_targeted_qlib_sync(
    *,
    adapter: QlibAdapter,
    previous_qlib_last_date: str | None,
    affected_symbols: list[str],
    apply: bool,
    output_dir: Path,
    skip_sync: bool = False,
    base_dir: str | Path | None = None,
    target_date: str | None = None,
) -> tuple[dict[str, Any], Path, Path, Path]:
    unique_symbols = sorted(set(affected_symbols))
    rows = [{"symbol": symbol, "selected_for_apply": True} for symbol in unique_symbols]
    previous = previous_qlib_last_date
    if not apply:
        status = "skipped"
        convert_mode = "skipped"
        reason = "dry-run does not mutate qlib"
        summary = {
            "previous_qlib_last_date": previous,
            "post_sync_qlib_last_date": previous_qlib_last_date,
            "affected_symbol_count": len(rows),
            "symbols_attempted": 0,
            "symbols_synced": 0,
            "symbols_failed": 0,
            "symbols_validated": 0,
            "backup_status": "skipped",
            "rollback_status": "not_needed",
            "qlib_update_status": status,
            "convert_mode": convert_mode,
            "reason": reason,
        }
        sync_rows = []
    elif skip_sync:
        summary = {
            "previous_qlib_last_date": previous,
            "post_sync_qlib_last_date": previous_qlib_last_date,
            "affected_symbol_count": len(rows),
            "symbols_attempted": 0,
            "symbols_synced": 0,
            "symbols_failed": 0,
            "symbols_validated": 0,
            "backup_status": "skipped",
            "rollback_status": "not_needed",
            "qlib_update_status": "skipped",
            "convert_mode": "skipped",
            "reason": "qlib sync explicitly skipped",
        }
        sync_rows = []
    elif not unique_symbols:
        summary = {
            "previous_qlib_last_date": previous,
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
            "reason": "no affected symbols for qlib sync",
        }
        sync_rows = []
    elif base_dir is None or target_date is None:
        summary = {
            "previous_qlib_last_date": previous,
            "post_sync_qlib_last_date": previous_qlib_last_date,
            "affected_symbol_count": len(rows),
            "symbols_attempted": len(rows),
            "symbols_synced": 0,
            "symbols_failed": 0,
            "symbols_validated": 0,
            "backup_status": "skipped",
            "rollback_status": "not_needed",
            "qlib_update_status": "skipped_requires_manual_rebuild",
            "convert_mode": "requires_manual_rebuild",
            "reason": "base_dir or target_date missing for selected symbol refresh",
        }
        sync_rows = []
    else:
        refresh_result = refresh_selected_symbols_from_raw(base_dir, unique_symbols, target_date=target_date, apply=apply, output_dir=output_dir)
        summary = refresh_result["summary"]
        sync_rows = refresh_result["rows"]
    affected_path = _write_csv(output_dir / "affected_symbols.csv", rows, AFFECTED_SYMBOL_COLUMNS)
    symbol_sync_path = _write_csv(output_dir / "qlib_symbol_sync.csv", sync_rows, QLIB_SYMBOL_SYNC_COLUMNS)
    summary_path = _write_json(output_dir / "qlib_sync_summary.json", summary)
    return summary, affected_path, summary_path, symbol_sync_path
