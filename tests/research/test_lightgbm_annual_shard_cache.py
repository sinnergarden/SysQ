from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd
import pytest
from unittest.mock import patch

from qsys.research.generators.lightgbm_single_label import (
    LightGBMSingleLabelGenerator,
)
from scripts.research.preheat_feature_cache import _annual_ranges


def _generator(tmp_path: Path, source: str = "source_v1") -> LightGBMSingleLabelGenerator:
    return LightGBMSingleLabelGenerator(
        feature_list_id="features_v1",
        use_feature_cache=True,
        feature_cache_root=str(tmp_path),
        source_manifest_hash=source,
    )


def _frame(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["trade_date", "instrument", "f1"]).assign(
        f2=lambda df: df["f1"] * 10
    )


def _write_annual(
    generator: LightGBMSingleLabelGenerator,
    frame: pd.DataFrame,
    start: str,
    end: str,
) -> None:
    generator.cache_write_scope = "annual_shard"
    generator._write_cache_frame(frame, start, end, ["f1", "f2"])


def test_annual_ranges_are_full_years_for_clipped_request() -> None:
    assert _annual_ranges("2020-06-01", "2021-06-30") == [
        ("2020-01-01", "2020-12-31"),
        ("2021-01-01", "2021-12-31"),
    ]


def test_preheat_span_years_include_training_history_and_extended_end() -> None:
    ranges = _annual_ranges("2018-03-01", "2026-08-30")
    assert ranges[0] == ("2018-01-01", "2018-12-31")
    assert ranges[-1] == ("2026-01-01", "2026-12-31")


def test_annual_shards_compose_to_exact_window_frame(tmp_path: Path) -> None:
    generator = _generator(tmp_path)
    first = _frame([
        ("2020-06-01", "A", 1.0),
        ("2020-12-31", "B", 2.0),
    ])
    second = _frame([
        ("2021-01-01", "C", 3.0),
        ("2021-06-30", "D", 4.0),
    ])
    _write_annual(generator, first, "2020-01-01", "2020-12-31")
    _write_annual(generator, second, "2021-01-01", "2021-12-31")

    composed = generator._load_annual_shard_cache(
        "2020-06-01", "2021-06-30", ["f1", "f2"]
    )
    expected = pd.concat([first, second], ignore_index=True).reset_index(drop=True)
    assert composed is not None
    pd.testing.assert_frame_equal(composed, expected)


def test_annual_shards_fail_closed_on_duplicate_keys(tmp_path: Path) -> None:
    generator = _generator(tmp_path)
    frame = _frame([
        ("2020-06-01", "A", 1.0),
        ("2020-06-01", "A", 2.0),
    ])
    _write_annual(generator, frame, "2020-01-01", "2020-12-31")

    with pytest.raises(ValueError, match="duplicate instrument/date keys"):
        generator._load_annual_shard_cache(
            "2020-06-01", "2020-06-01", ["f1", "f2"]
        )


def test_compose_matches_direct_qlib_instrument_major_order(tmp_path: Path) -> None:
    generator = _generator(tmp_path)
    # Annual files arrive year-major, while the canonical direct Qlib frame
    # contract is instrument-major with dates ascending within instrument.
    first = _frame([
        ("2020-06-01", "A", 1.0),
        ("2020-06-01", "B", 2.0),
    ])
    second = _frame([
        ("2021-06-01", "A", 3.0),
        ("2021-06-01", "B", 4.0),
    ])
    _write_annual(generator, first, "2020-01-01", "2020-12-31")
    _write_annual(generator, second, "2021-01-01", "2021-12-31")

    composed = generator._load_annual_shard_cache(
        "2020-06-01", "2021-06-01", ["f1", "f2"]
    )
    direct = pd.concat([first, second], ignore_index=True).sort_values(
        ["instrument", "trade_date"], kind="mergesort"
    ).reset_index(drop=True)
    assert composed is not None
    pd.testing.assert_frame_equal(composed, direct)


def test_load_data_uses_annual_compose_before_qlib(tmp_path: Path) -> None:
    generator = _generator(tmp_path)
    _write_annual(
        generator,
        _frame([("2020-06-01", "A", 1.0)]),
        "2020-01-01",
        "2020-12-31",
    )
    _write_annual(
        generator,
        _frame([("2021-01-01", "B", 2.0)]),
        "2021-01-01",
        "2021-12-31",
    )
    with patch(
        "qsys.feature.registry.FeatureListRegistry.load",
        return_value=["f1", "f2"],
    ), patch(
        "qsys.data.adapter.QlibAdapter.get_features",
        side_effect=AssertionError("annual cache should avoid qlib load"),
    ):
        loaded, features = generator._load_data("2020-06-01", "2021-06-30")
    assert features == ["f1", "f2"]
    assert loaded["instrument"].tolist() == ["A", "B"]


def test_missing_annual_shard_does_not_compose_partial_data(tmp_path: Path) -> None:
    generator = _generator(tmp_path)
    _write_annual(
        generator,
        _frame([("2020-06-01", "A", 1.0)]),
        "2020-01-01",
        "2020-12-31",
    )
    assert generator._load_annual_shard_cache(
        "2020-06-01", "2021-06-30", ["f1", "f2"]
    ) is None


def test_wrong_source_shard_is_not_reused(tmp_path: Path) -> None:
    good = _generator(tmp_path, source="source_good")
    bad = _generator(tmp_path, source="source_bad")
    frame = _frame([("2020-06-01", "A", 1.0)])
    _write_annual(bad, frame, "2020-01-01", "2020-12-31")

    # Also exercise the fail-closed metadata check if an old shard is copied
    # into the current identity's expected filename.
    expected_path = good._annual_shard_path("2020-01-01", "2020-12-31", ["f1", "f2"])
    expected_meta = good._annual_shard_meta_path("2020-01-01", "2020-12-31", ["f1", "f2"])
    bad_path = bad._annual_shard_path("2020-01-01", "2020-12-31", ["f1", "f2"])
    bad_meta = bad._annual_shard_meta_path("2020-01-01", "2020-12-31", ["f1", "f2"])
    expected_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(bad_path, expected_path)
    shutil.copyfile(bad_meta, expected_meta)
    assert good._load_annual_shard_cache(
        "2020-06-01", "2020-12-31", ["f1", "f2"]
    ) is None


def test_shard_metadata_binds_data_hash(tmp_path: Path) -> None:
    generator = _generator(tmp_path)
    _write_annual(
        generator,
        _frame([("2020-06-01", "A", 1.0)]),
        "2020-01-01",
        "2020-12-31",
    )
    meta_path = generator._annual_shard_meta_path(
        "2020-01-01", "2020-12-31", ["f1", "f2"]
    )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["data_sha256"] = "0" * 64
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    assert generator._load_annual_shard_cache(
        "2020-06-01", "2020-12-31", ["f1", "f2"]
    ) is None
