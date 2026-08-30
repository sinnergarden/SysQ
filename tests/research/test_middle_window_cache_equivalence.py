from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from scripts.research.verify_middle_window_cache_equivalence import (
    _compare_features,
    _compare_predictions,
)


def _write_predictions(path: Path, raw: list[float], score: list[float]) -> Path:
    pd.DataFrame({
        "trade_date": ["2024-01-02", "2024-01-02"],
        "data_date": ["2024-01-01", "2024-01-01"],
        "instrument": ["000001.SZ", "000002.SZ"],
        "score_model_raw": raw,
        "score": score,
    }).to_parquet(path, index=False)
    return path


def test_prediction_comparison_rejects_missing_and_nonfinite_mismatch(
    tmp_path: Path,
) -> None:
    left = _write_predictions(
        tmp_path / "left.parquet", [np.nan, np.inf], [1.0, 2.0]
    )
    right = _write_predictions(
        tmp_path / "right.parquet", [1.0, -np.inf], [1.0, 2.0]
    )

    comparison = _compare_predictions(left, right)

    assert comparison["status"] == "fail"
    assert comparison["score_model_raw_missing_mask_mismatch"] == 1
    assert comparison["score_model_raw_nonfinite_mismatch"] == 1


def test_feature_comparison_rejects_missing_and_nonfinite_mismatch(
    tmp_path: Path,
) -> None:
    keys = {
        "instrument": ["000001.SZ", "000002.SZ", "000003.SZ"],
        "trade_date": ["2024-01-02"] * 3,
    }
    direct = tmp_path / "direct.parquet"
    cache = tmp_path / "cache.parquet"
    pd.DataFrame({**keys, "$roe": [np.nan, np.inf, 0.025]}).to_parquet(
        direct, index=False
    )
    pd.DataFrame({**keys, "$roe": [1.0, -np.inf, 0.025]}).to_parquet(
        cache, index=False
    )

    comparison = _compare_features(
        direct, cache, ["$roe"], tmp_path / "unused"
    )

    assert comparison["status"] == "fail"
    assert comparison["missing_mask_mismatch_cells"] == 1
    assert comparison["nonfinite_mismatch_cells"] == 1


def test_feature_comparison_is_keyed_not_row_ordered(tmp_path: Path) -> None:
    direct = tmp_path / "direct.parquet"
    cache = tmp_path / "cache.parquet"
    pd.DataFrame({
        "instrument": ["000002.SZ", "000001.SZ"],
        "trade_date": ["2024-01-02", "2024-01-02"],
        "$roe": [0.02, 0.01],
    }).to_parquet(direct, index=False)
    pd.DataFrame({
        "instrument": ["000001.SZ", "000002.SZ"],
        "trade_date": ["2024-01-02", "2024-01-02"],
        "$roe": [0.01, 0.02],
    }).to_parquet(cache, index=False)

    comparison = _compare_features(
        direct, cache, ["$roe"], tmp_path / "unused"
    )

    assert comparison["status"] == "pass"
    assert comparison["finite_tolerance_mismatch_cells"] == 0


def test_feature_comparison_gates_only_rows_consumed_after_pit_filter(
    tmp_path: Path,
) -> None:
    direct = tmp_path / "direct.parquet"
    cache = tmp_path / "cache.parquet"
    keys = {
        "instrument": ["000001.SZ", "000002.SZ"],
        "trade_date": ["2024-01-02", "2024-01-02"],
    }
    pd.DataFrame({**keys, "$roe": [0.01, 0.02]}).to_parquet(
        direct, index=False
    )
    pd.DataFrame({**keys, "$roe": [np.nan, 0.02]}).to_parquet(
        cache, index=False
    )

    loaded = _compare_features(
        direct, cache, ["$roe"], tmp_path / "unused"
    )
    consumed = _compare_features(
        direct,
        cache,
        ["$roe"],
        tmp_path / "unused",
        row_filter=lambda frame: frame[frame["instrument"] == "000002.SZ"],
        comparison_scope="post_pit_membership_consumed_rows",
    )

    assert loaded["status"] == "fail"
    assert loaded["missing_mask_mismatch_cells"] == 1
    assert consumed["status"] == "pass"
    assert consumed["comparison_scope"] == "post_pit_membership_consumed_rows"
    assert consumed["loaded_direct_rows"] == 2
    assert consumed["direct_rows"] == 1
    assert consumed["missing_mask_mismatch_cells"] == 0


def test_feature_comparison_accepts_only_bounded_float_roundoff(
    tmp_path: Path,
) -> None:
    keys = {
        "instrument": ["000001.SZ", "000002.SZ"],
        "trade_date": ["2024-01-02", "2024-01-02"],
    }
    direct = tmp_path / "direct.parquet"
    cache = tmp_path / "cache.parquet"
    pd.DataFrame({**keys, "$roe": [1.0, 1.0]}).to_parquet(
        direct, index=False
    )
    pd.DataFrame({**keys, "$roe": [1.0 + 5e-10, 1.0 + 2e-9]}).to_parquet(
        cache, index=False
    )

    comparison = _compare_features(
        direct, cache, ["$roe"], tmp_path / "unused"
    )

    assert comparison["status"] == "fail"
    assert comparison["absolute_tolerance"] == 1e-9
    assert comparison["finite_exact_mismatch_cells"] == 2
    assert comparison["finite_tolerance_mismatch_cells"] == 1
