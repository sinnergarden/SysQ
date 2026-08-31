"""Tests for SignalAnalytics — DuckDB-powered cross-signal queries."""
from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd
import pytest

from qsys.research.signal_analytics import SignalAnalytics


def _populate_research_root(tmp_path: Path) -> None:
    """Create fixture signal + label parquet files."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    # ── Signals ──
    sig_run_dir = tmp_path / "signals" / "sig_a" / "run_001"
    sig_run_dir.mkdir(parents=True)
    dates = ["2026-01-05", "2026-01-06", "2026-01-07"]
    insts = [f"000{i:04d}.SZ" for i in range(20)]
    rows_sig = []
    for td in dates:
        for inst in insts:
            rows_sig.append({
                "trade_date": td, "data_date": td, "instrument": inst,
                "signal_id": "sig_a", "signal_run_id": "run_001",
                "score": 1.0 if inst == "0000000.SZ" else 0.5,
            })
    pq.write_table(pa.Table.from_pylist(rows_sig), str(sig_run_dir / "predictions.parquet"))

    # ── Labels ──
    lbl_dir = tmp_path / "labels" / "l1"
    lbl_dir.mkdir(parents=True)
    rows_lbl = []
    for td in dates:
        for inst in insts:
            rows_lbl.append({
                "trade_date": td, "instrument": inst,
                "label_id": "l1", "horizon": 5,
                "label_value": 0.02 if inst == "0000000.SZ" else -0.01,
            })
    pq.write_table(pa.Table.from_pylist(rows_lbl), str(lbl_dir / "labels.parquet"))


class TestSignalAnalytics:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path: Path) -> None:
        _populate_research_root(tmp_path)
        self.sa = SignalAnalytics(str(tmp_path))

    def test_list_signals(self) -> None:
        df = self.sa.list_signals()
        assert len(df) == 1
        assert df["signal_id"].iloc[0] == "sig_a"

    def test_list_labels(self) -> None:
        df = self.sa.list_labels()
        assert len(df) == 1
        assert df["label_id"].iloc[0] == "l1"

    def test_compute_ic_matrix(self) -> None:
        result = self.sa.compute_ic_matrix(
            signal_ids=["sig_a"], signal_run_ids={"sig_a": "run_001"}
        )
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 1
        assert result["signal_id"].iloc[0] == "sig_a"
        assert result["label_id"].iloc[0] == "l1"
        # sig_a has higher score for 0000000.SZ, which has positive label → positive IC
        assert result["ic_mean"].iloc[0] is not None
        assert result["ic_mean"].iloc[0] > 0

    def test_compute_rank_ic_matrix(self) -> None:
        result = self.sa.compute_rank_ic_matrix(
            signal_ids=["sig_a"], signal_run_ids={"sig_a": "run_001"}
        )
        assert len(result) == 1
        assert result["rank_ic_mean"].iloc[0] is not None
        assert result["rank_ic_mean"].iloc[0] > 0

    def test_daily_ic(self) -> None:
        df = self.sa.daily_ic("sig_a", "run_001")
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 3  # 3 dates
        assert list(df.columns) == ["trade_date", "ic", "n"]

    def test_ic_matrix_with_date_filter(self) -> None:
        result = self.sa.compute_ic_matrix(
            signal_ids=["sig_a"], signal_run_ids={"sig_a": "run_001"},
            start_date="2026-01-06", end_date="2026-01-07",
        )
        assert len(result) == 1
        assert result["ic_mean"].iloc[0] is not None

    def test_min_count_filters_insufficient_dates(self) -> None:
        """high min_count should exclude all pairs."""
        kwargs = {
            "signal_ids": ["sig_a"],
            "signal_run_ids": {"sig_a": "run_001"},
        }
        result_loose = self.sa.compute_ic_matrix(min_count=5, **kwargs)
        assert len(result_loose) == 1

        result_strict = self.sa.compute_ic_matrix(min_count=999, **kwargs)
        assert len(result_strict) == 0

    def test_query(self) -> None:
        df = self.sa.query("SELECT 1 AS a")
        assert df["a"].iloc[0] == 1

    def test_empty_root(self, tmp_path: Path) -> None:
        sa = SignalAnalytics(str(tmp_path / "empty"))
        assert len(sa.list_signals()) == 0
        assert len(sa.list_labels()) == 0
        assert len(sa.compute_ic_matrix(signal_ids=[], signal_run_ids={})) == 0

    def test_formal_analytics_requires_explicit_run_id(self) -> None:
        with pytest.raises(ValueError, match="signal_run_ids must be explicit"):
            self.sa.compute_ic_matrix(signal_ids=["sig_a"])
        with pytest.raises(ValueError, match="signal_run_id must be explicit"):
            self.sa.daily_ic("sig_a", "")

    def test_explicit_unknown_run_fails_without_fallback(self) -> None:
        with pytest.raises(FileNotFoundError, match="sig_a/missing"):
            self.sa.compute_ic_matrix(
                signal_ids=["sig_a"], signal_run_ids={"sig_a": "missing"}
            )

    def test_close_and_reopen(self) -> None:
        self.sa.close()
        sa2 = SignalAnalytics(str(self.sa.root))
        df = sa2.list_signals()
        assert len(df) == 1
        sa2.close()


def test_canonical_cli_rejects_missing_signal_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.run_signal_analytics import main

    monkeypatch.setattr(
        sys,
        "argv",
        ["run_signal_analytics.py", "--signal-id", "sig_a", "--label-id", "l1"],
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


def test_canonical_cli_runs_diagnostics_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from qsys.analysis.research_diagnostics import ResearchDiagnostics
    from scripts.run_signal_analytics import main

    config_path = tmp_path / "diagnostics.yaml"
    config_path.write_text("experiment_id: tiny\n", encoding="utf-8")
    expected = {
        "diagnostics_identity_sha256": "d" * 64,
        "manifest": str(tmp_path / "manifest.json"),
    }
    monkeypatch.setattr(
        ResearchDiagnostics,
        "from_config",
        classmethod(lambda cls, path, **kwargs: SimpleDiagnostics(expected)),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_signal_analytics.py",
            "--diagnostics-config",
            str(config_path),
            "--research-root",
            str(tmp_path),
        ],
    )

    main()

    output = capsys.readouterr().out
    assert "d" * 64 in output
    assert str(tmp_path / "manifest.json") in output


def test_canonical_cli_runs_diagnostics_validation_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import qsys.research.diagnostics_validation as validation_module
    from scripts.run_signal_analytics import main

    config_path = tmp_path / "diagnostics.yaml"
    config_path.write_text("experiment_id: tiny\n", encoding="utf-8")
    validation_path = tmp_path / "diagnostics_validation.json"
    monkeypatch.setattr(
        validation_module,
        "validate_research_diagnostics",
        lambda path, **kwargs: {
            "diagnostics_identity_sha256": "v" * 64,
            "validation": str(validation_path),
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_signal_analytics.py",
            "--diagnostics-config",
            str(config_path),
            "--research-root",
            str(tmp_path),
            "--validate-only",
        ],
    )

    main()

    output = capsys.readouterr().out
    assert "v" * 64 in output
    assert str(validation_path) in output


def test_canonical_cli_runs_backtest_validation_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import qsys.research.backtest_validation as validation_module
    from scripts.run_signal_analytics import main

    config_path = tmp_path / "backtest-validation.yaml"
    config_path.write_text("schema_version: fixture\n", encoding="utf-8")
    monkeypatch.setattr(
        validation_module,
        "validate_complete_accounting_backtest",
        lambda path: {
            "status": "pass",
            "manifest_sha256": "b" * 64,
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_signal_analytics.py",
            "--backtest-validation-config",
            str(config_path),
        ],
    )

    main()

    output = capsys.readouterr().out
    assert "Complete-accounting backtest validation: pass" in output
    assert "b" * 64 in output


class SimpleDiagnostics:
    def __init__(self, result: dict[str, str]) -> None:
        self._result = result

    def run(self) -> dict[str, str]:
        return self._result
