"""Feature-source freshness contracts shared by training and inference."""

from __future__ import annotations

from typing import Any

import pandas as pd


SHAREHOLDER_STALE_FEATURES = (
    "holder_num_stale_days",
    "top10_holder_stale_days",
)


def normalise_shareholder_freshness(value: Any) -> dict[str, Any]:
    """Validate and normalise the financial_rc shareholder freshness policy."""

    if not isinstance(value, dict):
        raise ValueError("feature_freshness.shareholder must be a mapping")
    raw_features = value.get("features")
    if not isinstance(raw_features, dict):
        raise ValueError(
            "feature_freshness.shareholder.features must be a mapping"
        )
    features: dict[str, dict[str, int]] = {}
    for feature in SHAREHOLDER_STALE_FEATURES:
        raw = raw_features.get(feature)
        if not isinstance(raw, dict):
            raise ValueError(
                f"feature_freshness.shareholder.features.{feature} must be a mapping"
            )
        try:
            max_median_days = int(raw["max_median_days"])
            max_row_days = int(raw["max_row_days"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"freshness limits for {feature} must be positive integers"
            ) from exc
        if (
            isinstance(raw.get("max_median_days"), bool)
            or isinstance(raw.get("max_row_days"), bool)
            or max_median_days <= 0
            or max_row_days < max_median_days
        ):
            raise ValueError(
                f"freshness limits for {feature} require "
                "0 < max_median_days <= max_row_days"
            )
        features[feature] = {
            "max_median_days": max_median_days,
            "max_row_days": max_row_days,
        }
    try:
        min_coverage = float(value["min_coverage"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "feature_freshness.shareholder.min_coverage must be numeric"
        ) from exc
    if not 0.0 < min_coverage <= 1.0:
        raise ValueError(
            "feature_freshness.shareholder.min_coverage must be in (0, 1]"
        )
    return {
        "source": str(
            value.get("source")
            or "tushare.stk_holdernumber+tushare.top10_holders"
        ),
        "availability_rule": "announcement_date_asof",
        "min_coverage": min_coverage,
        "features": features,
    }


def profile_shareholder_feature_freshness(
    frame: pd.DataFrame,
    contract: dict[str, Any],
    *,
    date_column: str | None = None,
) -> dict[str, Any]:
    """Profile stale-day features and return fail-closed violations.

    For a one-day inference snapshot the ordinary median is checked.  For a
    multi-date training frame, the maximum daily cross-sectional median is
    checked so a source outage cannot be hidden by older healthy rows.
    """

    results: dict[str, Any] = {}
    violations: list[str] = []
    for feature, limits in contract["features"].items():
        if feature not in frame.columns:
            results[feature] = {"coverage": 0.0}
            violations.append(f"missing freshness feature={feature}")
            continue
        values = pd.to_numeric(frame[feature], errors="coerce")
        finite = values.dropna()
        coverage = float(values.notna().mean()) if len(values) else 0.0
        profile: dict[str, Any] = {
            "coverage": round(coverage, 6),
            "median_days": (
                round(float(finite.median()), 6) if not finite.empty else None
            ),
            "p95_days": (
                round(float(finite.quantile(0.95)), 6) if not finite.empty else None
            ),
            "max_days": round(float(finite.max()), 6) if not finite.empty else None,
            **limits,
        }
        observed_median = profile["median_days"]
        if date_column and date_column in frame.columns:
            grouped = pd.DataFrame(
                {"date": frame[date_column], "value": values}
            ).groupby("date", sort=True)["value"]
            daily_medians = grouped.median().dropna()
            profile["max_daily_median_days"] = (
                round(float(daily_medians.max()), 6)
                if not daily_medians.empty
                else None
            )
            observed_median = profile["max_daily_median_days"]
        if coverage < contract["min_coverage"]:
            violations.append(
                f"{feature} coverage={coverage:.2%} below "
                f"{contract['min_coverage']:.2%}"
            )
        if observed_median is None or observed_median > limits["max_median_days"]:
            violations.append(
                f"{feature} median_stale_days={observed_median} exceeds "
                f"{limits['max_median_days']}"
            )
        results[feature] = profile
    return {
        "status": "pass" if not violations else "fail",
        "source": contract["source"],
        "availability_rule": contract["availability_rule"],
        "min_coverage": contract["min_coverage"],
        "features": results,
        "violations": violations,
    }


def shareholder_row_freshness_reasons(
    row: pd.Series, contract: dict[str, Any]
) -> list[str]:
    """Return explicit eligibility reasons for stale/missing shareholder rows."""

    reasons: list[str] = []
    for feature, limits in contract["features"].items():
        value = pd.to_numeric(pd.Series([row.get(feature)]), errors="coerce").iloc[0]
        if pd.isna(value):
            reasons.append(f"missing_{feature}")
        elif float(value) > limits["max_row_days"]:
            reasons.append(f"stale_{feature}")
    return reasons
