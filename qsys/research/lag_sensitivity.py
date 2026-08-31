"""Availability-lag sensitivity for provisional PIT feature research."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def _finite(value: Any) -> float | None:
    if value is None or not pd.notna(value):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _mean(values: pd.Series) -> float | None:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    return _finite(clean.mean()) if not clean.empty else None


def _ir(values: pd.Series) -> float | None:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if len(clean) < 2:
        return None
    std = float(clean.std(ddof=1))
    return _finite(clean.mean() / std) if std > 1e-12 else None


class AvailabilityLagSensitivity:
    """Delay an aligned feature snapshot on the global open-session calendar.

    Directions come from the base-lag Stage-A discovery split and remain fixed
    for every delayed view.  The configured holdout is never loaded here.
    """

    def __init__(
        self,
        *,
        feature_frame: pd.DataFrame,
        features: list[str],
        label_frame: pd.DataFrame,
        label_id: str,
        locked_directions: dict[str, int],
        calendar_dates: list[str],
        config: dict[str, Any],
        output_dir: Path,
    ) -> None:
        self.frame = feature_frame.copy()
        self.features = list(features)
        self.label = label_frame.copy()
        self.label_id = str(label_id)
        self.directions = {str(key): int(value) for key, value in locked_directions.items()}
        self.calendar = [str(value)[:10] for value in calendar_dates]
        self.cfg = config
        self.output_dir = Path(output_dir)
        self.base_lag = int(config.get("base_lag_sessions", 1))
        self.lags = tuple(int(value) for value in config.get("lags_sessions", [1, 5, 20]))
        self.min_count = int(config.get("min_count", 30))
        self._validate()

    def _validate(self) -> None:
        if (
            not self.lags
            or tuple(sorted(set(self.lags))) != self.lags
            or self.base_lag not in self.lags
            or self.base_lag < 0
            or any(value < self.base_lag for value in self.lags)
        ):
            raise ValueError("availability lags must be unique, sorted and include base lag")
        if self.min_count < 2:
            raise ValueError("availability-lag min_count must be at least two")
        required = {"trade_date", "instrument", *self.features}
        missing = sorted(required - set(self.frame.columns))
        if missing:
            raise ValueError(f"availability-lag feature frame missing columns: {missing}")
        if self.frame.duplicated(["trade_date", "instrument"]).any():
            raise ValueError("availability-lag feature keys must be unique")
        if self.label.duplicated(["trade_date", "instrument"]).any():
            raise ValueError("availability-lag label keys must be unique")
        if sorted(self.directions) != sorted(self.features) or not set(
            self.directions.values()
        ).issubset({-1, 1}):
            raise ValueError("availability-lag directions must cover every feature")
        if len(set(self.calendar)) != len(self.calendar) or self.calendar != sorted(
            self.calendar
        ):
            raise ValueError("availability-lag calendar must be sorted and unique")
        observed = set(self.frame["trade_date"].astype(str).str[:10])
        if not observed.issubset(set(self.calendar)):
            raise ValueError("availability-lag frame contains dates outside the calendar")

    def _shifted(self, lag: int) -> pd.DataFrame:
        offset = lag - self.base_lag
        calendar_index = {value: index for index, value in enumerate(self.calendar)}
        target = {
            value: self.calendar[index + offset]
            for value, index in calendar_index.items()
            if index + offset < len(self.calendar)
        }
        shifted = self.frame[["trade_date", "instrument", *self.features]].copy()
        shifted["trade_date"] = shifted["trade_date"].astype(str).str[:10]
        shifted["source_trade_date"] = shifted["trade_date"]
        shifted["trade_date"] = shifted["trade_date"].map(target)
        shifted = shifted.dropna(subset=["trade_date"])
        shifted["elapsed_calendar_days"] = (
            pd.to_datetime(shifted["trade_date"])
            - pd.to_datetime(shifted["source_trade_date"])
        ).dt.days
        for feature in self.features:
            shifted[feature] = pd.to_numeric(shifted[feature], errors="coerce")
            if feature.endswith("_stale_days") and offset:
                shifted[feature] = shifted[feature] + shifted["elapsed_calendar_days"]
        eligible = self.frame[["trade_date", "instrument"]].copy()
        eligible["trade_date"] = eligible["trade_date"].astype(str).str[:10]
        return shifted.merge(
            eligible,
            on=["trade_date", "instrument"],
            how="inner",
            validate="one_to_one",
        )

    def _daily_rank_ic(self, shifted: pd.DataFrame, feature: str) -> pd.DataFrame:
        joined = shifted[["trade_date", "instrument", feature]].merge(
            self.label[["trade_date", "instrument", "label_value"]],
            on=["trade_date", "instrument"],
            how="inner",
            validate="one_to_one",
        )
        joined["score"] = pd.to_numeric(joined[feature], errors="coerce") * self.directions[feature]
        joined["label_value"] = pd.to_numeric(joined["label_value"], errors="coerce")
        joined = joined.dropna(subset=["score", "label_value"])
        rows = []
        for trade_date, day in joined.groupby("trade_date", sort=True):
            if len(day) < self.min_count or day["score"].nunique() < 2:
                continue
            value = day["score"].corr(day["label_value"], method="spearman")
            if pd.notna(value):
                rows.append({"trade_date": trade_date, "rank_ic": float(value)})
        return pd.DataFrame(rows, columns=["trade_date", "rank_ic"])

    def run(self) -> dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        eligible = self.frame[["trade_date", "instrument"]].copy()
        eligible["trade_date"] = eligible["trade_date"].astype(str).str[:10]
        eligible["year"] = eligible["trade_date"].str[:4]
        years = sorted(eligible["year"].unique())
        eligible_by_year = eligible.groupby("year").size().to_dict()
        eligible_total = int(len(eligible))

        summary_rows: list[dict[str, Any]] = []
        coverage_rows: list[dict[str, Any]] = []
        stale_rows: list[dict[str, Any]] = []
        yearly_ic_rows: list[dict[str, Any]] = []
        base_rank_ic: dict[str, float | None] = {}

        for lag in self.lags:
            shifted = self._shifted(lag)
            shifted["year"] = shifted["trade_date"].str[:4]
            for feature in self.features:
                available = shifted[feature].notna()
                available_count = int(available.sum())
                coverage = available_count / eligible_total if eligible_total else None
                for year in years:
                    year_values = shifted.loc[shifted["year"].eq(year), feature]
                    year_available = int(year_values.notna().sum())
                    denominator = int(eligible_by_year.get(year, 0))
                    coverage_rows.append({
                        "lag_sessions": lag,
                        "feature": feature,
                        "year": int(year),
                        "eligible_count": denominator,
                        "available_count": year_available,
                        "coverage": year_available / denominator if denominator else None,
                        "missing_rate": 1.0 - year_available / denominator if denominator else None,
                    })

                daily_ic = self._daily_rank_ic(shifted, feature)
                daily_ic["year"] = (
                    daily_ic["trade_date"].str[:4] if not daily_ic.empty else pd.Series(dtype=str)
                )
                yearly_means: list[float] = []
                for year in years:
                    values = daily_ic.loc[daily_ic["year"].eq(year), "rank_ic"]
                    mean = _mean(values)
                    if mean is not None:
                        yearly_means.append(mean)
                    yearly_ic_rows.append({
                        "lag_sessions": lag,
                        "feature": feature,
                        "locked_direction": self.directions[feature],
                        "year": int(year),
                        "n_days": int(pd.to_numeric(values, errors="coerce").notna().sum()),
                        "rank_ic_mean": mean,
                        "rank_icir": _ir(values),
                        "positive_day_ratio": (
                            float((pd.to_numeric(values, errors="coerce") > 0).mean())
                            if len(values) else None
                        ),
                    })
                rank_ic_mean = _mean(daily_ic["rank_ic"])
                if lag == self.base_lag:
                    base_rank_ic[feature] = rank_ic_mean
                reference = base_rank_ic.get(feature)
                summary_rows.append({
                    "lag_sessions": lag,
                    "feature": feature,
                    "locked_direction": self.directions[feature],
                    "eligible_count": eligible_total,
                    "available_count": available_count,
                    "coverage": coverage,
                    "missing_rate": 1.0 - coverage if coverage is not None else None,
                    "n_rank_ic_days": int(len(daily_ic)),
                    "rank_ic_mean": rank_ic_mean,
                    "rank_icir": _ir(daily_ic["rank_ic"]),
                    "year_count": len(yearly_means),
                    "positive_year_ratio": (
                        float(np.mean(np.asarray(yearly_means) > 0))
                        if yearly_means else None
                    ),
                    "rank_ic_ratio_to_base_lag": (
                        rank_ic_mean / reference
                        if rank_ic_mean is not None and reference not in (None, 0.0)
                        else None
                    ),
                })

                if feature.endswith("_stale_days"):
                    values = pd.to_numeric(shifted.loc[available, feature], errors="coerce").dropna()
                    stale_rows.append({
                        "lag_sessions": lag,
                        "feature": feature,
                        "year": "all",
                        "available_count": int(len(values)),
                        "median_days": _finite(values.median()) if not values.empty else None,
                        "p95_days": _finite(values.quantile(0.95)) if not values.empty else None,
                        "max_days": _finite(values.max()) if not values.empty else None,
                    })
                    for year in years:
                        values = pd.to_numeric(
                            shifted.loc[available & shifted["year"].eq(year), feature],
                            errors="coerce",
                        ).dropna()
                        stale_rows.append({
                            "lag_sessions": lag,
                            "feature": feature,
                            "year": year,
                            "available_count": int(len(values)),
                            "median_days": _finite(values.median()) if not values.empty else None,
                            "p95_days": _finite(values.quantile(0.95)) if not values.empty else None,
                            "max_days": _finite(values.max()) if not values.empty else None,
                        })

        summary = pd.DataFrame(summary_rows)
        coverage_yearly = pd.DataFrame(coverage_rows)
        stale = pd.DataFrame(stale_rows, columns=[
            "lag_sessions", "feature", "year", "available_count",
            "median_days", "p95_days", "max_days",
        ])
        yearly_ic = pd.DataFrame(yearly_ic_rows)
        summary.to_csv(self.output_dir / "availability_lag_summary.csv", index=False)
        coverage_yearly.to_csv(
            self.output_dir / "availability_lag_coverage_yearly.csv", index=False
        )
        stale.to_csv(self.output_dir / "availability_lag_stale_days.csv", index=False)
        yearly_ic.to_csv(
            self.output_dir / "availability_lag_yearly_rank_ic.csv", index=False
        )
        protocol = {
            "schema_version": "availability_lag_sensitivity_v1",
            "contract": "global_open_session_delay_locked_base_direction_v1",
            "base_lag_sessions": self.base_lag,
            "lags_sessions": list(self.lags),
            "shift_offsets_sessions": {
                str(value): value - self.base_lag for value in self.lags
            },
            "stale_days_adjustment": "add_calendar_days_after_base_execution",
            "label_id": self.label_id,
            "feature_count": len(self.features),
            "year_count": len(years),
            "loaded_data_end": str(self.frame["trade_date"].max()),
            "holdout_consumed": False,
        }
        (self.output_dir / "availability_lag_protocol.json").write_text(
            json.dumps(protocol, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return protocol
