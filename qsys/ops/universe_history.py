"""Catch up feature lookback history for newly added live-universe members."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


def _resolve_data_root(
    *, project_root: Path | None = None, data_root: Path | None = None
) -> Path:
    if data_root is not None and project_root is not None:
        raise ValueError("pass data_root or project_root, not both")
    if data_root is not None:
        return Path(data_root)
    if project_root is not None:
        return Path(project_root) / "data"
    raise ValueError("data_root or project_root is required")


def _read_registry(path: Path) -> pd.DataFrame:
    columns = ["instrument", "start_date", "end_date"]
    if not path.is_file():
        return pd.DataFrame(columns=columns)
    frame = pd.read_csv(path, sep="\t", header=None, names=columns, dtype=str)
    frame = frame.dropna(subset=["instrument"]).drop_duplicates().copy()
    frame["instrument"] = frame["instrument"].str.strip().str.upper()
    return frame


def _atomic_write_registry(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".txt.tmp")
    os.close(descriptor)
    try:
        frame.to_csv(temporary, sep="\t", header=False, index=False)
        os.chmod(temporary, mode)
        Path(temporary).replace(path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def repair_qlib_instrument_history_spans(
    *,
    symbols: Iterable[str],
    project_root: Path | None = None,
    data_root: Path | None = None,
) -> dict[str, Any]:
    """Align Qlib registry spans to canonical data availability."""

    resolved_data_root = _resolve_data_root(
        project_root=project_root, data_root=data_root
    )
    canonical = resolved_data_root / "canonical" / "daily"
    spans: dict[str, tuple[str, str]] = {}
    for symbol in sorted({str(value).strip().upper() for value in symbols}):
        path = canonical / f"{symbol}.feather"
        if not path.is_file():
            continue
        dates = pd.to_datetime(
            pd.read_feather(path, columns=["trade_date"])["trade_date"],
            errors="coerce",
        ).dropna()
        if not dates.empty:
            spans[symbol] = (
                dates.min().strftime("%Y-%m-%d"),
                dates.max().strftime("%Y-%m-%d"),
            )

    changed: dict[str, int] = {}
    registry_dir = resolved_data_root / "qlib_bin" / "instruments"
    for name in ("all", "csi800", "csi300"):
        path = registry_dir / f"{name}.txt"
        frame = _read_registry(path)
        if frame.empty:
            continue
        changes = 0
        for symbol, (canonical_start, canonical_end) in spans.items():
            mask = frame["instrument"] == symbol
            if not mask.any():
                if name == "all":
                    frame.loc[len(frame)] = [symbol, canonical_start, canonical_end]
                    changes += 1
                continue
            current_start = frame.loc[mask, "start_date"].min()
            current_end = frame.loc[mask, "end_date"].max()
            resolved_start = min(str(current_start), canonical_start)
            resolved_end = max(str(current_end), canonical_end)
            if resolved_start != current_start or resolved_end != current_end:
                frame.loc[mask, "start_date"] = resolved_start
                frame.loc[mask, "end_date"] = resolved_end
                changes += int(mask.sum())
        if changes:
            frame = frame.sort_values(["instrument", "start_date", "end_date"])
            _atomic_write_registry(frame, path)
        changed[name] = changes
    return {"status": "success", "symbols": len(spans), "changed_rows": changed}


def inspect_universe_history(
    *,
    symbols: Iterable[str],
    as_of_date: str,
    lookback_calendar_days: int,
    project_root: Path | None = None,
    data_root: Path | None = None,
) -> dict[str, Any]:
    """Find members whose canonical price history starts after required lookback."""

    resolved_data_root = _resolve_data_root(
        project_root=project_root, data_root=data_root
    )
    required_start = pd.Timestamp(as_of_date) - pd.Timedelta(
        days=lookback_calendar_days
    )
    stock_basic: dict[str, str] = {}
    try:
        from qsys.data.storage import StockDataStore

        basic = StockDataStore().get_stock_list()
        if basic is not None and not basic.empty and {"ts_code", "list_date"}.issubset(basic):
            stock_basic = dict(
                zip(
                    basic["ts_code"].astype(str),
                    basic["list_date"].astype(str),
                    strict=False,
                )
            )
    except Exception:
        stock_basic = {}

    details: list[dict[str, Any]] = []
    deficient: list[str] = []
    canonical = resolved_data_root / "canonical" / "daily"
    all_registry = _read_registry(
        resolved_data_root / "qlib_bin" / "instruments" / "all.txt"
    )
    registry_starts = (
        all_registry.groupby("instrument")["start_date"].min().to_dict()
        if not all_registry.empty
        else {}
    )
    calendar_path = resolved_data_root / "qlib_bin" / "calendars" / "day.txt"
    if calendar_path.is_file():
        calendar = pd.to_datetime(
            [
                value.strip()
                for value in calendar_path.read_text(encoding="utf-8").splitlines()
                if value.strip()
            ],
            errors="coerce",
        )
        calendar = pd.DatetimeIndex(calendar).dropna().sort_values().unique()
    else:
        calendar = pd.DatetimeIndex([])
    for symbol in sorted({str(value).strip().upper() for value in symbols if str(value).strip()}):
        path = canonical / f"{symbol}.feather"
        first_date: pd.Timestamp | None = None
        last_date: pd.Timestamp | None = None
        row_count = 0
        dates = pd.Series(dtype="datetime64[ns]")
        if path.is_file():
            try:
                frame = pd.read_feather(path, columns=["trade_date"])
                dates = pd.to_datetime(frame["trade_date"], errors="coerce").dropna()
                row_count = len(dates)
                if not dates.empty:
                    first_date = dates.min()
                    last_date = dates.max()
            except (OSError, ValueError, KeyError):
                pass
        listed = pd.to_datetime(stock_basic.get(symbol), errors="coerce")
        expected_start = required_start
        if not pd.isna(listed):
            expected_start = max(required_start, pd.Timestamp(listed))
        expected_sessions = calendar[
            (calendar >= expected_start) & (calendar <= pd.Timestamp(as_of_date))
        ]
        if len(expected_sessions):
            observed_sessions = pd.DatetimeIndex(dates.unique()).intersection(
                expected_sessions
            )
            session_coverage = len(observed_sessions) / len(expected_sessions)
        else:
            session_coverage = None
        # Require both the lookback boundary and continuity.  The latter catches
        # missed sync windows even when one very old row makes min(date) look safe.
        canonical_deficient = (
            first_date is None
            or first_date > expected_start + pd.Timedelta(days=10)
            or (session_coverage is not None and session_coverage < 0.95)
        )
        registry_start = pd.to_datetime(
            registry_starts.get(symbol), errors="coerce"
        )
        registry_deficient = first_date is not None and (
            pd.isna(registry_start)
            or registry_start > expected_start + pd.Timedelta(days=10)
        )
        is_deficient = canonical_deficient or registry_deficient
        if is_deficient:
            deficient.append(symbol)
        details.append(
            {
                "ts_code": symbol,
                "first_date": first_date.strftime("%Y-%m-%d") if first_date is not None else None,
                "last_date": last_date.strftime("%Y-%m-%d") if last_date is not None else None,
                "expected_start": expected_start.strftime("%Y-%m-%d"),
                "row_count": row_count,
                "expected_session_count": len(expected_sessions),
                "session_coverage": (
                    round(float(session_coverage), 6)
                    if session_coverage is not None
                    else None
                ),
                "qlib_registry_start": (
                    pd.Timestamp(registry_start).strftime("%Y-%m-%d")
                    if not pd.isna(registry_start)
                    else None
                ),
                "canonical_deficient": canonical_deficient,
                "qlib_registry_deficient": registry_deficient,
                "status": "deficient" if is_deficient else "pass",
            }
        )
    return {
        "status": "pass" if not deficient else "fail",
        "as_of_date": pd.Timestamp(as_of_date).strftime("%Y-%m-%d"),
        "required_start": required_start.strftime("%Y-%m-%d"),
        "lookback_calendar_days": lookback_calendar_days,
        "symbol_count": len(details),
        "deficient_count": len(deficient),
        "deficient_symbols": deficient,
        "canonical_deficient_symbols": [
            item["ts_code"] for item in details if item["canonical_deficient"]
        ],
        "qlib_registry_deficient_symbols": [
            item["ts_code"] for item in details if item["qlib_registry_deficient"]
        ],
        "details": details,
    }


def run_universe_history_catchup(
    *,
    symbols: Iterable[str],
    as_of_date: str,
    lookback_calendar_days: int,
    output_dir: Path,
    apply: bool,
    project_root: Path | None = None,
    data_root: Path | None = None,
    collector: Any | None = None,
    adapter: Any | None = None,
) -> dict[str, Any]:
    """Backfill only deficient current members, rebuild Qlib, and verify."""

    resolved_data_root = _resolve_data_root(
        project_root=project_root, data_root=data_root
    )
    before = inspect_universe_history(
        data_root=resolved_data_root,
        symbols=symbols,
        as_of_date=as_of_date,
        lookback_calendar_days=lookback_calendar_days,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "status": "healthy" if before["status"] == "pass" else "planned",
        "apply": apply,
        "before": before,
        "backfilled_symbols": before["deficient_symbols"],
    }
    if apply and before["deficient_symbols"]:
        backup_dir = output_dir / "before"
        backup_dir.mkdir(parents=True, exist_ok=True)
        canonical = resolved_data_root / "canonical" / "daily"
        for symbol in before["canonical_deficient_symbols"]:
            source = canonical / f"{symbol}.feather"
            if source.is_file():
                shutil.copy2(source, backup_dir / source.name)
        if before["canonical_deficient_symbols"]:
            if collector is None:
                from qsys.data.collector import TushareCollector

                collector = TushareCollector()
            collector.update_universe_history(
                universe=before["canonical_deficient_symbols"],
                start_date=before["required_start"],
                end_date=as_of_date,
                incremental=False,
                batch_size=50,
                include_moneyflow=True,
                include_margin=True,
            )
        if adapter is None:
            from qsys.data.adapter import QlibAdapter

            adapter = QlibAdapter(
                qlib_dir=resolved_data_root / "qlib_bin",
                raw_dir=canonical,
            )
        result["qlib_rebuild"] = adapter.convert_fix_symbols(
            before["deficient_symbols"], refresh_universes=[]
        )
        result["registry_repair"] = repair_qlib_instrument_history_spans(
            data_root=resolved_data_root,
            symbols=before["deficient_symbols"],
        )
        result["backup_dir"] = str(backup_dir)

    after = inspect_universe_history(
        data_root=resolved_data_root,
        symbols=symbols,
        as_of_date=as_of_date,
        lookback_calendar_days=lookback_calendar_days,
    )
    result["after"] = after
    if apply:
        result["status"] = "success" if after["status"] == "pass" else "failed"
    summary_path = output_dir / "universe_history_catchup.json"
    summary_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    result["summary_path"] = str(summary_path)
    return result
