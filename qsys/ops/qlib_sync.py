from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from qsys.utils.json_io import write_csv, write_json

import pandas as pd
from qlib.utils import code_to_fname

from qsys.config import cfg
from qsys.data.adapter import QlibAdapter


AFFECTED_SYMBOL_COLUMNS = ["symbol", "selected_for_apply"]
QLIB_SYMBOL_SYNC_COLUMNS = [
    "symbol",
    "original_feature_path",
    "raw_history_start",
    "raw_last_date",
    "raw_row_count",
    "qlib_history_start_before",
    "qlib_last_date_before",
    "qlib_row_count_before",
    "qlib_history_start_after",
    "qlib_last_date_after",
    "qlib_row_count_after",
    "sync_status",
    "validated_on_target_date",
    "backup_path",
    "backup_status",
    "error",
]

QLIB_VALIDATION_FIELDS = ["$open", "$high", "$low", "$close", "$volume", "$amount"]


def can_run_incremental_qlib_sync(adapter: QlibAdapter, target_date: str | None = None) -> bool:
    """Check if incremental qlib sync (dump_update) can be used.

    dump_update appends new dates; dump_fix rewrites per-symbol bins.
    Use incremental when target_date is NEWER than last qlib date
    (appending new data). For existing dates, use dump_fix instead.

    Conditions:
    1. The adapter has a callable convert_incremental method
    2. target_date > last_qlib_date (new date to append)
    """
    convert_fn = getattr(adapter, "convert_incremental", None)
    if not callable(convert_fn):
        return False

    if target_date is None:
        return False

    last_date = adapter.get_last_qlib_date()
    if last_date is None:
        return False

    # Incremental works when target_date is NEWER than last qlib date
    # (appending new data). If target_date <= last_date, we're fixing
    # existing data and should use dump_fix instead.
    return pd.Timestamp(target_date) > last_date


def _feature_dir_name(symbol: str) -> str:
    return code_to_fname(str(symbol).strip().lower()).lower()


def _raw_history_stats(adapter: QlibAdapter, symbol: str, *, until_date: str | None = None) -> dict[str, Any]:
    raw_path = adapter.raw_dir / f"{symbol}.feather"
    stats = {"history_start": None, "history_end": None, "row_count": 0}
    if not raw_path.exists():
        return stats
    df = pd.read_feather(raw_path, columns=["trade_date"])
    if df.empty:
        return stats
    dates = pd.to_datetime(df["trade_date"], errors="coerce").dropna()
    if until_date is not None:
        dates = dates[dates <= pd.Timestamp(until_date)]
    if dates.empty:
        return stats
    stats["history_start"] = dates.min().strftime("%Y-%m-%d")
    stats["history_end"] = dates.max().strftime("%Y-%m-%d")
    stats["row_count"] = int(dates.nunique())
    return stats


def _collect_qlib_history_stats(
    adapter: QlibAdapter,
    symbols: list[str],
    *,
    start_date: str | None,
    end_date: str | None,
) -> dict[str, dict[str, Any]]:
    stats = {symbol: {"history_start": None, "history_end": None, "row_count": 0} for symbol in symbols}
    if not symbols or start_date is None or end_date is None:
        return stats
    if pd.Timestamp(start_date) > pd.Timestamp(end_date):
        return stats

    frame = adapter.get_features(symbols, ["$close"], start_time=start_date, end_time=end_date)
    if frame is None or frame.empty or not isinstance(frame.index, pd.MultiIndex):
        return stats

    work = frame.reset_index()
    if "$close" not in work.columns:
        return stats
    work["datetime"] = pd.to_datetime(work["datetime"], errors="coerce")
    work = work.dropna(subset=["datetime", "$close"])
    if work.empty:
        return stats

    grouped = work.groupby("instrument")["datetime"].agg(["min", "max", "nunique"])
    for instrument, row in grouped.iterrows():
        symbol = str(instrument)
        stats[symbol] = {
            "history_start": row["min"].strftime("%Y-%m-%d"),
            "history_end": row["max"].strftime("%Y-%m-%d"),
            "row_count": int(row["nunique"]),
        }
    return stats


