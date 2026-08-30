"""Minimal-pull DataFrame merge helpers extracted from TushareCollector.

These are pure functions with no dependency on ``self`` or instance state.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pandas as pd


FINANCIAL_AVAILABILITY_CONTRACT = "financial_first_available_v1"
FINANCIAL_LATEST_KNOWN_CONTRACT = (
    "financial_latest_known_actual_publication_v1"
)
FINANCIAL_OPERATIONAL_PIT_CONTRACT = "financial_operational_observed_v1"
FINANCIAL_BEST_EFFORT_BOOTSTRAP_CONTRACT = "financial_best_effort_bootstrap_v1"
FINANCIAL_VERSIONED_EVENT_CONTRACT = "financial_versioned_event_v1"
FINANCIAL_AVAILABILITY_RULE = (
    "publication_date_after_close_consumable_only_by_strictly_later_trade_date"
)
TUSHARE_FINA_INDICATOR_UNIT_CONTRACT = (
    "tushare_fina_indicator_percent_points_to_ratio_v1"
)
TUSHARE_FINA_INDICATOR_PERCENT_POINT_FIELDS = frozenset({
    "roe",
    "roe_waa",
    "roe_ttm",
    "grossprofit_margin",
    "debt_to_assets",
    "q_gr_yoy",
    "dt_netprofit_yoy",
    "profit_to_gr",
    "net_profit_margin",
})
STATEMENT_ENDPOINTS = frozenset({"income", "balancesheet", "cashflow"})
STATEMENT_LOGICAL_KEY = (
    "ts_code", "end_date", "report_type", "comp_type", "end_type",
)
INDICATOR_LOGICAL_KEY = ("ts_code", "end_date")
_STATEMENT_METADATA = frozenset(
    {
        *STATEMENT_LOGICAL_KEY, "ann_date", "f_ann_date", "publication_date",
        "update_flag",
    }
)
_INDICATOR_METADATA = frozenset(
    {*INDICATOR_LOGICAL_KEY, "ann_date", "publication_date", "update_flag"}
)
_EXPECTED_END_TYPE = {"0331": "1", "0630": "2", "0930": "3", "1231": "4"}
_CONSUMED_PAYLOAD_FIELDS = {
    "income": frozenset({"n_income", "revenue", "oper_cost"}),
    "balancesheet": frozenset({"total_assets", "total_hldr_eqy_exc_min_int"}),
    "cashflow": frozenset({"n_cashflow_act"}),
    "fina_indicator": frozenset({"roe", "grossprofit_margin", "debt_to_assets"}),
}
_LATEST_KNOWN_CONSUMED_PAYLOAD_FIELDS = {
    **_CONSUMED_PAYLOAD_FIELDS,
    "balancesheet": frozenset({
        "total_assets", "total_hldr_eqy_exc_min_int",
        "total_cur_assets", "total_cur_liab",
    }),
}


class FinancialAvailabilityError(RuntimeError):
    """A supplier response cannot be projected without guessing PIT semantics."""

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(f"{message}: {details}" if details else message)
        self.details = {"reason": message, **details}


def convert_tushare_fina_indicator_units(frame: pd.DataFrame) -> pd.DataFrame:
    """Convert Tushare ``fina_indicator`` percent points to ratios exactly once.

    The supplier contract, not a value threshold, owns the unit.  Callers must
    use this only on raw ``fina_indicator`` rows before they enter canonical
    storage.  Canonical-to-Qlib adapters pass these fields through unchanged.
    """

    if frame is None or frame.empty:
        return frame
    converted = frame.copy()
    for column in TUSHARE_FINA_INDICATOR_PERCENT_POINT_FIELDS:
        if column in converted.columns:
            converted[column] = pd.to_numeric(
                converted[column], errors="coerce"
            ) / 100.0
    return converted


def _diagnostic_json_value(value: Any) -> Any:
    """Keep bounded PIT diagnostics portable without changing data values."""

    if isinstance(value, dict):
        return {
            str(key): _diagnostic_json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_diagnostic_json_value(item) for item in value]
    if value is None or value is pd.NA:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _normalized_date_column(
    frame: pd.DataFrame, column: str, *, allow_missing: bool,
) -> pd.Series:
    values = frame[column].astype("string").str.strip()
    missing = values.isna() | values.eq("") | values.str.lower().eq("nan")
    compact = values.str.replace("-", "", regex=False)
    valid_shape = compact.str.fullmatch(r"\d{8}", na=False)
    parsed = pd.to_datetime(compact.where(valid_shape), format="%Y%m%d", errors="coerce")
    invalid = (~missing) & (~valid_shape | parsed.isna())
    if invalid.any() or (not allow_missing and missing.any()):
        samples = _diagnostic_json_value(
            values.loc[invalid | (missing if not allow_missing else False)].head(5).tolist()
        )
        raise FinancialAvailabilityError(
            "invalid_financial_date",
            column=column,
            invalid_count=int(invalid.sum() + ((missing).sum() if not allow_missing else 0)),
            samples=samples,
        )
    return parsed.dt.strftime("%Y%m%d").where(~missing)


def _normalized_code_column(frame: pd.DataFrame, column: str) -> pd.Series:
    values = frame[column].astype("string").str.strip()
    values = values.str.replace(r"\.0$", "", regex=True)
    return values.where(~(values.isna() | values.eq("") | values.str.lower().eq("nan")))


def _differing_columns(frame: pd.DataFrame, columns: list[str]) -> list[str]:
    return [column for column in columns if frame[column].nunique(dropna=False) > 1]


def _deterministic_first(frame: pd.DataFrame) -> pd.Series:
    order = [
        column for column in (
            "availability_date", "publication_date", "ann_date", "f_ann_date", "update_flag",
            "report_type", "comp_type", "end_type", "ts_code", "end_date",
        )
        if column in frame.columns
    ]
    return frame.sort_values(order, kind="mergesort", na_position="last").iloc[0]


def _consumed_value_tuple(row: pd.Series, fields: list[str]) -> tuple[Any, ...]:
    return tuple(_diagnostic_json_value(row[field]) for field in fields)


def _value_fingerprint(value: tuple[Any, ...]) -> str:
    payload = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _observed_session(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    compact = text.replace("-", "")
    if len(compact) == 8 and compact.isdigit():
        return _normalized_date_column(
            pd.DataFrame({"observed": [compact]}),
            "observed", allow_missing=False,
        ).iloc[0]
    parsed = pd.to_datetime(text, errors="coerce", utc=True)
    if pd.isna(parsed):
        raise FinancialAvailabilityError(
            "invalid_first_observed_at", first_observed_at=text,
        )
    return parsed.strftime("%Y%m%d")


def _select_financial_versioned_rows(
    frame: pd.DataFrame,
    *,
    endpoint: str,
    availability_cutoff: str | None,
    projection: str,
    first_observed_at: str | None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build versioned financial events, then apply one explicit PIT projection.

    ``strict_market`` keeps only source-dated complete timelines. ``operational``
    adds one supplier-current row for an incomplete timeline, visible no earlier
    than the immutable receipt's observation date. The certification oracle is
    intentionally separate from this materializer.
    """

    endpoint = str(endpoint)
    if endpoint not in {*STATEMENT_ENDPOINTS, "fina_indicator"}:
        raise ValueError(f"unsupported financial endpoint: {endpoint}")
    if projection not in {"strict_market", "operational"}:
        raise ValueError(f"unsupported financial projection: {projection}")
    observed_session = _observed_session(first_observed_at)
    if projection == "operational" and observed_session is None:
        raise FinancialAvailabilityError(
            "operational_projection_requires_first_observed_at",
            endpoint=endpoint,
        )
    cutoff = None
    if availability_cutoff is not None:
        cutoff = _normalized_date_column(
            pd.DataFrame({"cutoff": [availability_cutoff]}),
            "cutoff", allow_missing=False,
        ).iloc[0]
    stats: dict[str, Any] = {
        "contract": (
            FINANCIAL_LATEST_KNOWN_CONTRACT
            if projection == "strict_market"
            else FINANCIAL_OPERATIONAL_PIT_CONTRACT
        ),
        "source_event_contract": FINANCIAL_VERSIONED_EVENT_CONTRACT,
        "projection": projection,
        "first_observed_session": observed_session,
        "availability_rule": FINANCIAL_AVAILABILITY_RULE,
        "availability_cutoff": cutoff,
        "raw_rows": 0,
        "eligible_primary_rows": 0,
        "projected_rows": 0,
        "complete_logical_keys": 0,
        "blocked_logical_keys": 0,
        "right_censored_keys": 0,
        "same_publication_conflict_keys": 0,
        "proven_events": 0,
        "proven_revision_events": 0,
        "equivalent_republication_events": 0,
        "collapsed_equal_same_date_rows": 0,
        "excluded_future_rows": 0,
        "excluded_non_primary_report_type_rows": 0,
        "excluded_unsupported_statement_period_rows": 0,
        "excluded_missing_end_type_keys": 0,
        "canonical_branch_conflict_keys": 0,
        "observed_only_events": 0,
        "operational_unresolved_keys": 0,
        "excluded_observed_after_cutoff": 0,
        "exceptions": [],
    }
    if frame is None or frame.empty:
        empty = frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()
        empty["publication_date"] = pd.Series(dtype="object")
        empty["availability_date"] = pd.Series(dtype="object")
        return empty, stats

    work = frame.copy()
    stats["raw_rows"] = len(work)
    is_statement = endpoint in STATEMENT_ENDPOINTS
    logical_key = list(STATEMENT_LOGICAL_KEY if is_statement else INDICATOR_LOGICAL_KEY)
    consumed_fields = sorted(_LATEST_KNOWN_CONSUMED_PAYLOAD_FIELDS[endpoint])
    required = set(logical_key) | {"ann_date", "update_flag", *consumed_fields}
    if is_statement:
        required.add("f_ann_date")
    missing = sorted(required - set(work.columns))
    if missing:
        raise FinancialAvailabilityError(
            "missing_financial_fields", endpoint=endpoint, fields=missing,
        )

    symbols = work["ts_code"].astype("string").str.strip()
    invalid_symbols = symbols.isna() | symbols.eq("") | symbols.str.lower().eq("nan")
    if invalid_symbols.any():
        raise FinancialAvailabilityError(
            "invalid_financial_symbol", endpoint=endpoint,
            invalid_count=int(invalid_symbols.sum()),
        )
    work["ts_code"] = symbols
    work["ann_date"] = _normalized_date_column(work, "ann_date", allow_missing=False)
    work["end_date"] = _normalized_date_column(work, "end_date", allow_missing=False)
    work["update_flag"] = _normalized_code_column(work, "update_flag")
    invalid_flags = ~work["update_flag"].isin(["0", "1"])
    if invalid_flags.any():
        raise FinancialAvailabilityError(
            "invalid_update_flag", endpoint=endpoint,
            invalid_count=int(invalid_flags.sum()),
        )

    if is_statement:
        work["f_ann_date"] = _normalized_date_column(
            work, "f_ann_date", allow_missing=True,
        )
        work["publication_date"] = work[["ann_date", "f_ann_date"]].max(axis=1)
        for column in ("report_type", "comp_type", "end_type"):
            work[column] = _normalized_code_column(work, column)
        if work["comp_type"].isna().any():
            raise FinancialAvailabilityError(
                "missing_comp_type", endpoint=endpoint,
                invalid_count=int(work["comp_type"].isna().sum()),
            )
        primary = work["report_type"].eq("1")
        stats["excluded_non_primary_report_type_rows"] = int((~primary).sum())
        work = work.loc[primary].copy()
        supported = work["end_date"].astype(str).str.slice(4).isin(_EXPECTED_END_TYPE)
        stats["excluded_unsupported_statement_period_rows"] = int((~supported).sum())
        work = work.loc[supported].copy()
        kept: list[pd.DataFrame] = []
        for (_symbol, end_date), group in work.groupby(
            ["ts_code", "end_date"], dropna=False, sort=True,
        ):
            expected = _EXPECTED_END_TYPE[str(end_date)[4:]]
            matched = group.loc[group["end_type"].eq(expected)]
            if not matched.empty:
                kept.append(matched)
                continue
            if group["end_type"].isna().all():
                stats["excluded_missing_end_type_keys"] += 1
                continue
            raise FinancialAvailabilityError(
                "statement_end_type_mismatch", endpoint=endpoint,
                ts_code=str(_symbol), end_date=str(end_date),
                expected_end_type=expected,
                branches=sorted(group["end_type"].dropna().unique().tolist()),
            )
        work = pd.concat(kept, ignore_index=True) if kept else work.iloc[0:0].copy()
    else:
        if len(work) >= 100:
            raise FinancialAvailabilityError(
                "fina_indicator_possible_truncation", endpoint=endpoint,
                returned_rows=len(work), supplier_row_limit=100,
            )
        work["publication_date"] = work["ann_date"]
    work["availability_date"] = work["publication_date"]
    stats["eligible_primary_rows"] = len(work)
    if cutoff is not None:
        future = work["publication_date"].gt(cutoff)
        stats["excluded_future_rows"] = int(future.sum())
        work = work.loc[~future].copy()

    selected: list[pd.Series] = []
    for key, group in work.groupby(logical_key, dropna=False, sort=True):
        key_values = key if isinstance(key, tuple) else (key,)
        key_mapping = _diagnostic_json_value(dict(zip(logical_key, key_values)))
        earliest = group["publication_date"].min()
        first = group.loc[group["publication_date"].eq(earliest)]
        right_censored = not first["update_flag"].eq("0").any()
        conflict_dates = [
            str(publication_date)
            for publication_date, published in group.groupby(
                "publication_date", dropna=False, sort=True,
            )
            if _differing_columns(published, consumed_fields)
        ]
        if right_censored or conflict_dates:
            stats["blocked_logical_keys"] += 1
            stats["right_censored_keys"] += int(right_censored)
            stats["same_publication_conflict_keys"] += int(bool(conflict_dates))
            if len(stats["exceptions"]) < 100:
                stats["exceptions"].append({
                    "reason": (
                        "initial_publication_value_missing"
                        if right_censored else "same_publication_value_conflict"
                    ),
                    "endpoint": endpoint,
                    "logical_key": key_mapping,
                    "conflict_dates": conflict_dates,
                    "row_count": len(group),
                })
            if projection == "operational":
                current = group.loc[group["update_flag"].eq("1")].copy()
                if current.empty:
                    latest_publication = group["publication_date"].max()
                    current = group.loc[
                        group["publication_date"].eq(latest_publication)
                    ].copy()
                else:
                    latest_publication = current["publication_date"].max()
                    current = current.loc[
                        current["publication_date"].eq(latest_publication)
                    ].copy()
                if _differing_columns(current, consumed_fields):
                    stats["operational_unresolved_keys"] += 1
                    continue
                row = _deterministic_first(current).copy()
                availability_date = max(
                    str(observed_session), str(row["publication_date"]),
                )
                if cutoff is not None and availability_date > cutoff:
                    stats["excluded_observed_after_cutoff"] += 1
                    continue
                value = _consumed_value_tuple(row, consumed_fields)
                row["availability_date"] = availability_date
                row["source_available_session"] = pd.NA
                row["first_observed_session"] = observed_session
                row["availability_evidence"] = "observed_only"
                row["pit_tier"] = "operational_pit"
                row["row_fingerprint"] = _value_fingerprint(value)
                row["event_kind"] = (
                    "RIGHT_CENSORED_FIRST_OBSERVED"
                    if right_censored else "UNTIMED_REVISION_FIRST_OBSERVED"
                )
                row["capability_status"] = "OBSERVED_ONLY"
                selected.append(row)
                stats["observed_only_events"] += 1
            continue

        stats["complete_logical_keys"] += 1
        previous_value: tuple[Any, ...] | None = None
        for position, (_publication_date, published) in enumerate(group.groupby(
            "publication_date", dropna=False, sort=True,
        )):
            stats["collapsed_equal_same_date_rows"] += len(published) - 1
            row = _deterministic_first(published).copy()
            value = _consumed_value_tuple(row, consumed_fields)
            if position == 0:
                event_kind = "INITIAL_PUBLICATION"
            elif value == previous_value:
                event_kind = "EQUIVALENT_REPUBLICATION"
                stats["equivalent_republication_events"] += 1
            else:
                event_kind = "REVISION_PUBLICATION"
                stats["proven_revision_events"] += 1
            row["event_kind"] = event_kind
            row["capability_status"] = "PROVEN_COMPLETE_KEY"
            row["source_available_session"] = row["publication_date"]
            row["first_observed_session"] = observed_session
            row["availability_evidence"] = "provider_date"
            row["pit_tier"] = "strict_market_pit"
            row["row_fingerprint"] = _value_fingerprint(value)
            selected.append(row)
            stats["proven_events"] += 1
            previous_value = value

    result = (
        pd.DataFrame(selected).reset_index(drop=True)
        if selected else work.iloc[0:0].copy()
    )
    if is_statement and not result.empty:
        bad_periods: set[tuple[str, str]] = set()
        for (symbol, end_date, _publication_date), published in result.groupby(
            ["ts_code", "end_date", "publication_date"],
            dropna=False, sort=True,
        ):
            if _differing_columns(published, consumed_fields):
                bad_periods.add((str(symbol), str(end_date)))
        if bad_periods:
            bad = pd.Series(
                list(zip(result["ts_code"].astype(str), result["end_date"].astype(str))),
                index=result.index,
            ).isin(bad_periods)
            result = result.loc[~bad].copy()
            stats["canonical_branch_conflict_keys"] = len(bad_periods)
        collapsed: list[pd.Series] = []
        for (_symbol, _end_date, _publication_date), published in result.groupby(
            ["ts_code", "end_date", "publication_date"],
            dropna=False, sort=True,
        ):
            collapsed.append(_deterministic_first(published).copy())
        result = (
            pd.DataFrame(collapsed).reset_index(drop=True)
            if collapsed else result.iloc[0:0].copy()
        )
    stats["projected_rows"] = len(result)
    return result.reset_index(drop=True), stats


