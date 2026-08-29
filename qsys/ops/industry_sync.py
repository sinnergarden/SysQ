"""Audited Tushare ``bak_basic`` industry evidence projection.

This module deliberately owns no new datastore.  Verified raw supplier shards are
projected atomically into the existing per-symbol canonical daily Feather files.
"""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from qsys.data.source_audit import SourceAuditStore, stable_scope_hash

BAK_BASIC_START = "20160101"
PIT_INDUSTRY_FEATURE_START = "20180313"
BAK_BASIC_ROW_LIMIT = 7000
BAK_BASIC_FIELDS = "trade_date,ts_code,industry"
HISTORY_REQUEST_VARIANT = "history_bak_basic_industry_v1"
DAILY_REQUEST_VARIANT = "daily_bak_basic_industry_v1"


def _dates(frame: pd.DataFrame) -> pd.Series:
    return (
        frame["trade_date"].astype("string").str.strip()
        .str.replace("-", "", regex=False).str.replace(r"\.0$", "", regex=True)
    )


def _current_taxonomy(stock_basic: pd.DataFrame) -> dict[str, str]:
    if stock_basic is None or stock_basic.empty or not {"ts_code", "industry"}.issubset(stock_basic.columns):
        raise ValueError("stock_basic taxonomy is missing ts_code/industry")
    normalized = stock_basic.loc[:, ["ts_code", "industry"]].copy()
    normalized["ts_code"] = normalized["ts_code"].astype("string").str.strip().str.upper()
    normalized["industry"] = normalized["industry"].astype("string").str.strip()
    normalized = normalized.loc[normalized["industry"].notna() & normalized["industry"].ne("")]
    names = set(normalized["industry"].tolist())
    if not names:
        raise ValueError("stock_basic taxonomy is empty")
    return dict(zip(normalized["ts_code"], normalized["industry"]))


def validate_history_industry_response(
    frame: pd.DataFrame,
    *,
    symbol: str,
    target_date: str,
    required_dates: set[str],
) -> Mapping[str, object] | None:
    """Validate full raw response while applying the target cutoff only to coverage."""

    if frame is None or frame.empty:
        return None if not required_dates else {"reason": "required_history_empty"}
    if len(frame) >= BAK_BASIC_ROW_LIMIT:
        return {"reason": "possible_supplier_truncation", "row_count": len(frame)}
    missing = sorted({"ts_code", "trade_date", "industry"} - set(frame.columns))
    if missing:
        return {"reason": "response_missing_columns", "missing_columns": missing}
    symbols = frame["ts_code"].astype("string").str.strip().str.upper()
    if symbols.isna().any() or not symbols.eq(symbol).all():
        return {"reason": "response_symbol_out_of_scope"}
    dates = _dates(frame)
    parsed = pd.to_datetime(dates, format="%Y%m%d", errors="coerce")
    if parsed.isna().any():
        return {"reason": "response_date_invalid"}
    industries = frame["industry"].astype("string").str.strip()
    if industries.isna().any() or industries.eq("").any():
        return {"reason": "response_industry_missing"}
    if pd.DataFrame({"ts_code": symbols, "trade_date": dates}).duplicated().any():
        return {"reason": "response_duplicate_key"}
    eligible_dates = sorted(
        date for date in dates.tolist()
        if BAK_BASIC_START <= date <= target_date
    )
    first_available = eligible_dates[0] if eligible_dates else None
    missing_dates = sorted(
        date for date in required_dates
        if first_available is None or date < first_available
    )
    if missing_dates:
        return {"reason": "canonical_coverage_missing", "missing_count": len(missing_dates), "sample": missing_dates[:10]}
    return None


def _canonical_required_dates(store, symbol: str, target_date: str) -> set[str]:
    daily = store.load_daily(symbol)
    if daily is None or daily.empty or "trade_date" not in daily.columns:
        return set()
    dates = _dates(daily)
    return set(dates.loc[(dates >= PIT_INDUSTRY_FEATURE_START) & (dates <= target_date)].tolist())