def _instrument_last_date(adapter: QlibAdapter, symbol: str, instrument_file: str = "all") -> str | None:
    path = adapter.qlib_dir / "instruments" / f"{instrument_file}.txt"
    if not path.exists():
        return None
    df = pd.read_csv(path, sep="\t", header=None, names=["instrument", "start_date", "end_date"])
    matched = df[df["instrument"].astype(str).str.upper() == symbol.upper()]
    if matched.empty:
        return None
    return pd.to_datetime(matched["end_date"], errors="coerce").max().strftime("%Y-%m-%d")


def _instrument_registry_names(universe: str) -> list[str]:
    names = ["all"]
    normalized = str(universe or "all").strip().lower()
    if normalized and normalized != "all":
        names.append(normalized)
    return names


def _required_instrument_paths(adapter: QlibAdapter, universe: str) -> list[Path]:
    return [adapter.qlib_dir / "instruments" / f"{name}.txt" for name in _instrument_registry_names(universe)]


def _preferred_instrument_name(universe: str) -> str:
    normalized = str(universe or "all").strip().lower()
    return normalized if normalized and normalized != "all" else "all"


def _raw_trade_dates(adapter: QlibAdapter, symbol: str, *, until_date: str | None = None) -> list[str]:
    raw_path = adapter.raw_dir / f"{symbol}.feather"
    if not raw_path.exists():
        return []
    df = pd.read_feather(raw_path, columns=["trade_date"])
    if df.empty:
        return []
    dates = pd.to_datetime(df["trade_date"], errors="coerce").dropna()
    if until_date is not None:
        dates = dates[dates <= pd.Timestamp(until_date)]
    if dates.empty:
        return []
    return [ts.strftime("%Y-%m-%d") for ts in dates.drop_duplicates().sort_values().tolist()]


