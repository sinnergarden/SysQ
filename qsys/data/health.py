from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Iterable, Optional

import pandas as pd
from qlib.data import D

from qsys.data.adapter import QlibAdapter
from qsys.data.storage import StockDataStore


DEFAULT_REQUIRED_FIELDS = ("$open", "$high", "$low", "$close", "$volume", "$factor")
RAW_FIELD_PATTERN = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*")
PIT_FIELDS = ("$roe", "$grossprofit_margin", "$debt_to_assets", "$current_ratio")
MARGIN_FIELDS = (
    "$margin_balance",
    "$margin_buy_amount",
    "$margin_repay_amount",
    "$margin_total_balance",
    "$lend_volume",
    "$lend_sell_volume",
    "$lend_repay_volume",
)


@dataclass
class DataHealthReport:
    requested_date: str
    raw_latest: Optional[str]
    last_qlib_date: Optional[str]
    trading_calendar_last_date: Optional[str]
    expected_latest_date: Optional[str]
    date_ok: bool
    feature_rows: int
    feature_cols: int
    missing_ratio: float
    has_data_for_requested_date: bool
    gap_days: int
    aligned: bool
    required_fields: list[str]
    monitored_fields: list[str]
    column_missing_ratio: dict[str, float]
    unusable_required_fields: list[str]
    unusable_optional_fields: list[str]
    issues: list[str]
    blocking_issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    core_daily_status: str = "unknown"
    pit_status: str = "unknown"
    margin_status: str = "unknown"
    pit_missing_ratio: dict[str, float] = field(default_factory=dict)
    margin_missing_ratio: dict[str, float] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.blocking_issues

    def to_dict(self) -> dict:
        return {
            "requested_date": self.requested_date,
            "raw_latest": self.raw_latest,
            "last_qlib_date": self.last_qlib_date,
            "trading_calendar_last_date": self.trading_calendar_last_date,
            "expected_latest_date": self.expected_latest_date,
            "date_ok": self.date_ok,
            "feature_rows": self.feature_rows,
            "feature_cols": self.feature_cols,
            "missing_ratio": self.missing_ratio,
            "has_data_for_requested_date": self.has_data_for_requested_date,
            "gap_days": self.gap_days,
            "aligned": self.aligned,
            "required_fields": list(self.required_fields),
            "monitored_fields": list(self.monitored_fields),
            "column_missing_ratio": dict(self.column_missing_ratio),
            "unusable_required_fields": list(self.unusable_required_fields),
            "unusable_optional_fields": list(self.unusable_optional_fields),
            "issues": list(self.issues),
            "blocking_issues": list(self.blocking_issues),
            "warnings": list(self.warnings),
            "core_daily_status": self.core_daily_status,
            "pit_status": self.pit_status,
            "margin_status": self.margin_status,
            "pit_missing_ratio": dict(self.pit_missing_ratio),
            "margin_missing_ratio": dict(self.margin_missing_ratio),
        }

    def to_markdown(self) -> str:
        lines = ["## Data Health Check"]
        for key in [
            "requested_date",
            "expected_latest_date",
            "raw_latest",
            "last_qlib_date",
            "trading_calendar_last_date",
            "aligned",
            "feature_rows",
            "feature_cols",
            "has_data_for_requested_date",
            "gap_days",
            "core_daily_status",
            "pit_status",
            "margin_status",
        ]:
            lines.append(f"- {key}: {getattr(self, key)}")
        lines.append(f"- missing_ratio: {self.missing_ratio:.2%}")
        if self.blocking_issues:
            lines.append("- blocking_issues:")
            for issue in self.blocking_issues:
                lines.append(f"  - {issue}")
        if self.warnings:
            lines.append("- warnings:")
            for item in self.warnings:
                lines.append(f"  - {item}")
        if not self.blocking_issues and not self.warnings:
            lines.append("- issues: none")
        return "\n".join(lines)


class DataReadinessError(RuntimeError):
    def __init__(self, report: DataHealthReport):
        self.report = report
        message = (
            f"Data readiness check failed for {report.requested_date}: "
            + "; ".join(report.blocking_issues or report.issues)
        )
        super().__init__(message)


