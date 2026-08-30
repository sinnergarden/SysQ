from __future__ import annotations

from pathlib import Path

import pytest

from qsys.label.validation import (
    _date_map,
    _expected_label_ids,
    _resolve_under,
)


def test_expected_label_ids_expand_both_intervals() -> None:
    config = {
        "label_suite": {
            "horizons": [10, 5, 5],
            "primary_label_template": "open_{horizon}",
            "secondary_label_template": "close_{horizon}",
        }
    }
    assert _expected_label_ids(config) == [
        "close_10", "close_5", "open_10", "open_5"
    ]


def test_date_map_uses_prior_session_and_cutoff_bounded_maturity() -> None:
    mapping = _date_map(
        ["2023-12-29", "2024-01-02", "2024-01-03", "2024-01-04"],
        "2024-01-02",
        "2024-01-04",
        2,
    ).set_index("trade_date")
    assert mapping.loc["2024-01-02", "expected_signal_cutoff"] == "2023-12-29"
    assert mapping.loc["2024-01-02", "expected_end"] == "2024-01-04"
    assert mapping.loc["2024-01-03", "expected_end"] is None


def test_resolve_under_rejects_path_escape(tmp_path: Path) -> None:
    assert _resolve_under(tmp_path, "labels/a.parquet") == (
        tmp_path / "labels" / "a.parquet"
    )
    with pytest.raises(ValueError, match="escapes research root"):
        _resolve_under(tmp_path, "../outside.parquet")
