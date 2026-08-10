"""Catch up feature lookback history for newly added live-universe members."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


def inspect_universe_history(
    *,
    project_root: Path,
    symbols: Iterable[str],
    as_of_date: str,
    lookback_calendar_days: int,
) -> dict[str, Any]:
    """Find members whose canonical price history starts after required lookback."""

    root = Path(project_root)
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
    canonical = root / "data" / "canonical" / "daily"
    calendar_path = root / "data" / "qlib_bin" / "calendars" / "day.txt"
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
        is_deficient = (
            first_date is None
            or first_date > expected_start + pd.Timedelta(days=10)
            or (session_coverage is not None and session_coverage < 0.95)
        )
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
        "details": details,
    }


def run_universe_history_catchup(
    *,
    project_root: Path,
    symbols: Iterable[str],
    as_of_date: str,
    lookback_calendar_days: int,
    output_dir: Path,
    apply: bool,
    collector: Any | None = None,
    adapter: Any | None = None,
) -> dict[str, Any]:
    """Backfill only deficient current members, rebuild Qlib, and verify."""

    root = Path(project_root)
    before = inspect_universe_history(
        project_root=root,
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
        canonical = root / "data" / "canonical" / "daily"
        for symbol in before["deficient_symbols"]:
            source = canonical / f"{symbol}.feather"
            if source.is_file():
                shutil.copy2(source, backup_dir / source.name)
        if collector is None:
            from qsys.data.collector import TushareCollector

            collector = TushareCollector()
        collector.update_universe_history(
            universe=before["deficient_symbols"],
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
                qlib_dir=root / "data" / "qlib_bin",
                raw_dir=canonical,
            )
        result["qlib_rebuild"] = adapter.convert_fix_symbols(
            before["deficient_symbols"]
        )
        result["backup_dir"] = str(backup_dir)

    after = inspect_universe_history(
        project_root=root,
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