def _normalize_date(value: str | pd.Timestamp) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _normalize_feature_fields(feature_fields: Iterable[str] | dict | tuple | None) -> list[str]:
    if feature_fields is None:
        return []
    if isinstance(feature_fields, tuple) and len(feature_fields) == 2:
        return list(feature_fields[0])
    if isinstance(feature_fields, dict):
        fields = feature_fields.get("feature") or feature_fields.get("fields") or []
        if isinstance(fields, tuple) and len(fields) == 2:
            return list(fields[0])
        return list(fields)
    return list(feature_fields)


def _extract_probe_fields(feature_fields: Iterable[str], required_fields: Iterable[str]) -> list[str]:
    fields = set(required_fields)
    for item in feature_fields:
        if not isinstance(item, str):
            continue
        stripped = item.strip()
        if not stripped:
            continue
        if stripped.startswith("$"):
            fields.add(stripped)
            continue
        fields.update(RAW_FIELD_PATTERN.findall(stripped))
    return sorted(fields)


def _resolve_expected_latest_date(requested_date: str) -> tuple[str | None, str | None]:
    ts = pd.Timestamp(requested_date)
    calendar = D.calendar(start_time=ts - pd.Timedelta(days=31), end_time=ts)
    dates = [pd.Timestamp(x) for x in calendar]
    if not dates:
        return None, None
    trading_calendar_last_date = max(dates)
    eligible = [d for d in dates if d <= ts]
    if not eligible:
        return None, trading_calendar_last_date.strftime("%Y-%m-%d")
    expected = max(eligible)
    return expected.strftime("%Y-%m-%d"), trading_calendar_last_date.strftime("%Y-%m-%d")


def _classify_layer(fields: list[str], ratios: dict[str, float], threshold: float, *, empty_ok: bool = True) -> tuple[str, list[str]]:
    present = {field: ratios[field] for field in fields if field in ratios}
    if not present:
        return ("not_requested" if empty_ok else "warning"), []
    bad = [f"{field}={value:.2%}" for field, value in present.items() if value > threshold]
    if not bad:
        return "ok", []
    if len(bad) == len(present):
        return "warning", bad
    return "partial", bad


