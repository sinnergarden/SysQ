from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from qsys.feature.availability import MARGIN_SOURCE, resolve_lagged_open_session

from qsys.config import cfg
from qsys.utils.json_io import write_csv, write_json

from qsys.data.collector import TushareCollector
from qsys.data.storage import StockDataStore


RAW_PLAN_COLUMNS = [
    "symbol",
    "selected_for_apply",
    "raw_last_date_before",
    "raw_last_date_after",
    "target_start_date",
    "target_end_date",
    "attempt_started_at",
    "attempt_ended_at",
    "rows_before",
    "rows_after",
    "rows_added",
    "status",
    "error",
]

MARGIN_REPAIR_FIELDS = [
    "margin_balance",
    "margin_buy_amount",
    "margin_repay_amount",
    "margin_total_balance",
    "lend_volume",
    "lend_sell_volume",
    "lend_repay_volume",
]


def _now_text() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _normalize_date(value: object) -> str | None:
    if value is None or value == "":
        return None
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        text = str(value).strip()
        if len(text) == 8 and text.isdigit():
            ts = pd.to_datetime(text, format="%Y%m%d", errors="coerce")
    if pd.isna(ts):
        return None
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def _count_rows(store: StockDataStore, symbol: str) -> tuple[int, str | None]:
    existing = store.load_daily(symbol)
    if existing is None or existing.empty or "trade_date" not in existing.columns:
        return 0, None
    return int(len(existing)), _normalize_date(existing["trade_date"].max())


def build_raw_update_plan(
    *,
    store: StockDataStore,
    symbols: list[str],
    target_date: str,
    lookback_days: int,
    selected_symbols: set[str] | None = None,
    resume_success_symbols: set[str] | None = None,
) -> list[dict[str, Any]]:
    target_ts = pd.Timestamp(target_date)
    selected_symbols = selected_symbols or set(symbols)
    resume_success_symbols = resume_success_symbols or set()
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        rows_before, raw_last_date = _count_rows(store, symbol)
        start_ts = target_ts - pd.Timedelta(days=lookback_days)
        if raw_last_date:
            start_ts = min(target_ts, max(pd.Timestamp(raw_last_date), start_ts))
        selected_for_apply = symbol in selected_symbols
        status = "planned" if selected_for_apply else "skipped"
        error = ""
        if symbol in resume_success_symbols:
            selected_for_apply = False
            status = "skipped"
            error = "resume_skip_previous_success"
        rows.append(
            {
                "symbol": symbol,
                "selected_for_apply": selected_for_apply,
                "raw_last_date_before": raw_last_date,
                "raw_last_date_after": raw_last_date,
                "target_start_date": start_ts.strftime("%Y-%m-%d"),
                "target_end_date": target_date,
                "attempt_started_at": "",
                "attempt_ended_at": "",
                "rows_before": rows_before,
                "rows_after": rows_before,
                "rows_added": 0,
                "status": status,
                "error": error,
            }
        )
    return rows


def load_success_symbols_from_plan(plan_path: Path) -> set[str]:
    if not plan_path.exists():
        return set()
    with plan_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return {str(row.get("symbol", "")).strip() for row in rows if str(row.get("status", "")).strip() == "success"}


