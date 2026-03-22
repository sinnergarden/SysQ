from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

import pandas as pd
from qlib.data import D

from qsys.data.adapter import QlibAdapter
from qsys.data.storage import StockDataStore


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
        if self.issues:
            lines.append("- issues:")
            for issue in self.issues:
                lines.append(f"  - {issue}")
        else:
            lines.append("- issues: none")
        return "\n".join(lines)


def _normalize_date(value: str | pd.Timestamp) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


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

    fields = list(feature_fields)
    features = adapter.get_features(universe, fields, start_time=requested_date, end_time=requested_date)
    if features is None:
        features = pd.DataFrame()

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
        issues=issues,
    )
