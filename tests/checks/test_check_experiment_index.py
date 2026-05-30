"""Tests for scripts/checks/check_experiment_index.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parents[2]
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from scripts.checks.check_experiment_index import check_experiment_index


def _touch(path: Path, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _write_csv(path: Path, columns: list[str], rows: list[list]) -> Path:
    import pandas as pd
    df = pd.DataFrame(rows, columns=columns)
    df.to_csv(path, index=False)
    return path


class TestCheckExperimentIndex:
    def test_valid_experiment_passes(self, tmp_path: Path) -> None:
        exp = tmp_path / "valid_exp"
        exp.mkdir()
        _touch(exp / "manifest.json", '{"experiment_id": "valid_exp", "created_at": "2026-05-30T00:00:00Z"}')
        _write_csv(
            exp / "signal_run_index.csv",
            ["signal_id", "signal_run_id", "prediction_start", "prediction_end", "row_count", "path"],
            [["sig1", "run1", "2026-05-01", "2026-05-30", 100, "/tmp/sig1.csv"]],
        )
        _write_csv(
            exp / "signal_eval_index.csv",
            ["signal_id", "signal_run_id", "label_id", "ic_mean", "rank_ic_mean", "rank_icir", "path"],
            [["sig1", "run1", "fr_5d", 0.05, 0.03, 0.5, "/tmp/eval1.csv"]],
        )
        _write_csv(
            exp / "backtest_index.csv",
            ["strategy_template_id", "signal_id", "signal_run_id", "start_date", "end_date",
             "initial_capital", "final_value", "total_return", "trading_day_count"],
            [["rank_weight_top20", "sig1", "run1", "2026-05-01", "2026-05-30",
              1000000, 1050000, 0.05, 21]],
        )
        result = check_experiment_index(exp)
        assert result["status"] == "passed"
        assert result["signal_eval_count"] == 1
        assert result["backtest_count"] == 1

    def test_missing_manifest_fails(self, tmp_path: Path) -> None:
        exp = tmp_path / "no_manifest"
        exp.mkdir()
        _write_csv(
            exp / "signal_run_index.csv",
            ["signal_id", "signal_run_id", "prediction_start", "prediction_end", "row_count", "path"],
            [["sig1", "run1", "2026-05-01", "2026-05-30", 100, "/tmp/sig1.csv"]],
        )
        _write_csv(
            exp / "signal_eval_index.csv",
            ["signal_id", "signal_run_id", "label_id", "ic_mean", "rank_ic_mean", "rank_icir", "path"],
            [["sig1", "run1", "fr_5d", 0.05, 0.03, 0.5, "/tmp/eval1.csv"]],
        )
        _write_csv(
            exp / "backtest_index.csv",
            ["strategy_template_id", "signal_id", "signal_run_id", "start_date", "end_date",
             "initial_capital", "final_value", "total_return", "trading_day_count"],
            [["rank_weight_top20", "sig1", "run1", "2026-05-01", "2026-05-30",
              1000000, 1050000, 0.05, 21]],
        )
        result = check_experiment_index(exp)
        assert result["status"] == "failed"
        assert "manifest.json" in result["missing_files"]

    def test_cross_ref_warning_on_missing_signal_run(self, tmp_path: Path) -> None:
        exp = tmp_path / "xref_bad"
        exp.mkdir()
        _touch(exp / "manifest.json", '{"experiment_id": "xref_bad"}')
        # Empty signal_run_index
        _write_csv(
            exp / "signal_run_index.csv",
            ["signal_id", "signal_run_id", "prediction_start", "prediction_end", "row_count", "path"],
            [],
        )
        # eval references run not in index
        _write_csv(
            exp / "signal_eval_index.csv",
            ["signal_id", "signal_run_id", "label_id", "ic_mean", "rank_ic_mean", "rank_icir", "path"],
            [["ghost_sig", "ghost_run", "fr_5d", 0.05, 0.03, 0.5, "/tmp/eval1.csv"]],
        )
        _write_csv(
            exp / "backtest_index.csv",
            ["strategy_template_id", "signal_id", "signal_run_id", "start_date", "end_date",
             "initial_capital", "final_value", "total_return", "trading_day_count"],
            [],
        )
        result = check_experiment_index(exp)
        # missing_files should be empty, but warnings about cross-refs
        assert len(result["warnings"]) >= 1
        assert "ghost_sig" in result["warnings"][0]

    def test_missing_backtest_index_is_failure(self, tmp_path: Path) -> None:
        exp = tmp_path / "no_bt"
        exp.mkdir()
        _touch(exp / "manifest.json", '{"experiment_id": "no_bt"}')
        _write_csv(
            exp / "signal_run_index.csv",
            ["signal_id", "signal_run_id", "prediction_start", "prediction_end", "row_count", "path"],
            [["sig1", "run1", "2026-05-01", "2026-05-30", 100, "/tmp/sig1.csv"]],
        )
        _write_csv(
            exp / "signal_eval_index.csv",
            ["signal_id", "signal_run_id", "label_id", "ic_mean", "rank_ic_mean", "rank_icir", "path"],
            [],
        )
        # missing backtest_index.csv
        result = check_experiment_index(exp)
        assert result["status"] == "failed"
        assert "backtest_index.csv" in result["missing_files"]

    def test_output_is_json_serializable(self, tmp_path: Path) -> None:
        exp = tmp_path / "json_test"
        exp.mkdir()
        _touch(exp / "manifest.json", '{}')
        _write_csv(
            exp / "signal_run_index.csv",
            ["signal_id", "signal_run_id", "prediction_start", "prediction_end", "row_count", "path"],
            [],
        )
        _write_csv(
            exp / "signal_eval_index.csv",
            ["signal_id", "signal_run_id", "label_id", "ic_mean", "rank_ic_mean", "rank_icir", "path"],
            [],
        )
        _write_csv(
            exp / "backtest_index.csv",
            ["strategy_template_id", "signal_id", "signal_run_id", "start_date", "end_date",
             "initial_capital", "final_value", "total_return", "trading_day_count"],
            [],
        )
        result = check_experiment_index(exp)
        parsed = json.loads(json.dumps(result))
        assert "status" in parsed