def run_targeted_raw_update(
    *,
    symbols: list[str],
    target_date: str,
    lookback_days: int,
    apply: bool,
    output_dir: Path,
    universe: str = "csi300",
    selected_symbols: set[str] | None = None,
    resume_success_symbols: set[str] | None = None,
) -> tuple[dict[str, Any], Path, Path, list[str]]:
    store = StockDataStore()
    plan_rows = build_raw_update_plan(
        store=store,
        symbols=symbols,
        target_date=target_date,
        lookback_days=lookback_days,
        selected_symbols=selected_symbols,
        resume_success_symbols=resume_success_symbols,
    )
    affected_symbols: list[str] = []
    selected_count = sum(1 for row in plan_rows if row["selected_for_apply"])

    if not apply:
        pass
    elif selected_count == 0:
        pass
    else:
        try:
            collector = TushareCollector()
        except Exception as exc:
            error_text = str(exc)
            for row in plan_rows:
                if row["selected_for_apply"]:
                    row["status"] = "failed"
                    row["error"] = error_text
                    row["attempt_started_at"] = _now_text()
                    row["attempt_ended_at"] = row["attempt_started_at"]
        else:
            for row in plan_rows:
                if not row["selected_for_apply"]:
                    continue
                symbol = str(row["symbol"])
                row["attempt_started_at"] = _now_text()
                try:
                    collector.update_universe_history(
                        universe=[symbol],
                        start_date=str(row["target_start_date"]).replace("-", ""),
                        end_date=str(row["target_end_date"]).replace("-", ""),
                    )
                    rows_after, raw_last_date_after = _count_rows(store, symbol)
                    row["rows_after"] = rows_after
                    row["raw_last_date_after"] = raw_last_date_after
                    row["rows_added"] = max(rows_after - int(row["rows_before"]), 0)
                    row["status"] = "success" if row["rows_added"] > 0 or (raw_last_date_after and raw_last_date_after >= target_date) else "unchanged"
                    if row["status"] == "success":
                        affected_symbols.append(symbol)
                except Exception as exc:
                    row["status"] = "failed"
                    row["error"] = str(exc)
                    rows_after, raw_last_date_after = _count_rows(store, symbol)
                    row["rows_after"] = rows_after
                    row["raw_last_date_after"] = raw_last_date_after
                    row["rows_added"] = max(rows_after - int(row["rows_before"]), 0)
                finally:
                    row["attempt_ended_at"] = _now_text()

    failed_count = sum(1 for row in plan_rows if row["status"] == "failed")
    success_count = sum(1 for row in plan_rows if row["status"] == "success")
    unchanged_count = sum(1 for row in plan_rows if row["status"] == "unchanged")
    raw_on_target = sum(1 for row in plan_rows if row["raw_last_date_after"] and str(row["raw_last_date_after"]) >= target_date)
    if not apply:
        status = "skipped"
    elif selected_count == 0:
        status = "skipped"
    elif failed_count == 0:
        status = "success"
    elif failed_count == selected_count:
        status = "failed"
    else:
        status = "partial"

    summary = {
        "universe": universe,
        "target_symbol_count": len(symbols),
        "selected_symbol_count": selected_count,
        "symbols_attempted": selected_count if apply else 0,
        "symbols_updated": success_count,
        "symbols_failed": failed_count,
        "symbols_unchanged": unchanged_count,
        "symbols_with_raw_on_target": raw_on_target,
        "status": status,
    }
    plan_path = write_csv(output_dir / "raw_update_plan.csv", plan_rows, RAW_PLAN_COLUMNS)
    summary_path = write_json(output_dir / "raw_update_summary.json", summary)
    return summary, plan_path, summary_path, sorted(set(affected_symbols))


def _normalise_date_series(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, errors="coerce").dt.strftime("%Y-%m-%d")


def patch_margin_history_frame(
    existing: pd.DataFrame,
    margin_rows: pd.DataFrame,
) -> tuple[pd.DataFrame, int]:
    """Patch only margin columns onto existing canonical rows.

    The returned frame preserves every non-margin column and never creates a
    price-only date.  This is deliberately narrower than ``save_daily`` with a
    margin-only frame, which would replace the complete canonical row for the
    same trade date.
    """

    if existing is None or existing.empty or margin_rows is None or margin_rows.empty:
        return existing.copy(), 0
    if "trade_date" not in existing.columns or "trade_date" not in margin_rows.columns:
        raise ValueError("margin repair requires trade_date in both frames")

    updated = existing.copy()
    updated_dates = _normalise_date_series(updated["trade_date"])
    patch = margin_rows.copy()
    patch["_trade_date"] = _normalise_date_series(patch["trade_date"])
    patch = patch.dropna(subset=["_trade_date"]).drop_duplicates(
        subset=["_trade_date"], keep="last"
    )
    patch = patch.set_index("_trade_date")

    changed = 0
    for column in MARGIN_REPAIR_FIELDS:
        if column not in patch.columns:
            continue
        if column not in updated.columns:
            updated[column] = pd.NA
        incoming = updated_dates.map(pd.to_numeric(patch[column], errors="coerce"))
        mask = incoming.notna()
        current = pd.to_numeric(updated[column], errors="coerce")
        changed += int((mask & (current.isna() | current.ne(incoming))).sum())
        updated.loc[mask, column] = incoming.loc[mask]

    return updated, changed


def _open_dates_for_margin_repair(
    store: StockDataStore,
    *,
    start_date: str,
    end_date: str,
) -> list[str]:
    calendar = store.get_calendar()
    if calendar is None or calendar.empty or "cal_date" not in calendar.columns:
        return []
    dates = _normalise_date_series(calendar["cal_date"])
    mask = dates.between(start_date, end_date)
    if "is_open" in calendar.columns:
        mask &= pd.to_numeric(calendar["is_open"], errors="coerce").eq(1)
    return sorted(dates.loc[mask].dropna().unique().tolist())


