from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Optional

import pandas as pd
from qlib.data import D

from qsys.data.adapter import QlibAdapter
from qsys.data.storage import StockDataStore


DEFAULT_REQUIRED_FIELDS = ("$open", "$high", "$low", "$close", "$volume", "$factor")
RAW_FIELD_PATTERN = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*")


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

    @property
    def ok(self) -> bool:
        return not self.issues

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
        }

    def to_markdown(self) -> str:
        lines = ["## Data Health Check"]
        lines.append(f"- requested_date: {self.requested_date}")
        lines.append(f"- expected_latest_date: {self.expected_latest_date}")
        lines.append(f"- raw_latest: {self.raw_latest}")
        lines.append(f"- last_qlib_date: {self.last_qlib_date}")
        lines.append(f"- trading_calendar_last_date: {self.trading_calendar_last_date}")
        lines.append(f"- aligned: {self.aligned}")
        lines.append(f"- feature_rows: {self.feature_rows}")
        lines.append(f"- feature_cols: {self.feature_cols}")
        lines.append(f"- missing_ratio: {self.missing_ratio:.2%}")
        lines.append(f"- has_data_for_requested_date: {self.has_data_for_requested_date}")
        lines.append(f"- gap_days: {self.gap_days}")
        if self.unusable_required_fields:
            lines.append(f"- unusable_required_fields: {self.unusable_required_fields}")
        if self.unusable_optional_fields:
            lines.append(f"- unusable_optional_fields: {self.unusable_optional_fields}")
        if self.issues:
            lines.append("- issues:")
            for issue in self.issues:
                lines.append(f"  - {issue}")
        else:
            lines.append("- issues: none")
        return "\n".join(lines)


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


class DataReadinessError(RuntimeError):
    def __init__(self, report: DataHealthReport):
        self.report = report
        message = (
            f"Data readiness check failed for {report.requested_date}: "
            + "; ".join(report.issues)
        )
        super().__init__(message)


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


def inspect_qlib_data_health(
    requested_date: str,
    feature_fields: Iterable[str],
    *,
    universe: str = "csi300",
    missing_ratio_threshold: float = 0.4,
    required_fields: Iterable[str] = DEFAULT_REQUIRED_FIELDS,
    required_field_missing_threshold: float = 0.2,
    optional_field_missing_threshold: float = 0.95,
) -> DataHealthReport:
    adapter = QlibAdapter()
    adapter.init_qlib()

    requested_date = _normalize_date(requested_date)
    expected_latest_date, trading_calendar_last_date = _resolve_expected_latest_date(requested_date)

    # Get raw latest date
    store = StockDataStore()
    raw_latest_str = store.get_global_latest_date()
    raw_latest = _normalize_date(raw_latest_str) if raw_latest_str else None

    last_qlib_ts = adapter.get_last_qlib_date()
    last_qlib_date = _normalize_date(last_qlib_ts) if last_qlib_ts is not None else None

    # Check alignment
    aligned = False
    if raw_latest and last_qlib_date:
        aligned = raw_latest == last_qlib_date

    issues: list[str] = []
    gap_days = 0
    if expected_latest_date is None:
        issues.append("Failed to resolve expected latest trading date from calendar")
    elif last_qlib_date is None:
        issues.append("Qlib calendar is empty or unreadable")
    else:
        gap_days = max((pd.Timestamp(expected_latest_date) - pd.Timestamp(last_qlib_date)).days, 0)
        if pd.Timestamp(last_qlib_date) < pd.Timestamp(expected_latest_date):
            issues.append(
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
        issues.append(f"No feature rows available for requested_date={requested_date}")
    elif missing_ratio > missing_ratio_threshold:
        issues.append(
            f"Feature missing ratio too high on {requested_date}: {missing_ratio:.2%} > {missing_ratio_threshold:.2%}"
        )

    column_missing_ratio: dict[str, float] = {}
    unusable_required_fields: list[str] = []
    unusable_optional_fields: list[str] = []
    if probe_features.empty:
        issues.append(f"No probe rows available for requested_date={requested_date} using fields={probe_fields}")
    else:
        for field in probe_fields:
            if field not in probe_features.columns:
                column_missing_ratio[field] = 1.0
                if field in required_fields_list:
                    unusable_required_fields.append(field)
                else:
                    unusable_optional_fields.append(field)
                continue
            miss_ratio = float(probe_features[field].isna().mean())
            column_missing_ratio[field] = miss_ratio
            if field in required_fields_list and miss_ratio > required_field_missing_threshold:
                unusable_required_fields.append(field)
            elif field not in required_fields_list and miss_ratio > optional_field_missing_threshold:
                unusable_optional_fields.append(field)

    if unusable_required_fields:
        details = ", ".join(
            f"{field}={column_missing_ratio.get(field, 1.0):.2%}"
            for field in sorted(set(unusable_required_fields))
        )
        issues.append(
            "Required qlib columns unusable "
            f"(missing ratio > {required_field_missing_threshold:.0%}): {details}"
        )

    if unusable_optional_fields:
        details = ", ".join(
            f"{field}={column_missing_ratio.get(field, 1.0):.2%}"
            for field in sorted(set(unusable_optional_fields))
        )
        issues.append(
            "Monitored qlib columns mostly unusable "
            f"(missing ratio > {optional_field_missing_threshold:.0%}): {details}"
        )

    if features.empty and not probe_features.empty:
        issues.append(
            "Requested feature expressions yielded zero rows while probe fields have data; "
            "check expression dependencies and column usability"
        )

    date_ok = expected_latest_date == last_qlib_date if expected_latest_date and last_qlib_date else False

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
