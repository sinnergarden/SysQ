from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.research.readiness import EXTENDED_BLOCKED, EXTENDED_WARN

COMMON_BAD_FIELD_CANDIDATES = [
    "market_breadth",
    "limit_up_breadth",
    "small_vs_large_strength",
    "growth_vs_value_proxy",
    "ps_ttm",
]


@dataclass(frozen=True)
class BadFieldDiagnostics:
    object_name: str
    coverage: pd.DataFrame


def load_coverage_csv(path: str | Path) -> pd.DataFrame:
    return pd.read_csv(path)


def build_bad_field_diagnostics(
    *,
    coverage_254: pd.DataFrame,
    coverage_254_absnorm: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    merged = _merge_coverages(coverage_254, coverage_254_absnorm)
    bad_254 = _build_object_bad_fields(
        object_name="feature_254",
        current=merged,
        other_prefix="absnorm",
        current_prefix="254",
    )
    bad_absnorm = _build_object_bad_fields(
        object_name="feature_254_absnorm",
        current=merged,
        other_prefix="254",
        current_prefix="absnorm",
    )

    trim_fields_254 = _recommend_trim_fields(bad_254)
    trim_fields_absnorm = _recommend_trim_fields(bad_absnorm)
    proposal = {
        "object_names": ["feature_254", "feature_254_absnorm"],
        "candidate_bad_fields": COMMON_BAD_FIELD_CANDIDATES,
        "feature_254": {
            "fields_to_trim": trim_fields_254,
            "fields_to_keep_as_warn": [row["field_name"] for _, row in bad_254.iterrows() if row["trim_recommendation"] == "keep_as_warn"],
        },
        "feature_254_absnorm": {
            "fields_to_trim": trim_fields_absnorm,
            "fields_to_keep_as_warn": [row["field_name"] for _, row in bad_absnorm.iterrows() if row["trim_recommendation"] == "keep_as_warn"],
        },
        "trimmed_objects": {
            "feature_254_trimmed": {
                "source": "feature_254",
                "excluded_fields": trim_fields_254,
            },
            "feature_254_absnorm_trimmed": {
                "source": "feature_254_absnorm",
                "excluded_fields": trim_fields_absnorm,
            },
        },
    }
    return bad_254, bad_absnorm, proposal


def write_trimmed_diagnostics(
    *,
    output_dir: str | Path,
    bad_254: pd.DataFrame,
    bad_absnorm: pd.DataFrame,
    proposal: dict[str, Any],
) -> dict[str, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    bad_254_path = out / "feature_254_bad_fields.csv"
    bad_absnorm_path = out / "feature_254_absnorm_bad_fields.csv"
    proposal_path = out / "trimmed_proposal.json"
    bad_254.to_csv(bad_254_path, index=False)
    bad_absnorm.to_csv(bad_absnorm_path, index=False)
    proposal_path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "feature_254_bad_fields": str(bad_254_path),
        "feature_254_absnorm_bad_fields": str(bad_absnorm_path),
        "trimmed_proposal": str(proposal_path),
    }


def _merge_coverages(coverage_254: pd.DataFrame, coverage_254_absnorm: pd.DataFrame) -> pd.DataFrame:
    left = coverage_254[
        [
            "field_name",
            "coverage_ratio",
            "constant_ratio",
            "zero_ratio",
            "degradation_level",
            "usable_for_train",
            "notes",
        ]
    ].rename(
        columns={
            "coverage_ratio": "coverage_ratio_254",
            "constant_ratio": "constant_ratio_254",
            "zero_ratio": "zero_ratio_254",
            "degradation_level": "degradation_level_254",
            "usable_for_train": "usable_for_train_254",
            "notes": "notes_254",
        }
    )
    right = coverage_254_absnorm[
        [
            "field_name",
            "coverage_ratio",
            "constant_ratio",
            "zero_ratio",
            "degradation_level",
            "usable_for_train",
            "notes",
        ]
    ].rename(
        columns={
            "coverage_ratio": "coverage_ratio_absnorm",
            "constant_ratio": "constant_ratio_absnorm",
            "zero_ratio": "zero_ratio_absnorm",
            "degradation_level": "degradation_level_absnorm",
            "usable_for_train": "usable_for_train_absnorm",
            "notes": "notes_absnorm",
        }
    )
    return left.merge(right, on="field_name", how="outer")


def _build_object_bad_fields(*, object_name: str, current: pd.DataFrame, other_prefix: str, current_prefix: str) -> pd.DataFrame:
    current_coverage = f"coverage_ratio_{current_prefix}"
    current_constant = f"constant_ratio_{current_prefix}"
    current_zero = f"zero_ratio_{current_prefix}"
    current_degrade = f"degradation_level_{current_prefix}"
    current_usable = f"usable_for_train_{current_prefix}"
    current_notes = f"notes_{current_prefix}"
    other_coverage = f"coverage_ratio_{other_prefix}"
    other_constant = f"constant_ratio_{other_prefix}"
    other_zero = f"zero_ratio_{other_prefix}"
    other_degrade = f"degradation_level_{other_prefix}"

    rows: list[dict[str, Any]] = []
    frame = current.copy()
    for _, row in frame.iterrows():
        coverage = row.get(current_coverage)
        constant = row.get(current_constant)
        zero = row.get(current_zero)
        degrade = row.get(current_degrade)
        usable = row.get(current_usable)
        if pd.isna(coverage) and pd.isna(constant) and pd.isna(zero):
            continue
        is_bad = (
            (pd.notna(coverage) and float(coverage) < 0.7)
            or (pd.notna(constant) and float(constant) >= 0.95)
            or (pd.notna(zero) and float(zero) >= 0.98)
            or str(degrade) in {EXTENDED_WARN, EXTENDED_BLOCKED}
            or not bool(usable)
        )
        if not is_bad:
            continue
        other_cov = row.get(other_coverage)
        other_const = row.get(other_constant)
        other_zero = row.get(other_zero)
        other_degr = row.get(other_degrade)
        absnorm_improved = _is_improved(
            base_coverage=coverage,
            base_constant=constant,
            base_zero=zero,
            other_coverage=other_cov,
            other_constant=other_const,
            other_zero=other_zero,
            other_degradation=other_degr,
        )
        trim_recommendation = _trim_recommendation(
            field_name=str(row.get("field_name")),
            coverage=coverage,
            constant=constant,
            zero=zero,
            absnorm_improved=absnorm_improved,
        )
        rows.append(
            {
                "field_name": row.get("field_name"),
                "in_feature_254": pd.notna(row.get("coverage_ratio_254")),
                "in_feature_254_absnorm": pd.notna(row.get("coverage_ratio_absnorm")),
                "coverage_ratio": _to_float(coverage),
                "constant_ratio": _to_float(constant),
                "zero_ratio": _to_float(zero),
                "degradation_level": degrade,
                "absnorm_improved": absnorm_improved,
                "trim_recommendation": trim_recommendation,
                "reason": _reason_text(
                    coverage=coverage,
                    constant=constant,
                    zero=zero,
                    absnorm_improved=absnorm_improved,
                    notes=row.get(current_notes),
                ),
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["trim_recommendation", "coverage_ratio", "constant_ratio"], ascending=[True, True, False])
    return df.reset_index(drop=True)


def _recommend_trim_fields(df: pd.DataFrame) -> list[str]:
    return df[df["trim_recommendation"] == "remove_from_trimmed"]["field_name"].dropna().astype(str).tolist()


def _trim_recommendation(*, field_name: str, coverage: Any, constant: Any, zero: Any, absnorm_improved: bool) -> str:
    if field_name in COMMON_BAD_FIELD_CANDIDATES:
        return "remove_from_trimmed"
    if pd.notna(coverage) and float(coverage) < 0.4:
        return "remove_from_trimmed"
    if pd.notna(constant) and float(constant) >= 0.98:
        return "remove_from_trimmed" if not absnorm_improved else "keep_as_warn"
    if pd.notna(zero) and float(zero) >= 0.98:
        return "keep_as_warn"
    return "keep_as_warn"


def _is_improved(*, base_coverage: Any, base_constant: Any, base_zero: Any, other_coverage: Any, other_constant: Any, other_zero: Any, other_degradation: Any) -> bool:
    improved = False
    if pd.notna(base_coverage) and pd.notna(other_coverage) and float(other_coverage) > float(base_coverage):
        improved = True
    if pd.notna(base_constant) and pd.notna(other_constant) and float(other_constant) < float(base_constant):
        improved = True
    if pd.notna(base_zero) and pd.notna(other_zero) and float(other_zero) < float(base_zero):
        improved = True
    return improved


def _reason_text(*, coverage: Any, constant: Any, zero: Any, absnorm_improved: bool, notes: Any) -> str:
    parts: list[str] = []
    if pd.notna(coverage) and float(coverage) < 0.7:
        parts.append("high_missing")
    if pd.notna(constant) and float(constant) >= 0.95:
        parts.append("near_constant")
    if pd.notna(zero) and float(zero) >= 0.98:
        parts.append("high_zero")
    if absnorm_improved:
        parts.append("absnorm_improved")
    if notes:
        parts.append(str(notes))
    return ";".join(parts) if parts else "ok"


def _to_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), 8)