def inspect_margin_history_coverage(
    store: StockDataStore,
    *,
    symbols: list[str],
    open_dates: list[str],
) -> dict[str, Any]:
    counts = {trade_date: 0 for trade_date in open_dates}
    exchanges = sorted({_symbol_exchange(symbol) for symbol in symbols})
    expected_by_exchange = {
        exchange: sum(_symbol_exchange(symbol) == exchange for symbol in symbols)
        for exchange in exchanges
    }
    exchange_counts = {
        trade_date: {exchange: 0 for exchange in exchanges}
        for trade_date in open_dates
    }
    for symbol in symbols:
        frame = store.load_daily(symbol)
        if (
            frame is None
            or frame.empty
            or "trade_date" not in frame.columns
            or "margin_balance" not in frame.columns
        ):
            continue
        dates = _normalise_date_series(frame["trade_date"])
        valid = pd.to_numeric(frame["margin_balance"], errors="coerce").notna()
        for trade_date in dates.loc[valid].dropna().unique().tolist():
            if trade_date in counts:
                counts[trade_date] += 1
                exchange_counts[trade_date][_symbol_exchange(symbol)] += 1
    exchange_coverage = {
        trade_date: {
            exchange: (
                exchange_counts[trade_date][exchange] / expected
                if expected
                else 1.0
            )
            for exchange, expected in expected_by_exchange.items()
        }
        for trade_date in open_dates
    }
    return {
        "date_counts": counts,
        "expected_symbols_by_exchange": expected_by_exchange,
        "date_exchange_counts": exchange_counts,
        "date_exchange_coverage": exchange_coverage,
        "minimum_active": min(counts.values()) if counts else 0,
        "latest_active": counts.get(open_dates[-1], 0) if open_dates else 0,
    }


def _symbol_exchange(symbol: str) -> str:
    text = str(symbol).strip().upper()
    if text.endswith(".SH"):
        return "SH"
    if text.endswith(".SZ"):
        return "SZ"
    return "UNKNOWN"


def _margin_gap_dates(
    coverage: dict[str, Any],
    *,
    min_active: int,
    min_exchange_coverage: float,
) -> list[str]:
    return [
        trade_date
        for trade_date, count in coverage["date_counts"].items()
        if count < min_active
        or any(
            ratio < min_exchange_coverage
            for ratio in coverage["date_exchange_coverage"][trade_date].values()
        )
    ]


def resolve_margin_availability_date(
    store: StockDataStore,
    *,
    signal_date: str,
    lag_sessions: int = 1,
) -> str:
    """Resolve the latest margin session available to a post-close run."""

    calendar = store.get_calendar()
    if calendar is None or calendar.empty or "cal_date" not in calendar.columns:
        raise ValueError("cannot resolve margin availability without trade calendar")
    if "is_open" in calendar.columns:
        calendar = calendar[pd.to_numeric(calendar["is_open"], errors="coerce").eq(1)]
    return resolve_lagged_open_session(
        signal_date,
        calendar["cal_date"].tolist(),
        lag_sessions,
    )


