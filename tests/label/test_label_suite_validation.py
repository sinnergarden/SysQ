from __future__ import annotations

from pathlib import Path
import sys

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


def test_canonical_label_cli_routes_suite_validation(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import qsys.label.validation as validation_module
    import scripts.research.compute_labels as cli

    config_path = tmp_path / "suite.yaml"
    config_path.write_text(
        "label_suite:\n  suite_id: fixture_suite\n",
        encoding="utf-8",
    )
    observed = {}

    class FakePaths:
        def label_suite_manifest(self, suite_id: str) -> Path:
            return tmp_path / "research" / "label_suites" / suite_id / "manifest.json"

    class FakeStore:
        def __init__(self, root: str) -> None:
            self.paths = FakePaths()

    def fake_validate(**kwargs):
        observed.update(kwargs)
        return {"outputs": [{"label_id": "a"}], "status": "passed"}

    monkeypatch.setattr(cli, "LabelStore", FakeStore)
    monkeypatch.setattr(
        validation_module, "validate_executable_label_suite", fake_validate
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compute_labels.py",
            "--config", str(config_path),
            "--research-root", str(tmp_path / "research"),
            "--validate-suite",
            "--data-root", str(tmp_path / "data"),
        ],
    )
    cli.main()
    assert observed["suite_manifest_path"] == (
        tmp_path
        / "research"
        / "label_suites"
        / "fixture_suite"
        / "manifest.json"
    )
    assert observed["output_path"].name == "validation.json"
    assert "Validated 1 labels: passed" in capsys.readouterr().out