def _project_history_industry(
    frame: pd.DataFrame,
    *,
    symbol: str,
    target_date: str,
    required_dates: set[str],
) -> pd.DataFrame:
    """Project the latest known industry state without using future rows."""

    ordered_required = sorted(required_dates)
    if not ordered_required:
        return pd.DataFrame(columns=["ts_code", "trade_date", "industry"])
    dates = _dates(frame)
    eligible = frame.loc[
        (dates >= BAK_BASIC_START) & (dates <= target_date),
        ["trade_date", "industry"],
    ].copy()
    eligible["trade_date"] = dates.loc[eligible.index]
    states = eligible.sort_values("trade_date").set_index("trade_date")["industry"]
    timeline = states.reindex(
        states.index.union(pd.Index(ordered_required))
    ).sort_index().ffill()
    projected = timeline.reindex(ordered_required)
    if projected.isna().any():
        raise ValueError("industry history has no state available at required date")
    return pd.DataFrame({
        "ts_code": symbol,
        "trade_date": ordered_required,
        "industry": projected.astype("string").tolist(),
    })


def fetch_audited_history_industry(
    collector,
    codes: list[str],
    target_date: str,
    *,
    is_history_repair: bool,
    run_id: str,
    audit_store: SourceAuditStore,
    resume_proof: Mapping[str, object] | None,
    scope_key: str,
    universe: str,
    recent_overlap_days: int = 45,
    min_taxonomy_match_ratio: float = 0.95,
) -> tuple[dict, list[str]]:
    """Fetch, receipt, validate, and atomically project one shard per symbol."""

    if universe != "csi1800" or not is_history_repair:
        return {"status": "not_required"}, []
    target = str(target_date).replace("-", "")
    symbols = sorted({str(code).strip().upper() for code in codes if str(code).strip()})
    if scope_key != "csi1800" or not symbols or len(target) != 8 or not target.isdigit():
        raise ValueError("history industry evidence requires a bounded CSI1800 scope")
    current_by_symbol = _current_taxonomy(collector.store.get_stock_list())
    if not 0.0 <= float(min_taxonomy_match_ratio) <= 1.0:
        raise ValueError("min_taxonomy_match_ratio must be between zero and one")
    recent_cutoff = (
        pd.Timestamp(target) - pd.Timedelta(days=recent_overlap_days)
    ).strftime("%Y%m%d")
    expected_taxonomy_comparisons = 0
    for symbol in symbols:
        dates = _canonical_required_dates(collector.store, symbol, target)
        if current_by_symbol.get(symbol) and dates and max(dates) >= recent_cutoff:
            expected_taxonomy_comparisons += 1
    allowed_taxonomy_mismatches = int(
        (1.0 - float(min_taxonomy_match_ratio)) * expected_taxonomy_comparisons
    )
    receipts: list[str] = []
    mutations = 0
    success = 0
    empty = 0
    future_rows = 0
    before_rows = 0
    taxonomy_comparisons = 0
    taxonomy_mismatches = 0
    failure: dict | None = None
    for symbol in symbols:
        required_dates = _canonical_required_dates(collector.store, symbol, target)
        scope = {
            "date_start": BAK_BASIC_START,
            "date_end": target,
            "query_axis": "all_history",
            "availability_cutoff": target,
            "symbol_count": 1,
            "symbols": [symbol],
            "symbols_sha256": stable_scope_hash([symbol]),
        }
        validator = lambda response, expected=symbol, required=required_dates: validate_history_industry_response(
            response,
            symbol=expected,
            target_date=target,
            required_dates=required,
        )
        try:
            frame, receipt_id = collector._fetch_daily_endpoint_with_receipt(
                "bak_basic", run_id=run_id, audit_store=audit_store,
                requested_scope=scope, resume_proof=resume_proof,
                scope_key=scope_key, universe=universe,
                request_variant=HISTORY_REQUEST_VARIANT,
                identity_columns=("ts_code", "trade_date"),
                evidence_fields=("industry",), response_validator=validator,
                required_endpoint=True, supplier_call_delay_seconds=0.35,
                ts_code=symbol, fields=BAK_BASIC_FIELDS,
            )
        except Exception as exc:
            failure = {"symbol": symbol, "reason": "supplier_failure", "error": str(exc)}
            break
        if not receipt_id:
            failure = {"symbol": symbol, "reason": "receipt_missing"}
            break
        receipts.append(str(receipt_id))
        check = audit_store.verify_fetch_receipt(run_id=run_id, receipt_id=str(receipt_id))
        if check["status"] != "success" or validator(frame) is not None:
            failure = {"symbol": symbol, "reason": str(check.get("reason") or "semantic_partial")}
            break
        if frame.empty:
            empty += 1
            continue
        dates = _dates(frame)
        future_rows += int((dates > target).sum())
        before_rows += int((dates < BAK_BASIC_START).sum())
        if current_by_symbol.get(symbol) and required_dates and max(required_dates) >= recent_cutoff:
            eligible = frame.loc[dates <= target]
            latest_date = max(_dates(eligible).tolist())
            latest_industry = str(
                eligible.loc[_dates(eligible) == latest_date, "industry"].iloc[-1]
            ).strip()
            taxonomy_comparisons += 1
            taxonomy_mismatches += int(latest_industry != current_by_symbol[symbol])
            if taxonomy_mismatches > allowed_taxonomy_mismatches:
                failure = {
                    "symbol": symbol,
                    "reason": "taxonomy_overlap_below_threshold",
                    "comparison_count": taxonomy_comparisons,
                    "mismatch_count": taxonomy_mismatches,
                    "allowed_mismatch_count": allowed_taxonomy_mismatches,
                }
                break
        if not required_dates:
            success += 1
            audit_store.append_event(run_id, "industry_symbol_committed", {
                "symbol": symbol, "receipt_id": str(receipt_id),
                "projected_rows": 0,
                "excluded_before_rows": int((dates < BAK_BASIC_START).sum()),
                "excluded_future_rows": int((dates > target).sum()),
                "reason": "no_consumed_canonical_rows",
            })
            continue
        projection = _project_history_industry(
            frame,
            symbol=symbol,
            target_date=target,
            required_dates=required_dates,
        )
        symbol_mutations = collector.store.merge_daily_industry(
            projection, symbol, source_run_id=run_id, source_receipt_id=str(receipt_id)
        )
        audit_store.record_mutations(run_id=run_id, mutations=symbol_mutations)
        mutations += len(symbol_mutations)
        success += 1
        audit_store.append_event(run_id, "industry_symbol_committed", {
            "symbol": symbol, "receipt_id": str(receipt_id),
            "projected_rows": len(projection),
            "excluded_before_rows": int((dates < BAK_BASIC_START).sum()),
            "excluded_future_rows": int((dates > target).sum()),
        })
    complete = failure is None and success + empty == len(symbols)
    summary = {
        "status": "success" if complete else "failed",
        "query_axis": "all_history", "availability_cutoff": target,
        "date_start": BAK_BASIC_START, "date_end": target,
        "symbol_count": len(symbols), "symbols_sha256": stable_scope_hash(symbols),
        "receipt_count": len(receipts), "success_count": success,
        "empty_count": empty, "mutation_count": mutations,
        "excluded_future_rows": future_rows,
        "excluded_before_rows": before_rows,
        "taxonomy_comparison_count": taxonomy_comparisons,
        "taxonomy_expected_comparison_count": expected_taxonomy_comparisons,
        "taxonomy_mismatch_count": taxonomy_mismatches,
        "taxonomy_match_ratio": (
            (taxonomy_comparisons - taxonomy_mismatches) / taxonomy_comparisons
            if taxonomy_comparisons else None
        ),
        "taxonomy_min_match_ratio": float(min_taxonomy_match_ratio),
    }
    if failure:
        summary["failure"] = failure
    audit_store.append_event(run_id, "history_industry_evidence", summary)
    return summary, receipts