def run_margin_history_repair(
    *,
    symbols: list[str],
    start_date: str,
    end_date: str,
    min_active: int,
    min_exchange_coverage: float = 0.90,
    apply: bool,
    output_dir: Path,
    store: StockDataStore | None = None,
    collector: TushareCollector | None = None,
    qlib_refresh_fn: Callable[..., dict[str, Any]] | None = None,
    signal_date: str | None = None,
    availability_lag_sessions: int | None = None,
    universe: str = "csi800",
) -> dict[str, Any]:
    """Repair missing daily margin history and refresh affected Qlib symbols.

    Source data is fetched by open date for the full market, then restricted
    to the explicit universe.  Only margin columns are patched; price,
    financial and tradability columns remain byte-for-byte equivalent at the
    dataframe value level.
    """

    resolved_start = pd.Timestamp(start_date).strftime("%Y-%m-%d")
    resolved_end = pd.Timestamp(end_date).strftime("%Y-%m-%d")
    store = store or StockDataStore()
    availability: dict[str, Any] | None = None
    if (signal_date is None) != (availability_lag_sessions is None):
        raise ValueError(
            "signal_date and availability_lag_sessions must be provided together"
        )
    if signal_date is not None and availability_lag_sessions is not None:
        resolved_signal = pd.Timestamp(signal_date).strftime("%Y-%m-%d")
        expected_end = resolve_margin_availability_date(
            store,
            signal_date=resolved_signal,
            lag_sessions=availability_lag_sessions,
        )
        if resolved_end != expected_end:
            raise ValueError(
                "margin repair end_date violates availability contract: "
                f"expected={expected_end}, got={resolved_end}"
            )
        availability = {
            "signal_date": resolved_signal,
            "as_of_date": expected_end,
            "lag_sessions": availability_lag_sessions,
            "source": MARGIN_SOURCE,
        }
    open_dates = _open_dates_for_margin_repair(
        store, start_date=resolved_start, end_date=resolved_end
    )
    if not open_dates:
        raise ValueError(
            f"no open dates for margin repair: {resolved_start}..{resolved_end}"
        )

    if not 0.0 <= min_exchange_coverage <= 1.0:
        raise ValueError("min_exchange_coverage must be between 0 and 1")
    before = inspect_margin_history_coverage(
        store, symbols=symbols, open_dates=open_dates
    )
    gap_dates = _margin_gap_dates(
        before,
        min_active=min_active,
        min_exchange_coverage=min_exchange_coverage,
    )
    summary: dict[str, Any] = {
        "universe": universe,
        "start_date": resolved_start,
        "end_date": resolved_end,
        "open_date_count": len(open_dates),
        "symbol_count": len(symbols),
        "min_active": min_active,
        "min_exchange_coverage": min_exchange_coverage,
        "apply": apply,
        "before": before,
        "gap_dates_before": gap_dates,
        "source_rows": 0,
        "affected_symbol_count": 0,
        "patched_value_count": 0,
        "qlib_refresh": {"status": "skipped"},
        "status": "healthy" if not gap_dates else "planned",
        "availability": availability,
    }
    if not gap_dates or not apply:
        summary_path = write_json(output_dir / "margin_repair_summary.json", summary)
        return {**summary, "summary_path": str(summary_path)}

    collector = collector or TushareCollector()
    source = collector._fetch_by_date_range(  # noqa: SLF001 - same-package ops adapter
        "margin",
        None,
        gap_dates[0].replace("-", ""),
        gap_dates[-1].replace("-", ""),
    )
    if source is None or source.empty:
        summary["status"] = "failed"
        summary["error"] = "Tushare margin source returned no rows"
        summary_path = write_json(output_dir / "margin_repair_summary.json", summary)
        return {**summary, "summary_path": str(summary_path)}

    source = source.copy()
    rename_map = collector._get_interface_rename("margin")  # noqa: SLF001
    if rename_map:
        source = source.rename(columns=rename_map)
    source["trade_date"] = _normalise_date_series(source["trade_date"])
    source = source[
        source["ts_code"].astype(str).isin(set(symbols))
        & source["trade_date"].isin(set(gap_dates))
    ].copy()
    summary["source_rows"] = int(len(source))

    affected_symbols: list[str] = []
    patched_values = 0
    for symbol, rows in source.groupby("ts_code", sort=True):
        existing = store.load_daily(str(symbol))
        if existing is None or existing.empty:
            continue
        updated, changed = patch_margin_history_frame(existing, rows)
        if changed == 0:
            continue
        repaired_dates = _normalise_date_series(rows["trade_date"]).dropna().unique()
        updated_dates = _normalise_date_series(updated["trade_date"])
        repaired_rows = updated.loc[updated_dates.isin(repaired_dates)].copy()
        store.save_daily(repaired_rows, str(symbol), existing_df=existing)
        affected_symbols.append(str(symbol))
        patched_values += changed

    summary["affected_symbol_count"] = len(affected_symbols)
    summary["patched_value_count"] = patched_values
    if affected_symbols:
        if qlib_refresh_fn is None:
            from qsys.ops.qlib_sync import refresh_selected_symbols_from_raw

            qlib_refresh_fn = refresh_selected_symbols_from_raw
        refresh_result = qlib_refresh_fn(
            Path(cfg.project_root),
            sorted(affected_symbols),
            universe=universe,
            target_date=resolved_end,
            apply=True,
            output_dir=output_dir / "qlib_refresh",
        )
        summary["qlib_refresh"] = refresh_result.get("summary", refresh_result)

    after = inspect_margin_history_coverage(
        store, symbols=symbols, open_dates=open_dates
    )
    gap_dates_after = _margin_gap_dates(
        after,
        min_active=min_active,
        min_exchange_coverage=min_exchange_coverage,
    )
    summary["after"] = after
    summary["gap_dates_after"] = gap_dates_after
    qlib_status = str(summary["qlib_refresh"].get("qlib_update_status", "skipped"))
    summary["status"] = (
        "success"
        if not gap_dates_after and qlib_status == "success"
        else "failed"
    )
    summary_path = write_json(output_dir / "margin_repair_summary.json", summary)
    return {**summary, "summary_path": str(summary_path)}
