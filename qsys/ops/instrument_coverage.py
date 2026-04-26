from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from qlib.data import D

from qsys.data.adapter import QlibAdapter

CORE_FEATURE_FIELDS = ["$close", "$open", "$volume"]
DEFAULT_MIN_ACTIVE_INSTRUMENTS = 50


@dataclass(frozen=True)
class UniverseRegistrySummary:
    universe: str
    instrument_total: int
    active_on_trade_date: int
    stale_end_date_count: int
    latest_end_date: str | None
    oldest_end_date: str | None
    coverage_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "universe": self.universe,
            "instrument_total": self.instrument_total,
            "active_on_trade_date": self.active_on_trade_date,
            "stale_end_date_count": self.stale_end_date_count,
            "latest_end_date": self.latest_end_date,
            "oldest_end_date": self.oldest_end_date,
            "coverage_status": self.coverage_status,
        }


def read_calendar_summary(adapter: QlibAdapter) -> dict[str, Any]:
    cal_path = adapter.qlib_dir / "calendars" / "day.txt"
    if not cal_path.exists():
        return {
            "calendar_first_date": None,
            "calendar_last_date": None,
            "calendar_count": 0,
        }
    df = pd.read_csv(cal_path, header=None, names=["date"])
    if df.empty:
        return {
            "calendar_first_date": None,
            "calendar_last_date": None,
            "calendar_count": 0,
        }
    dates = pd.to_datetime(df["date"], errors="coerce").dropna().sort_values()
    return {
        "calendar_first_date": dates.iloc[0].strftime("%Y-%m-%d") if not dates.empty else None,
        "calendar_last_date": dates.iloc[-1].strftime("%Y-%m-%d") if not dates.empty else None,
        "calendar_count": int(len(dates)),
    }


def read_instrument_file(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["instrument", "start_date", "end_date"])
    df = pd.read_csv(path, sep="\t", header=None, names=["instrument", "start_date", "end_date"])
    df["start_date"] = pd.to_datetime(df["start_date"], errors="coerce")
    df["end_date"] = pd.to_datetime(df["end_date"], errors="coerce")
    return df


def summarize_universe_registry(adapter: QlibAdapter, *, universe: str, trade_date: str) -> UniverseRegistrySummary:
    path = adapter.qlib_dir / "instruments" / f"{universe}.txt"
    df = read_instrument_file(path)
    if df.empty:
        return UniverseRegistrySummary(universe=universe, instrument_total=0, active_on_trade_date=0, stale_end_date_count=0, latest_end_date=None, oldest_end_date=None, coverage_status="missing")
    dt = pd.Timestamp(trade_date)
    active = df[(df["start_date"] <= dt) & (df["end_date"] >= dt)]
    latest_end = df["end_date"].max()
    oldest_end = df["end_date"].min()
    stale_count = int((df["end_date"] < dt).sum())
    status = "ok" if stale_count == 0 else "mismatch"
    return UniverseRegistrySummary(
        universe=universe,
        instrument_total=int(len(df)),
        active_on_trade_date=int(len(active)),
        stale_end_date_count=stale_count,
        latest_end_date=latest_end.strftime("%Y-%m-%d") if pd.notna(latest_end) else None,
        oldest_end_date=oldest_end.strftime("%Y-%m-%d") if pd.notna(oldest_end) else None,
        coverage_status=status,
    )


def _feature_last_date_for_instrument(*, instrument: str, end_date: str, fields: list[str]) -> str | None:
    frame = D.features([instrument], fields, start_time="2010-01-01", end_time=end_date)
    if frame is None or frame.empty:
        return None
    valid = frame.dropna(how="all")
    if valid.empty:
        return None
    if not isinstance(valid.index, pd.MultiIndex):
        return None
    datetimes = valid.index.get_level_values("datetime") if "datetime" in valid.index.names else valid.index.get_level_values(-1)
    return pd.Timestamp(datetimes.max()).strftime("%Y-%m-%d")


def build_instrument_coverage_rows(
    adapter: QlibAdapter,
    *,
    universe: str,
    last_qlib_date: str,
    fields: list[str] | None = None,
) -> list[dict[str, Any]]:
    adapter.init_qlib()
    fields = list(fields or CORE_FEATURE_FIELDS)
    path = adapter.qlib_dir / "instruments" / f"{universe}.txt"
    df = read_instrument_file(path)
    rows: list[dict[str, Any]] = []
    for row in df.itertuples(index=False):
        instrument = str(row.instrument)
        instrument_end = row.end_date.strftime("%Y-%m-%d") if pd.notna(row.end_date) else None
        feature_last_date = _feature_last_date_for_instrument(instrument=instrument, end_date=last_qlib_date, fields=fields)
        has_feature_on_last = bool(feature_last_date is not None and feature_last_date >= last_qlib_date)
        is_active = bool(pd.notna(row.start_date) and pd.notna(row.end_date) and row.start_date <= pd.Timestamp(last_qlib_date) <= row.end_date)
        if has_feature_on_last and not is_active:
            reason = "instrument_end_date_stale_but_feature_available"
        elif not has_feature_on_last and not is_active:
            reason = "feature_rows_missing"
        elif has_feature_on_last and is_active:
            reason = "ok"
        else:
            reason = "inactive_or_missing"
        rows.append(
            {
                "instrument": instrument,
                "instrument_file_end_date": instrument_end,
                "feature_last_date": feature_last_date,
                "has_feature_on_last_qlib_date": has_feature_on_last,
                "is_active_by_instrument_file": is_active,
                "coverage_mismatch_reason": reason,
            }
        )
    return rows


def build_repair_plan(*, universe: str, last_qlib_date: str, coverage_rows: list[dict[str, Any]]) -> dict[str, Any]:
    stale_but_feature_available = [row for row in coverage_rows if row["coverage_mismatch_reason"] == "instrument_end_date_stale_but_feature_available"]
    feature_missing = [row for row in coverage_rows if row["coverage_mismatch_reason"] == "feature_rows_missing"]
    return {
        "universe": universe,
        "last_qlib_date": last_qlib_date,
        "universe_mode": "static_csi300_file_repair" if universe == "csi300" else "existing_registry_file_repair",
        "pit_constituent_accurate": False if universe == "csi300" else None,
        "stale_but_feature_available_count": int(len(stale_but_feature_available)),
        "feature_missing_count": int(len(feature_missing)),
        "stale_but_feature_available": [row["instrument"] for row in stale_but_feature_available],
        "feature_missing": [row["instrument"] for row in feature_missing],
    }


def apply_repair_plan(adapter: QlibAdapter, *, universe: str, last_qlib_date: str, coverage_rows: list[dict[str, Any]]) -> dict[str, Any]:
    path = adapter.qlib_dir / "instruments" / f"{universe}.txt"
    df = read_instrument_file(path)
    repair_targets = {row["instrument"] for row in coverage_rows if row["coverage_mismatch_reason"] == "instrument_end_date_stale_but_feature_available"}
    updated = 0
    if not df.empty and repair_targets:
        mask = df["instrument"].astype(str).isin(repair_targets)
        updated = int(mask.sum())
        df.loc[mask, "end_date"] = pd.Timestamp(last_qlib_date)
        out = df.copy()
        out["start_date"] = out["start_date"].dt.strftime("%Y-%m-%d")
        out["end_date"] = out["end_date"].dt.strftime("%Y-%m-%d")
        out.to_csv(path, sep="\t", header=False, index=False)
    return {
        "universe": universe,
        "applied": True,
        "updated_end_date_count": updated,
        "last_qlib_date": last_qlib_date,
        "repair_targets": sorted(repair_targets),
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