def inspect_qlib_data_health(
    requested_date: str,
    feature_fields: Iterable[str],
    *,
    universe: str = "csi300",
    missing_ratio_threshold: float = 0.4,
    required_fields: Iterable[str] = DEFAULT_REQUIRED_FIELDS,
    required_field_missing_threshold: float = 0.2,
    optional_field_missing_threshold: float = 0.95,
    pit_optional_field_missing_threshold: float = 0.97,
    margin_optional_field_missing_threshold: float = 0.995,
) -> DataHealthReport:
    adapter = QlibAdapter()
    adapter.init_qlib()

    requested_date = _normalize_date(requested_date)
    expected_latest_date, trading_calendar_last_date = _resolve_expected_latest_date(requested_date)

    store = StockDataStore()
    raw_latest_str = store.get_global_latest_date()
    raw_latest = _normalize_date(raw_latest_str) if raw_latest_str else None

    last_qlib_ts = adapter.get_last_qlib_date()
    last_qlib_date = _normalize_date(last_qlib_ts) if last_qlib_ts is not None else None
    aligned = bool(raw_latest and last_qlib_date and raw_latest == last_qlib_date)

    blocking_issues: list[str] = []
    warnings: list[str] = []
    gap_days = 0

    coverage = adapter.get_instrument_coverage_report(universe=universe)
    if not coverage.is_closed:
        blocking_issues.append(coverage.blocker_message())
    if expected_latest_date is None:
        blocking_issues.append("Failed to resolve expected latest trading date from calendar")
    elif last_qlib_date is None:
        blocking_issues.append("Qlib calendar is empty or unreadable")
    else:
        gap_days = max((pd.Timestamp(expected_latest_date) - pd.Timestamp(last_qlib_date)).days, 0)
        if pd.Timestamp(last_qlib_date) < pd.Timestamp(expected_latest_date):
            blocking_issues.append(
                f"Qlib data is stale: last_qlib_date={last_qlib_date}, expected_latest_date={expected_latest_date}"
            )

    fields = _normalize_feature_fields(feature_fields)
    required_fields_list = [f for f in required_fields if isinstance(f, str) and f.strip()]
    probe_fields = _extract_probe_fields(fields, required_fields_list)

    features = adapter.get_features(universe, fields, start_time=requested_date, end_time=requested_date)
    if features is None:
        features = pd.DataFrame()
    probe_features = adapter.get_features(universe, probe_fields, start_time=requested_date, end_time=requested_date)
    if probe_features is None:
        probe_features = pd.DataFrame()

    has_data = not features.empty
    feature_rows = len(features)
    feature_cols = len(features.columns)
    missing_ratio = float(features.isna().mean().mean()) if has_data and feature_cols > 0 else 1.0

    if not has_data:
        blocking_issues.append(f"No feature rows available for requested_date={requested_date}")
    elif missing_ratio > missing_ratio_threshold:
        warnings.append(
            f"Feature missing ratio high on {requested_date}: {missing_ratio:.2%} > {missing_ratio_threshold:.2%}"
        )

    column_missing_ratio: dict[str, float] = {}
    unusable_required_fields: list[str] = []
    unusable_optional_fields: list[str] = []
    if probe_features.empty:
        blocking_issues.append(f"No probe rows available for requested_date={requested_date} using fields={probe_fields}")
    else:
        for field in probe_fields:
            if field not in probe_features.columns:
                column_missing_ratio[field] = 1.0
            else:
                column_missing_ratio[field] = float(probe_features[field].isna().mean())
            miss_ratio = column_missing_ratio[field]
            if field in required_fields_list and miss_ratio > required_field_missing_threshold:
                unusable_required_fields.append(field)
            elif field not in required_fields_list and miss_ratio > optional_field_missing_threshold:
                unusable_optional_fields.append(field)

    if unusable_required_fields:
        details = ", ".join(f"{field}={column_missing_ratio.get(field, 1.0):.2%}" for field in sorted(set(unusable_required_fields)))
        blocking_issues.append(
            "Required qlib columns unusable "
            f"(missing ratio > {required_field_missing_threshold:.0%}): {details}"
        )

    core_daily_status = "ok" if not blocking_issues else "blocked"

    requested_set = set(probe_fields)
    pit_requested = [field for field in PIT_FIELDS if field in requested_set]
    margin_requested = [field for field in MARGIN_FIELDS if field in requested_set]
    pit_missing_ratio = {field: column_missing_ratio[field] for field in pit_requested if field in column_missing_ratio}
    margin_missing_ratio = {field: column_missing_ratio[field] for field in margin_requested if field in column_missing_ratio}

    pit_status, pit_bad = _classify_layer(pit_requested, pit_missing_ratio, pit_optional_field_missing_threshold)
    if pit_bad:
        warnings.append(
            "PIT fundamentals coverage weak but non-blocking: " + ", ".join(pit_bad)
        )

    margin_status, margin_bad = _classify_layer(margin_requested, margin_missing_ratio, margin_optional_field_missing_threshold)
    if margin_bad:
        warnings.append(
            "Margin layer coverage weak but non-blocking: " + ", ".join(margin_bad)
        )

    if features.empty and not probe_features.empty:
        blocking_issues.append(
            "Requested feature expressions yielded zero rows while probe fields have data; check expression dependencies and column usability"
        )

    date_ok = expected_latest_date == last_qlib_date if expected_latest_date and last_qlib_date else False
    issues = blocking_issues + warnings

    return DataHealthReport(
        requested_date=requested_date,
        raw_latest=raw_latest,
        last_qlib_date=last_qlib_date,
        trading_calendar_last_date=trading_calendar_last_date,
        expected_latest_date=expected_latest_date,
        date_ok=date_ok,
        feature_rows=feature_rows,
        feature_cols=feature_cols,
        missing_ratio=missing_ratio,
        has_data_for_requested_date=has_data,
        gap_days=gap_days,
        aligned=aligned,
        required_fields=required_fields_list,
        monitored_fields=probe_fields,
        column_missing_ratio=column_missing_ratio,
        unusable_required_fields=sorted(set(unusable_required_fields)),
        unusable_optional_fields=sorted(set(unusable_optional_fields)),
        issues=issues,
        blocking_issues=blocking_issues,
        warnings=warnings,
        core_daily_status=core_daily_status,
        pit_status=pit_status,
        margin_status=margin_status,
        pit_missing_ratio=pit_missing_ratio,
        margin_missing_ratio=margin_missing_ratio,
    )


def assert_qlib_data_ready(
    requested_date: str,
    feature_fields: Iterable[str],
    **kwargs,
) -> DataHealthReport:
    report = inspect_qlib_data_health(requested_date, feature_fields, **kwargs)
    if not report.ok:
        raise DataReadinessError(report)
    return report
