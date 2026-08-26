from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from qsys.data._merge_helpers import (
    FINANCIAL_AVAILABILITY_CONTRACT,
    FINANCIAL_AVAILABILITY_RULE,
)
from qsys.data.income_sidecar import (
    INCOME_SIDECAR_SCHEMA,
    INCOME_SIDECAR_TRANSFORM,
)
from qsys.data.source_audit import stable_scope_hash
from qsys.feature.groups.growth_confirmation_v0 import (
    _compute_quarterly_features,
    _latest_mature_feature_events,
    _pit_merge,
    _rolling_max_available_from,
    build_growth_confirmation_features,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _identity_sha(payload: dict[str, object]) -> str:
    content = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _quarterly_rows(*, symbol: str = "000001.SZ") -> pd.DataFrame:
    periods = pd.date_range("2019-03-31", periods=8, freq="QE")
    rows: list[dict[str, object]] = []
    for index, end_date in enumerate(periods):
        quarter = end_date.quarter
        year_growth = 1.0 if end_date.year == 2019 else 1.2
        cumulative_revenue = 100.0 * quarter * year_growth
        availability = end_date + pd.Timedelta(days=30)
        rows.append({
            "ts_code": symbol,
            "ann_date": availability.strftime("%Y%m%d"),
            "f_ann_date": availability.strftime("%Y%m%d"),
            "publication_date": availability.strftime("%Y%m%d"),
            "availability_date": availability.strftime("%Y%m%d"),
            "end_date": end_date.strftime("%Y%m%d"),
            "report_type": "1",
            "comp_type": "1",
            "end_type": str(quarter),
            "update_flag": "0",
            "n_income": cumulative_revenue * 0.1,
            "revenue": cumulative_revenue,
            "oper_cost": cumulative_revenue * 0.6,
            "source_run_id": "run-income",
            "source_receipt_id": f"receipt-{index}",
            "source_payload_sha256": "a" * 64,
        })
    frame = pd.DataFrame(rows)
    for column in ("ann_date", "publication_date", "availability_date", "end_date"):
        frame[column] = pd.to_datetime(frame[column])
    return frame


def _write_sidecar(tmp_path: Path) -> dict[str, str]:
    frame = _quarterly_rows()
    artifact = tmp_path / "income.parquet"
    frame.to_parquet(artifact, index=False)
    artifact_sha = _sha256(artifact)
    symbols = ["000001.SZ"]
    immutable_identity = {
        "schema": INCOME_SIDECAR_SCHEMA,
        "transform_contract": INCOME_SIDECAR_TRANSFORM,
        "financial_availability_contract": FINANCIAL_AVAILABILITY_CONTRACT,
        "financial_availability_rule": FINANCIAL_AVAILABILITY_RULE,
        "source": "tushare",
        "endpoint": "income",
        "source_run_id": "run-income",
        "terminal_receipt_sha256": "c" * 64,
        "scope_key": "csi1800",
        "range_start": "20180101",
        "range_end": "20211231",
        "availability_cutoff": "20211231",
        "symbol_count": 1,
        "symbols_sha256": stable_scope_hash(symbols),
        "source_receipts": [],
    }
    manifest = {
        "schema_version": 1,
        "artifact_type": INCOME_SIDECAR_SCHEMA,
        "artifact_id": _identity_sha(immutable_identity),
        "identity": immutable_identity,
        "artifact": {
            "path": artifact.name,
            "sha256": artifact_sha,
            "rows": len(frame),
            "columns": list(frame.columns),
        },
        "scope": {
            "scope_key": "csi1800",
            "range_start": "20180101",
            "range_end": "20211231",
            "availability_cutoff": "20211231",
            "symbol_count": 1,
            "symbols_sha256": stable_scope_hash(symbols),
            "symbols": symbols,
        },
        "contracts": {
            "transform": INCOME_SIDECAR_TRANSFORM,
            "financial_availability": FINANCIAL_AVAILABILITY_CONTRACT,
            "availability_rule": FINANCIAL_AVAILABILITY_RULE,
        },
        "source_evidence": {
            "run_id": "run-income",
            "terminal_receipt_sha256": "c" * 64,
        },
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True), encoding="utf-8"
    )
    return {
        "income_sidecar_path": str(artifact),
        "income_sidecar_sha256": artifact_sha,
        "income_sidecar_manifest_path": str(manifest_path),
        "income_sidecar_manifest_sha256": _sha256(manifest_path),
    }