def select_latest_known_financial_rows(
    frame: pd.DataFrame,
    *,
    endpoint: str,
    availability_cutoff: str | None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Project only source-dated, complete latest-known market events."""

    return _select_financial_versioned_rows(
        frame,
        endpoint=endpoint,
        availability_cutoff=availability_cutoff,
        projection="strict_market",
        first_observed_at=None,
    )


def select_operational_financial_rows(
    frame: pd.DataFrame,
    *,
    endpoint: str,
    availability_cutoff: str | None,
    first_observed_at: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Project source-dated events plus observation-bounded current versions."""

    return _select_financial_versioned_rows(
        frame,
        endpoint=endpoint,
        availability_cutoff=availability_cutoff,
        projection="operational",
        first_observed_at=first_observed_at,
    )


def select_first_available_financial_rows(
    frame: pd.DataFrame,
    *,
    endpoint: str,
    availability_cutoff: str | None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Implement the legacy vendor-date projection used by bootstrap only.

    Supplier rows are never modified by the caller before its raw receipt is
    persisted.  This function returns a separate projection: statements use
    ``max(ann_date, f_ann_date)`` and indicators use ``ann_date``.  When an
    initial (``update_flag=0``) row exists, the earliest initial row is used;
    otherwise only statements with an independently later final-announcement
    date remain eligible.  A public canonical row cannot represent conflicting
    consumed company-type branches, so those responses fail closed instead of
    relying on row order. Callers that consume these rows must go through
    :func:`select_best_effort_bootstrap_financial_rows` so the downgraded trust
    tier is explicit.
    """

    endpoint = str(endpoint)
    if endpoint not in {*STATEMENT_ENDPOINTS, "fina_indicator"}:
        raise ValueError(f"unsupported financial endpoint: {endpoint}")
    cutoff = None
    if availability_cutoff is not None:
        cutoff_frame = pd.DataFrame({"cutoff": [availability_cutoff]})
        cutoff = _normalized_date_column(
            cutoff_frame, "cutoff", allow_missing=False,
        ).iloc[0]
    if frame is None or frame.empty:
        empty = frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()
        empty["availability_date"] = pd.Series(dtype="object")
        return empty, {
            "contract": FINANCIAL_AVAILABILITY_CONTRACT,
            "availability_rule": FINANCIAL_AVAILABILITY_RULE,
            "availability_cutoff": cutoff,
            "raw_rows": 0,
            "selected_first_available_rows": 0,
            "projected_rows": 0,
            "excluded_future_rows": 0,
            "excluded_later_revision_rows": 0,
            "excluded_non_primary_report_type_rows": 0,
            "excluded_unsupported_statement_period_rows": 0,
            "unsupported_statement_period_exceptions": [],
            "collapsed_equivalent_branch_rows": 0,
            "missing_end_type_fallback_keys": 0,
            "non_consumed_branch_exception_count": 0,
            "non_consumed_branch_exceptions": [],
            "revision_timeline_unproven_excluded_keys": 0,
            "revision_timeline_unproven_excluded_rows": 0,
            "revision_timeline_unproven_exceptions": [],
        }

    work = frame.copy()
    is_statement = endpoint in STATEMENT_ENDPOINTS
    logical_key = list(STATEMENT_LOGICAL_KEY if is_statement else INDICATOR_LOGICAL_KEY)
    required = set(logical_key) | {"ann_date", "update_flag"}
    if is_statement:
        required.add("f_ann_date")
    missing_columns = sorted(required - set(work.columns))
    if missing_columns:
        raise FinancialAvailabilityError(
            "missing_financial_fields", endpoint=endpoint, fields=missing_columns,
        )

    ts_codes = work["ts_code"].astype("string").str.strip()
    invalid_symbols = ts_codes.isna() | ts_codes.eq("") | ts_codes.str.lower().eq("nan")
    if invalid_symbols.any():
        raise FinancialAvailabilityError(
            "invalid_financial_symbol", endpoint=endpoint,
            invalid_count=int(invalid_symbols.sum()),
        )
    work["ts_code"] = ts_codes
    work["ann_date"] = _normalized_date_column(work, "ann_date", allow_missing=False)
    work["end_date"] = _normalized_date_column(work, "end_date", allow_missing=False)
    if is_statement:
        work["f_ann_date"] = _normalized_date_column(
            work, "f_ann_date", allow_missing=True,
        )
        work["publication_date"] = work[["ann_date", "f_ann_date"]].max(axis=1)
    else:
        if len(work) >= 100:
            raise FinancialAvailabilityError(
                "fina_indicator_possible_truncation",
                endpoint=endpoint, returned_rows=len(work), supplier_row_limit=100,
            )
        work["publication_date"] = work["ann_date"]
    work["availability_date"] = work["publication_date"]

    work["update_flag"] = _normalized_code_column(work, "update_flag")
    invalid_flags = ~work["update_flag"].isin(["0", "1"])
    if invalid_flags.any():
        raise FinancialAvailabilityError(
            "invalid_update_flag", endpoint=endpoint,
            invalid_count=int(invalid_flags.sum()),
            values=sorted(work.loc[invalid_flags, "update_flag"].dropna().unique().tolist()),
        )

    raw_rows = len(work)
    excluded_future_rows = int(
        (work["availability_date"] > cutoff).sum() if cutoff is not None else 0
    )
    excluded_non_primary = 0
    missing_end_type_fallback_keys = 0
    unsupported_statement_period_rows = 0
    unsupported_statement_period_exceptions: list[dict[str, Any]] = []
    metadata = _STATEMENT_METADATA if is_statement else _INDICATOR_METADATA
    payload_columns = sorted(set(work.columns) - metadata - {"availability_date"})
    if not payload_columns:
        raise FinancialAvailabilityError("missing_financial_payload", endpoint=endpoint)

    if is_statement:
        work["report_type"] = _normalized_code_column(work, "report_type")
        work["comp_type"] = _normalized_code_column(work, "comp_type")
        work["end_type"] = _normalized_code_column(work, "end_type")
        if work["comp_type"].isna().any():
            raise FinancialAvailabilityError(
                "missing_comp_type", endpoint=endpoint,
                invalid_count=int(work["comp_type"].isna().sum()),
            )
        primary = work["report_type"].eq("1")
        excluded_non_primary = int((~primary).sum())
        work = work.loc[primary].copy()
        supported_period = work["end_date"].astype(str).str.slice(4).isin(
            _EXPECTED_END_TYPE
        )
        unsupported = work.loc[~supported_period]
        unsupported_statement_period_rows = len(unsupported)
        for (symbol, end_date), group in unsupported.groupby(
            ["ts_code", "end_date"], dropna=False, sort=True,
        ):
            if len(unsupported_statement_period_exceptions) >= 100:
                break
            unsupported_statement_period_exceptions.append({
                "reason": "unsupported_statement_period_excluded",
                "endpoint": endpoint,
                "ts_code": str(symbol),
                "end_date": str(end_date),
                "row_count": len(group),
            })
        work = work.loc[supported_period].copy()

        end_type_rows: list[pd.DataFrame] = []
        for (symbol, end_date), group in work.groupby(
            ["ts_code", "end_date"], dropna=False, sort=True,
        ):
            expected = _EXPECTED_END_TYPE.get(str(end_date)[4:])
            if expected is None:
                raise FinancialAvailabilityError(
                    "unsupported_statement_period",
                    endpoint=endpoint, ts_code=str(symbol), end_date=str(end_date),
                )
            matched = group.loc[group["end_type"].eq(expected)]
            if not matched.empty:
                end_type_rows.append(matched)
                continue
            branch_values = sorted(group["end_type"].dropna().unique().tolist())
            if not branch_values:
                missing_end_type_fallback_keys += 1
                end_type_rows.append(group)
                continue
            raise FinancialAvailabilityError(
                "statement_end_type_mismatch",
                endpoint=endpoint, ts_code=str(symbol), end_date=str(end_date),
                expected_end_type=expected, branches=branch_values,
            )
        work = (
            pd.concat(end_type_rows, ignore_index=True)
            if end_type_rows else work.iloc[0:0].copy()
        )

    logical_selected: list[pd.Series] = []
    excluded_later_revision_rows = 0
    unproven_key_count = 0
    unproven_row_count = 0
    unproven_exceptions: list[dict[str, Any]] = []
    for key, group in work.groupby(logical_key, dropna=False, sort=True):
        logical_key_value = _diagnostic_json_value(
            dict(zip(logical_key, key if isinstance(key, tuple) else (key,)))
        )
        initial = group.loc[group["update_flag"].eq("0")]
        if not initial.empty:
            eligible_timeline = initial
            # Once an initial row exists, every non-selected row is a later
            # revision excluded by this first-available-v1 projection.
            revision_rows = group
            unproven_rows = group.iloc[0:0]
        elif is_statement:
            independently_timed = group["f_ann_date"] > group["ann_date"]
            eligible_timeline = group.loc[independently_timed]
            revision_rows = eligible_timeline
            unproven_rows = group.loc[~independently_timed]
        else:
            eligible_timeline = group.iloc[0:0]
            revision_rows = eligible_timeline
            unproven_rows = group
        if not unproven_rows.empty:
            unproven_key_count += 1
            unproven_row_count += len(unproven_rows)
            if len(unproven_exceptions) < 100:
                unproven_exceptions.append({
                    "reason": "revision_timeline_unproven_excluded",
                    "endpoint": endpoint,
                    "logical_key": logical_key_value,
                    "row_count": len(unproven_rows),
                })
        if eligible_timeline.empty:
            continue
        earliest = eligible_timeline["availability_date"].min()
        candidates = eligible_timeline.loc[
            eligible_timeline["availability_date"].eq(earliest)
        ]
        # Count only independently timed revision rows.  Unproven rows are
        # counted separately, including mixed keys that still yield a row.
        excluded_later_revision_rows += max(len(revision_rows) - len(candidates), 0)
        if cutoff is not None and earliest > cutoff:
            logical_selected.append(_deterministic_first(candidates))
            continue
        differing = _differing_columns(candidates, payload_columns)
        if differing:
            raise FinancialAvailabilityError(
                "same_priority_payload_conflict",
                endpoint=endpoint,
                logical_key=logical_key_value,
                availability_date=str(earliest),
                differing_fields=differing,
                row_count=len(candidates),
            )
        logical_selected.append(_deterministic_first(candidates))

    selected = (
        pd.DataFrame(logical_selected).reset_index(drop=True)
        if logical_selected else work.iloc[0:0].copy()
    )
    selected_first_available_rows = len(selected)
    eligible = (
        selected.loc[selected["availability_date"] <= cutoff].copy()
        if cutoff is not None else selected.copy()
    )

    collapsed_equivalent = 0
    non_consumed_branch_exception_count = 0
    non_consumed_branch_exceptions: list[dict[str, Any]] = []
    if is_statement and not eligible.empty:
        canonical_rows: list[pd.Series] = []
        for (symbol, end_date), group in eligible.groupby(
            ["ts_code", "end_date"], dropna=False, sort=True,
        ):
            first_availability = group["availability_date"].min()
            candidates = group.loc[
                group["availability_date"].eq(first_availability)
            ]
            excluded_later_revision_rows += len(group) - len(candidates)
            differing = _differing_columns(candidates, payload_columns)
            branches = _diagnostic_json_value(candidates[
                [
                    "comp_type", "end_type", "availability_date", "ann_date",
                    "f_ann_date", "update_flag",
                ]
            ].to_dict(orient="records"))
            consumed_conflicts = sorted(
                set(differing).intersection(_CONSUMED_PAYLOAD_FIELDS[endpoint])
            )
            if consumed_conflicts:
                raise FinancialAvailabilityError(
                    "canonical_company_type_branch_conflict",
                    endpoint=endpoint, ts_code=str(symbol), end_date=str(end_date),
                    differing_fields=consumed_conflicts, branches=branches,
                )
            row = _deterministic_first(candidates).copy()
            if differing:
                # The baseline does not consume these fields.  Do not guess a
                # company-type branch: retain the public column as missing and
                # emit a bounded diagnostic in the projection audit event.
                for column in differing:
                    row[column] = pd.NA
                non_consumed_branch_exception_count += 1
                if len(non_consumed_branch_exceptions) < 100:
                    non_consumed_branch_exceptions.append({
                        "reason": "non_consumed_company_type_branch_conflict",
                        "ts_code": str(symbol),
                        "end_date": str(end_date),
                        "fields": differing,
                        "branches": branches,
                    })
            else:
                collapsed_equivalent += max(len(candidates) - 1, 0)
            canonical_rows.append(row)
        eligible = pd.DataFrame(canonical_rows).reset_index(drop=True)

    eligible = eligible.drop(columns=[column for column in eligible if column.startswith("_")])
    stats = {
        "contract": FINANCIAL_AVAILABILITY_CONTRACT,
        "availability_rule": FINANCIAL_AVAILABILITY_RULE,
        "availability_cutoff": cutoff,
        "raw_rows": raw_rows,
        "selected_first_available_rows": selected_first_available_rows,
        "projected_rows": len(eligible),
        "excluded_future_rows": excluded_future_rows,
        "excluded_later_revision_rows": excluded_later_revision_rows,
        "excluded_non_primary_report_type_rows": excluded_non_primary,
        "excluded_unsupported_statement_period_rows": (
            unsupported_statement_period_rows
        ),
        "unsupported_statement_period_exceptions": (
            unsupported_statement_period_exceptions
        ),
        "collapsed_equivalent_branch_rows": collapsed_equivalent,
        "missing_end_type_fallback_keys": missing_end_type_fallback_keys,
        "non_consumed_branch_exception_count": non_consumed_branch_exception_count,
        "non_consumed_branch_exceptions": non_consumed_branch_exceptions,
        "revision_timeline_unproven_excluded_keys": unproven_key_count,
        "revision_timeline_unproven_excluded_rows": unproven_row_count,
        "revision_timeline_unproven_exceptions": unproven_exceptions,
    }
    return eligible.reset_index(drop=True), stats


def select_best_effort_bootstrap_financial_rows(
    frame: pd.DataFrame,
    *,
    endpoint: str,
    availability_cutoff: str | None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Expose legacy vendor-date history only under an explicit downgraded tier."""

    projected, stats = select_first_available_financial_rows(
        frame,
        endpoint=endpoint,
        availability_cutoff=availability_cutoff,
    )
    stats = {
        **stats,
        "contract": FINANCIAL_BEST_EFFORT_BOOTSTRAP_CONTRACT,
        "source_projection_contract": FINANCIAL_AVAILABILITY_CONTRACT,
        "projection": "best_effort_bootstrap",
    }
    if projected.empty:
        return projected, stats
    projected = projected.copy()
    value_fields = sorted(
        set(_LATEST_KNOWN_CONSUMED_PAYLOAD_FIELDS[str(endpoint)])
        .intersection(projected.columns)
    )
    projected["source_available_session"] = pd.NA
    projected["first_observed_session"] = pd.NA
    projected["availability_evidence"] = "vendor_date"
    projected["pit_tier"] = "best_effort_pit"
    projected["row_fingerprint"] = projected.apply(
        lambda row: _value_fingerprint(_consumed_value_tuple(row, value_fields)),
        axis=1,
    )
    projected["capability_status"] = "BEST_EFFORT_BOOTSTRAP"
    return projected, stats


def merge_trade_frames(left: pd.DataFrame, right: pd.DataFrame, *, keys: list[str]) -> pd.DataFrame:
    """Left-merge two DataFrames, coalescing overlapping columns with ``combine_first``.

    Overlapping columns in *right* (those not in *keys*) that also exist in *left*
    are merged with a ``__src`` suffix pattern, then combined with
    ``left[col].combine_first(right[col])`` so the left value wins when non-null.
    """
    if left is None or left.empty:
        return right.copy() if right is not None else pd.DataFrame()
    if right is None or right.empty:
        return left

    overlapping = [col for col in right.columns if col in left.columns and col not in keys]
    merged = pd.merge(left, right, on=keys, how="left", suffixes=("", "__src"))
    for col in overlapping:
        src_col = f"{col}__src"
        if src_col not in merged.columns:
            continue
        merged[col] = merged[col].combine_first(merged[src_col])
        merged = merged.drop(columns=[src_col])
    return merged


def prepare_financial_frame(df: pd.DataFrame, value_cols) -> pd.DataFrame:
    """Validate, clean, and sort a financial DataFrame for PIT merge.

    Requires the already-audited ``availability_date`` projection and sorts by
    ``[ts_code, availability_date, end_date]``.  Supplier revision metadata is
    intentionally kept out of canonical rows.
    """
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    if "availability_date" not in df.columns:
        return pd.DataFrame()

    if "end_date" not in df.columns:
        df["end_date"] = None

    # Clean date fields
    df["availability_date"] = df["availability_date"].replace("", None)
    df["end_date"] = df["end_date"].replace("", None)

    df = df[df["availability_date"].notna()]

    df["_availability_dt"] = pd.to_datetime(df["availability_date"], errors="coerce")
    df["_end_dt"] = pd.to_datetime(df["end_date"], errors="coerce")

    df = df.sort_values(["ts_code", "_availability_dt", "_end_dt"])

    cols = ["ts_code", "availability_date", "end_date"] + list(value_cols)
    cols = [c for c in cols if c in df.columns]
    return df[cols]