def fetch_audited_daily_industry(
    collector,
    codes: list[str],
    target_date: str,
    *,
    run_id: str,
    audit_store: SourceAuditStore,
    resume_proof: Mapping[str, object] | None,
    scope_key: str,
    universe: str,
    min_taxonomy_match_ratio: float = 0.95,
) -> tuple[dict, list[str]]:
    """Fetch one full-market date shard and project only requested canonical rows."""

    if universe != "csi1800":
        return {"status": "not_required"}, []
    target = str(target_date).replace("-", "")
    symbols = sorted({str(code).strip().upper() for code in codes if str(code).strip()})
    current_by_symbol = _current_taxonomy(collector.store.get_stock_list())
    if not 0.0 <= float(min_taxonomy_match_ratio) <= 1.0:
        raise ValueError("min_taxonomy_match_ratio must be between zero and one")
    required = {
        symbol for symbol in symbols
        if target in _canonical_required_dates(collector.store, symbol, target)
    }

    def validator(frame: pd.DataFrame) -> Mapping[str, object] | None:
        if frame is None or frame.empty:
            return None if not required else {"reason": "required_daily_snapshot_empty"}
        if len(frame) >= BAK_BASIC_ROW_LIMIT:
            return {"reason": "possible_supplier_truncation", "row_count": len(frame)}
        missing = sorted({"ts_code", "trade_date", "industry"} - set(frame.columns))
        if missing:
            return {"reason": "response_missing_columns", "missing_columns": missing}
        response_symbols = frame["ts_code"].astype("string").str.strip().str.upper()
        response_dates = _dates(frame)
        industries = frame["industry"].astype("string").str.strip()
        if not response_dates.eq(target).all():
            return {"reason": "response_date_out_of_scope"}
        if pd.DataFrame({"ts_code": response_symbols, "trade_date": response_dates}).duplicated().any():
            return {"reason": "response_duplicate_key"}
        if industries.isna().any() or industries.eq("").any():
            return {"reason": "response_industry_missing"}
        missing_symbols = sorted(required - set(response_symbols.tolist()))
        if missing_symbols:
            return {"reason": "canonical_coverage_missing", "missing_count": len(missing_symbols), "sample": missing_symbols[:10]}
        return None

    scope = {
        "date_start": target, "date_end": target,
        "query_axis": "trade_date_market_snapshot",
        "availability_cutoff": target,
        "symbol_count": len(symbols), "symbols": symbols,
        "symbols_sha256": stable_scope_hash(symbols),
    }
    frame, receipt_id = collector._fetch_daily_endpoint_with_receipt(
        "bak_basic", run_id=run_id, audit_store=audit_store,
        requested_scope=scope, resume_proof=resume_proof,
        scope_key=scope_key, universe=universe,
        request_variant=DAILY_REQUEST_VARIANT,
        identity_columns=("ts_code", "trade_date"), evidence_fields=("industry",),
        response_validator=validator, required_endpoint=True,
        trade_date=target, fields=BAK_BASIC_FIELDS,
    )
    if not receipt_id:
        raise RuntimeError("daily industry receipt missing")
    check = audit_store.verify_fetch_receipt(run_id=run_id, receipt_id=str(receipt_id))
    semantic_error = validator(frame)
    if check["status"] != "success" or semantic_error is not None:
        summary = {"status": "failed", "failure": semantic_error or check}
        audit_store.append_event(run_id, "daily_industry_evidence", summary)
        return summary, [str(receipt_id)]
    response_symbols = frame["ts_code"].astype("string").str.strip().str.upper() if not frame.empty else pd.Series(dtype="string")
    industries = frame["industry"].astype("string").str.strip() if not frame.empty else pd.Series(dtype="string")
    requested_rows = pd.DataFrame({"ts_code": response_symbols, "industry": industries})
    requested_rows = requested_rows.loc[requested_rows["ts_code"].isin(required)]
    comparisons = [
        (code, industry, current_by_symbol[code])
        for code, industry in requested_rows.itertuples(index=False, name=None)
        if current_by_symbol.get(code)
    ]
    mismatched = sorted(code for code, industry, current in comparisons if industry != current)
    comparison_count = len(comparisons)
    match_ratio = (
        (comparison_count - len(mismatched)) / comparison_count
        if comparison_count else None
    )
    taxonomy_ok = (
        not required
        or (
            comparison_count > 0
            and match_ratio is not None
            and match_ratio >= float(min_taxonomy_match_ratio)
        )
    )
    taxonomy_summary = {
        "comparison_count": comparison_count,
        "mismatch_count": len(mismatched),
        "match_ratio": match_ratio,
        "min_match_ratio": float(min_taxonomy_match_ratio),
        "mismatch_sample": mismatched[:10],
    }
    if not taxonomy_ok:
        summary = {
            "status": "failed", "failure": {
                "reason": "taxonomy_overlap_below_threshold", **taxonomy_summary,
            }, "taxonomy": taxonomy_summary,
        }
        audit_store.append_event(run_id, "daily_industry_evidence", summary)
        return summary, [str(receipt_id)]
    mutation_count = 0
    for symbol in sorted(required):
        projection = frame.loc[response_symbols == symbol, ["ts_code", "trade_date", "industry"]].copy()
        changes = collector.store.merge_daily_industry(
            projection, symbol, source_run_id=run_id, source_receipt_id=str(receipt_id)
        )
        audit_store.record_mutations(run_id=run_id, mutations=changes)
        mutation_count += len(changes)
    summary = {
        "status": "success", "date_start": target, "date_end": target,
        "symbol_count": len(symbols), "required_canonical_count": len(required),
        "receipt_count": 1, "mutation_count": mutation_count,
        "taxonomy": taxonomy_summary,
    }
    audit_store.append_event(run_id, "daily_industry_evidence", summary)
    return summary, [str(receipt_id)]