def _build_validation_probe_dates(raw_trade_dates: list[str], target_date: str, *, max_probe_count: int = 4) -> list[str]:
    if not raw_trade_dates:
        return []
    ordered = sorted({str(date) for date in raw_trade_dates if str(date) <= target_date})
    if not ordered:
        return []
    candidate_indexes = {0, len(ordered) - 1}
    if len(ordered) > 2:
        candidate_indexes.add(len(ordered) // 2)
    if len(ordered) > 3 and max_probe_count >= 4:
        candidate_indexes.update({len(ordered) // 3, (2 * len(ordered)) // 3})
    probe_dates = [ordered[idx] for idx in sorted(candidate_indexes) if 0 <= idx < len(ordered)]
    deduped: list[str] = []
    for probe_date in probe_dates:
        if probe_date not in deduped:
            deduped.append(probe_date)
    if len(deduped) > max_probe_count:
        deduped = deduped[: max_probe_count - 1] + [deduped[-1]]
    return deduped


def _validate_symbol_probe_dates(adapter: QlibAdapter, symbol: str, probe_dates: list[str]) -> list[str]:
    if not probe_dates:
        return []
    start_date = min(probe_dates)
    end_date = max(probe_dates)
    frame = adapter.get_features([symbol], QLIB_VALIDATION_FIELDS, start_time=start_date, end_time=end_date)
    if frame is None or frame.empty or not isinstance(frame.index, pd.MultiIndex):
        return list(probe_dates)
    work = frame.reset_index()
    if "instrument" not in work.columns or "datetime" not in work.columns:
        return list(probe_dates)
    work["instrument"] = work["instrument"].astype(str)
    work["datetime"] = pd.to_datetime(work["datetime"], errors="coerce")
    work = work[(work["instrument"] == symbol) & work["datetime"].notna()].copy()
    if work.empty:
        return list(probe_dates)
    work["probe_date"] = work["datetime"].dt.strftime("%Y-%m-%d")

    failed_dates: list[str] = []
    for probe_date in probe_dates:
        rows = work[work["probe_date"] == probe_date]
        if rows.empty:
            failed_dates.append(probe_date)
            continue
        probe_ok = False
        for _, candidate in rows.iterrows():
            missing = [field for field in QLIB_VALIDATION_FIELDS if field not in candidate.index or pd.isna(candidate[field])]
            if not missing:
                probe_ok = True
                break
        if not probe_ok:
            failed_dates.append(probe_date)
    return failed_dates


def _symbol_has_target_feature(adapter: QlibAdapter, symbol: str, target_date: str) -> bool:
    frame = adapter.get_features([symbol], QLIB_VALIDATION_FIELDS, start_time=target_date, end_time=target_date)
    if frame is None or frame.empty:
        return False
    if not isinstance(frame.index, pd.MultiIndex):
        return False
    work = frame.reset_index()
    if "instrument" not in work.columns or "datetime" not in work.columns:
        return False
    work["instrument"] = work["instrument"].astype(str)
    work["datetime"] = pd.to_datetime(work["datetime"], errors="coerce")
    rows = work[(work["instrument"] == symbol) & (work["datetime"].dt.strftime("%Y-%m-%d") == target_date)]
    if rows.empty:
        return False
    for _, candidate in rows.iterrows():
        missing = [field for field in QLIB_VALIDATION_FIELDS if field not in candidate.index or pd.isna(candidate[field])]
        if not missing:
            return True
    return False


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
    # Rebuild the full selected-symbol history up to target_date so replacing a
    # feature directory does not collapse prior dates to a one-day snapshot.
    return adapter._prepare_csvs(until_date=pd.Timestamp(target_date), selected_symbols=symbols, output_dir=output_dir)


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
    universe: str = "csi300",
    target_date: str,
    apply: bool,
    output_dir: Path,
    backup: bool = True,
) -> dict[str, Any]:
    base_dir = Path(base_dir)
    adapter = QlibAdapter()
    adapter.init_qlib()
    selected = sorted(set(symbols))
    preferred_instrument = _preferred_instrument_name(universe)
    required_instrument_paths = _required_instrument_paths(adapter, universe)

    raw_stats = {symbol: _raw_history_stats(adapter, symbol, until_date=target_date) for symbol in selected}
    probe_dates_by_symbol = {
        symbol: _build_validation_probe_dates(_raw_trade_dates(adapter, symbol, until_date=target_date), target_date)
        for symbol in selected
    }
    validation_starts = [stats["history_start"] for stats in raw_stats.values() if stats["history_start"]]
    validation_start_date = min(validation_starts) if validation_starts else target_date
    qlib_before_stats = _collect_qlib_history_stats(
        adapter,
        selected,
        start_date=validation_start_date,
        end_date=target_date,
    )

    rows = []
    for symbol in selected:
        raw_stat = raw_stats[symbol]
        before_stat = qlib_before_stats.get(symbol, {})
        rows.append(
            {
                "symbol": symbol,
                "original_feature_path": str(adapter.qlib_dir / "features" / _feature_dir_name(symbol)),
                "raw_history_start": raw_stat.get("history_start"),
                "raw_last_date": raw_stat.get("history_end"),
                "raw_row_count": raw_stat.get("row_count", 0),
                "qlib_history_start_before": before_stat.get("history_start"),
                "qlib_last_date_before": before_stat.get("history_end") or _instrument_last_date(adapter, symbol, preferred_instrument),
                "qlib_row_count_before": before_stat.get("row_count", 0),
                "qlib_history_start_after": before_stat.get("history_start"),
                "qlib_last_date_after": before_stat.get("history_end") or _instrument_last_date(adapter, symbol, preferred_instrument),
                "qlib_row_count_after": before_stat.get("row_count", 0),
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
            "history_validation_start_date": validation_start_date,
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
            "history_validation_start_date": validation_start_date,
            "backup_status": backup_status,
            "rollback_status": rollback_status,
            "previous_qlib_last_date": previous_qlib_last_date_text,
            "post_sync_qlib_last_date": previous_qlib_last_date_text,
            "reason": "selected refresh cannot extend global qlib calendar safely",
        }
        return {"summary": summary, "rows": rows}

    missing_instrument_paths = [path for path in required_instrument_paths if not path.exists()]
    if missing_instrument_paths:
        error_text = "missing_instrument_registry:" + ",".join(path.name for path in missing_instrument_paths)
        for row in rows:
            row["sync_status"] = "skipped_requires_manual_rebuild"
            row["error"] = error_text
        summary = {
            "qlib_update_status": "skipped_requires_manual_rebuild",
            "convert_mode": "selected_symbol_refresh",
            "affected_symbol_count": len(selected),
            "symbols_attempted": len(selected),
            "symbols_synced": 0,
            "symbols_failed": 0,
            "symbols_validated": 0,
            "history_validation_start_date": validation_start_date,
            "backup_status": backup_status,
            "rollback_status": rollback_status,
            "previous_qlib_last_date": previous_qlib_last_date_text,
            "post_sync_qlib_last_date": previous_qlib_last_date_text,
            "reason": error_text,
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
            "history_validation_start_date": validation_start_date,
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
    for instrument_path in required_instrument_paths:
        shutil.copy2(instrument_path, temp_qlib_dir / "instruments" / instrument_path.name)
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
            "history_validation_start_date": validation_start_date,
            "backup_status": backup_status,
            "rollback_status": rollback_status,
            "previous_qlib_last_date": previous_qlib_last_date_text,
            "post_sync_qlib_last_date": previous_qlib_last_date_text,
            "reason": str(exc),
        }
        return {"summary": summary, "rows": rows}

    feature_backups: dict[str, Path] = {}
    feature_existed_before: dict[str, bool] = {}
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
                feature_existed_before[symbol] = real_dir.exists()
                row["backup_path"] = str(backup_dir)
                if real_dir.exists():
                    _copytree_replace(real_dir, backup_dir)
                    row["backup_status"] = "success"
                else:
                    row["backup_status"] = "skipped_missing_source"
                feature_backups[symbol] = backup_dir
            for instrument_path in required_instrument_paths:
                dst = backups_dir / instrument_path.name
                shutil.copy2(instrument_path, dst)
                instrument_backup_paths[instrument_path.name] = dst
            backup_status = "success"

        for row in rows:
            symbol = row["symbol"]
            folder = _feature_dir_name(symbol)
            temp_dir = temp_qlib_dir / "features" / folder
            real_dir = adapter.qlib_dir / "features" / folder
            feature_existed_before.setdefault(symbol, real_dir.exists())
            if not temp_dir.exists():
                raise RuntimeError(f"temp feature dir missing for {symbol}")
            _copytree_replace(temp_dir, real_dir)
            row["sync_status"] = "success"
            symbols_synced += 1
        for instrument_path in required_instrument_paths:
            _update_instrument_file(instrument_path, selected, target_date)
        adapter.touch_qlib_mtime()

        qlib_after_stats = _collect_qlib_history_stats(
            adapter,
            selected,
            start_date=validation_start_date,
            end_date=target_date,
        )
        for row in rows:
            symbol = row["symbol"]
            raw_stat = raw_stats[symbol]
            after_stat = qlib_after_stats.get(symbol, {})
            row["qlib_history_start_after"] = after_stat.get("history_start")
            row["qlib_last_date_after"] = after_stat.get("history_end") or _instrument_last_date(adapter, symbol, preferred_instrument)
            row["qlib_row_count_after"] = after_stat.get("row_count", 0)

            history_preserved = True
            if raw_stat.get("row_count", 0) > 0:
                history_preserved = (
                    row["qlib_history_start_after"] == raw_stat.get("history_start")
                    and row["qlib_last_date_after"] == raw_stat.get("history_end")
                    and row["qlib_row_count_after"] == raw_stat.get("row_count")
                )
            target_feature_ok = _symbol_has_target_feature(adapter, symbol, target_date)
            failed_probe_dates = _validate_symbol_probe_dates(adapter, symbol, probe_dates_by_symbol.get(symbol, []))
            validated = target_feature_ok and history_preserved and not failed_probe_dates
            row["validated_on_target_date"] = validated
            if validated:
                symbols_validated += 1
            else:
                row["sync_status"] = "failed_validation"
                if not history_preserved:
                    row["error"] = "history_not_preserved_after_refresh"
                elif not target_feature_ok:
                    row["error"] = "missing_target_feature_after_refresh"
                else:
                    row["error"] = "historical_probe_validation_failed:" + ",".join(failed_probe_dates)
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
                elif not feature_existed_before.get(symbol, False) and real_dir.exists():
                    shutil.rmtree(real_dir)
            for name, backup_path in instrument_backup_paths.items():
                shutil.copy2(backup_path, adapter.qlib_dir / "instruments" / name)
            adapter.touch_qlib_mtime()
            rollback_status = "success"
        finally:
            rolled_back_stats = _collect_qlib_history_stats(
                adapter,
                selected,
                start_date=validation_start_date,
                end_date=target_date,
            )
            for row in rows:
                restored_stat = rolled_back_stats.get(row["symbol"], {})
                row["qlib_history_start_after"] = restored_stat.get("history_start")
                row["qlib_last_date_after"] = restored_stat.get("history_end") or _instrument_last_date(adapter, row["symbol"], preferred_instrument)
                row["qlib_row_count_after"] = restored_stat.get("row_count", 0)
                if not row["error"]:
                    row["error"] = str(exc)
                row["sync_status"] = "failed"
        summary = {
            "qlib_update_status": "failed",
            "convert_mode": "selected_symbol_refresh",
            "affected_symbol_count": len(selected),
            "symbols_attempted": len(selected),
            "symbols_synced": 0,
            "symbols_failed": len(selected),
            "symbols_validated": 0,
            "history_validation_start_date": validation_start_date,
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
        "history_validation_start_date": validation_start_date,
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
    universe: str = "csi300",
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
    elif can_run_incremental_qlib_sync(adapter, target_date=target_date):
        try:
            adapter.convert_incremental(target_date)
            status = "success"
            convert_mode = "incremental"
            reason = f"incremental qlib sync via dump_update (target={target_date})"
            post_sync = str(pd.Timestamp(target_date).strftime("%Y-%m-%d"))
        except Exception as exc:
            status = "failed"
            convert_mode = "incremental"
            reason = f"incremental qlib sync failed, falling back: {exc}"
            # Fallback to the full fix path
            refresh_result = refresh_selected_symbols_from_raw(
                base_dir,
                unique_symbols,
                universe=universe,
                target_date=target_date,
                apply=apply,
                output_dir=output_dir,
            )
            summary = refresh_result["summary"]
            sync_rows = refresh_result["rows"]
            summary["reason"] = f"incremental failed, used fallback; {reason}"
            affected_path = write_csv(output_dir / "affected_symbols.csv", rows, AFFECTED_SYMBOL_COLUMNS)
            symbol_sync_path = write_csv(output_dir / "qlib_symbol_sync.csv", sync_rows, QLIB_SYMBOL_SYNC_COLUMNS)
            summary_path = write_json(output_dir / "qlib_sync_summary.json", summary)
            return summary, affected_path, summary_path, symbol_sync_path
        else:
            summary = {
                "previous_qlib_last_date": previous,
                "post_sync_qlib_last_date": post_sync,
                "affected_symbol_count": len(rows),
                "symbols_attempted": len(unique_symbols),
                "symbols_synced": len(unique_symbols),
                "symbols_failed": 0,
                "symbols_validated": 0,
                "backup_status": "not_needed",
                "rollback_status": "not_needed",
                "qlib_update_status": status,
                "convert_mode": convert_mode,
                "reason": reason,
            }
            sync_rows = []
    else:
        refresh_result = refresh_selected_symbols_from_raw(
            base_dir,
            unique_symbols,
            universe=universe,
            target_date=target_date,
            apply=apply,
            output_dir=output_dir,
        )
        summary = refresh_result["summary"]
        sync_rows = refresh_result["rows"]
    affected_path = write_csv(output_dir / "affected_symbols.csv", rows, AFFECTED_SYMBOL_COLUMNS)
    symbol_sync_path = write_csv(output_dir / "qlib_symbol_sync.csv", sync_rows, QLIB_SYMBOL_SYNC_COLUMNS)
    summary_path = write_json(output_dir / "qlib_sync_summary.json", summary)
    return summary, affected_path, summary_path, symbol_sync_path