def test_quarterly_formula_is_unchanged_and_dependency_delay_propagates() -> None:
    income = _quarterly_rows()
    # The prior-year Q1 was observed late.  2020-Q1 single-quarter YoY must not
    # mature until that actually used denominator is available.
    income.loc[income["end_date"].eq(pd.Timestamp("2019-03-31")), "availability_date"] = (
        pd.Timestamp("2020-05-15")
    )

    quarterly = _compute_quarterly_features(income)
    q1_2020 = quarterly.loc[
        quarterly["end_date"].eq(pd.Timestamp("2020-03-31"))
    ].iloc[0]

    assert q1_2020["single_q_revenue_yoy"] == pytest.approx(0.2)
    assert q1_2020["single_q_revenue_yoy_available_from"] == pd.Timestamp(
        "2020-05-15"
    )
    q4_2020 = quarterly.loc[
        quarterly["end_date"].eq(pd.Timestamp("2020-12-31"))
    ].iloc[0]
    assert q4_2020["single_q_revenue_yoy"] == pytest.approx(0.2)
    assert q4_2020["ttm_revenue_yoy"] == pytest.approx(0.2)
    assert q4_2020["is_profitable_ttm"] == 1.0
    assert q4_2020["gross_margin_delta_yoy"] == pytest.approx(0.0)


def test_rolling_availability_preserves_nat_and_never_becomes_ancient() -> None:
    values = pd.Series(pd.to_datetime([None, "2020-02-01", "2020-03-01"]))
    result = _rolling_max_available_from(
        values, pd.Series(["A", "A", "A"]), window=2
    )

    assert result.iloc[:2].isna().all()
    assert result.iloc[2] == pd.Timestamp("2020-03-01")


def test_feature_events_nan_overwrites_and_period_never_regresses() -> None:
    quarterly = pd.DataFrame({
        "ts_code": ["A"] * 5,
        "end_date": pd.to_datetime([
            "2020-12-31", "2021-03-31", "2020-09-30",
            "2021-06-30", "2021-09-30",
        ]),
        "single_q_revenue_yoy": [1.0, np.nan, 9.0, 2.0, 3.0],
        "single_q_revenue_yoy_available_from": pd.to_datetime([
            "2021-04-01", "2021-04-05", "2021-04-10",
            "2021-07-01", "2021-07-01",
        ]),
    })

    events = _latest_mature_feature_events(
        quarterly, "single_q_revenue_yoy"
    )

    # Newer Q1 has a real NaN feature event and therefore clears the old value.
    assert list(events["_ann_dt"].dt.strftime("%Y%m%d")) == [
        "20210401", "20210405", "20210701",
    ]
    assert pd.isna(events.iloc[1]["single_q_revenue_yoy"])
    # The late 2020-Q3 report cannot regress the mature period, and an exact
    # availability tie deterministically selects the largest end_date.
    assert events.iloc[-1]["end_date"] == pd.Timestamp("2021-09-30")
    assert events.iloc[-1]["single_q_revenue_yoy"] == 3.0

    merged = _pit_merge(
        pd.DataFrame({
            "ts_code": ["A", "A"],
            "_dt": pd.to_datetime(["2021-04-04", "2021-04-06"]),
        }),
        events[["ts_code", "_ann_dt", "single_q_revenue_yoy"]],
    )
    assert merged.iloc[0]["single_q_revenue_yoy"] == 1.0
    assert pd.isna(merged.iloc[1]["single_q_revenue_yoy"])


def test_growth_requires_explicit_identity_and_consumes_real_sidecar(
    tmp_path: Path,
) -> None:
    daily = pd.DataFrame({
        "ts_code": ["000001.SZ", "000001.SZ"],
        "trade_date": ["2020-05-01", "2020-05-02"],
        "close": [10.0, 10.1],
    })
    with pytest.raises(ValueError, match="explicit audited income sidecar identity"):
        build_growth_confirmation_features(daily)

    identity = _write_sidecar(tmp_path)
    result = build_growth_confirmation_features(
        daily,
        **identity,
        income_sidecar_required_start="20200501",
        income_sidecar_required_end="20200502",
    )

    assert set(
        [
            "single_q_revenue_yoy", "ttm_revenue_yoy",
            "is_profitable_ttm", "gross_margin_delta_yoy",
        ]
    ).issubset(result.columns)


def test_growth_rejects_tampered_manifest(tmp_path: Path) -> None:
    identity = _write_sidecar(tmp_path)
    Path(identity["income_sidecar_manifest_path"]).write_text(
        "{}", encoding="utf-8"
    )
    daily = pd.DataFrame({
        "ts_code": ["000001.SZ"],
        "trade_date": ["2020-05-01"],
        "close": [10.0],
    })
    with pytest.raises(RuntimeError, match="manifest sha256 mismatch"):
        build_growth_confirmation_features(daily, **identity)
