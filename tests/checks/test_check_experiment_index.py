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


_RUN_COLS = ["signal_id", "signal_run_id", "prediction_start", "prediction_end",
             "row_count", "path"]
_EVAL_COLS = ["signal_id", "signal_run_id", "label_id", "ic_mean",
              "rank_ic_mean", "rank_icir", "path"]
_BT_COLS = ["strategy_template_id", "signal_id", "signal_run_id", "start_date",
            "end_date", "initial_capital", "final_value", "total_return",
            "trading_day_count", "path"]

_FIXTURE_MANIFEST = '{"experiment_id": "test_exp", "created_at": "2026-05-30T00:00:00Z"}'


def _valid_exp(tmp_path: Path, name: str = "valid_exp") -> Path:
    exp = tmp_path / name
    exp.mkdir()
    _touch(exp / "manifest.json", _FIXTURE_MANIFEST)
    _write_csv(exp / "signal_run_index.csv", _RUN_COLS,
               [["sig1", "run1", "2026-05-01", "2026-05-30", 100, "/tmp/sig1.parquet"]])
    _write_csv(exp / "signal_eval_index.csv", _EVAL_COLS,
               [["sig1", "run1", "fr_5d", 0.05, 0.03, 0.5, "/tmp/eval1.parquet"]])
    _write_csv(exp / "backtest_index.csv", _BT_COLS,
               [["rank_weight_top20", "sig1", "run1", "2026-05-01", "2026-05-30",
                 1000000, 1050000, 0.05, 21, "/tmp/bt1.parquet"]])
    return exp


class TestCheckExperimentIndex:
    def test_valid_experiment_passes(self, tmp_path: Path) -> None:
        exp = _valid_exp(tmp_path)
        result = check_experiment_index(exp)
        assert result["status"] == "passed"
        assert result["signal_eval_count"] == 1
        assert result["backtest_count"] == 1

    def test_missing_manifest_fails(self, tmp_path: Path) -> None:
        exp = _valid_exp(tmp_path)
        (exp / "manifest.json").unlink()
        result = check_experiment_index(exp)
        assert result["status"] == "failed"
        assert "manifest.json" in result["missing_files"]

    def test_cross_ref_warning_on_missing_signal_run(self, tmp_path: Path) -> None:
        exp = tmp_path / "xref_bad"
        exp.mkdir()
        _touch(exp / "manifest.json", _FIXTURE_MANIFEST)
        _write_csv(exp / "signal_run_index.csv", _RUN_COLS, [])
        _write_csv(exp / "signal_eval_index.csv", _EVAL_COLS,
                   [["ghost_sig", "ghost_run", "fr_5d", 0.05, 0.03, 0.5, "/tmp/eval1.parquet"]])
        _write_csv(exp / "backtest_index.csv", _BT_COLS, [])
        result = check_experiment_index(exp)
        assert len(result["warnings"]) >= 1
        assert "ghost_sig" in result["warnings"][0]

    def test_missing_backtest_index_is_failure(self, tmp_path: Path) -> None:
        exp = _valid_exp(tmp_path)
        (exp / "backtest_index.csv").unlink()
        result = check_experiment_index(exp)
        assert result["status"] == "failed"
        assert "backtest_index.csv" in result["missing_files"]

    def test_output_is_json_serializable(self, tmp_path: Path) -> None:
        exp = _valid_exp(tmp_path)
        result = check_experiment_index(exp)
        parsed = json.loads(json.dumps(result))
        assert "status" in parsed

    # ------------------------------------------------------------------
    # Edge cases: missing eval index (should not raise UnboundLocalError)
    # ------------------------------------------------------------------
    def test_missing_signal_eval_no_unbound(self, tmp_path: Path) -> None:
        """signal_run + backtest exist, signal_eval missing → no crash."""
        exp = tmp_path / "no_eval"
        exp.mkdir()
        _touch(exp / "manifest.json", _FIXTURE_MANIFEST)
        _write_csv(exp / "signal_run_index.csv", _RUN_COLS,
                   [["sig1", "run1", "2026-05-01", "2026-05-30", 100, "/tmp/sig1.parquet"]])
        _write_csv(exp / "backtest_index.csv", _BT_COLS,
                   [["rank_weight_top20", "sig1", "run1", "2026-05-01", "2026-05-30",
                     1000000, 1050000, 0.05, 21, "/tmp/bt1.parquet"]])
        # signal_eval_index.csv does not exist at all
        result = check_experiment_index(exp)
        assert "signal_eval_index.csv" in result["missing_files"]
        assert result["status"] == "failed"

    def test_empty_signal_eval_no_unbound(self, tmp_path: Path) -> None:
        """signal_run + backtest exist, signal_eval empty → no crash."""
        exp = tmp_path / "empty_eval"
        exp.mkdir()
        _touch(exp / "manifest.json", _FIXTURE_MANIFEST)
        _write_csv(exp / "signal_run_index.csv", _RUN_COLS,
                   [["sig1", "run1", "2026-05-01", "2026-05-30", 100, "/tmp/sig1.parquet"]])
        _write_csv(exp / "signal_eval_index.csv", _EVAL_COLS, [])
        _write_csv(exp / "backtest_index.csv", _BT_COLS,
                   [["rank_weight_top20", "sig1", "run1", "2026-05-01", "2026-05-30",
                     1000000, 1050000, 0.05, 21, "/tmp/bt1.parquet"]])
        result = check_experiment_index(exp)
        assert result["signal_eval_count"] == 0

    def test_only_signal_run_exists_no_unbound(self, tmp_path: Path) -> None:
        """Only signal_run exists → minimal files, should fail but not crash."""
        exp = tmp_path / "only_run"
        exp.mkdir()
        _touch(exp / "manifest.json", _FIXTURE_MANIFEST)
        _write_csv(exp / "signal_run_index.csv", _RUN_COLS,
                   [["sig1", "run1", "2026-05-01", "2026-05-30", 100, "/tmp/sig1.parquet"]])
        result = check_experiment_index(exp)
        assert "signal_eval_index.csv" in result["missing_files"]
        assert "backtest_index.csv" in result["missing_files"]
        assert result["status"] == "failed"

    # ------------------------------------------------------------------
    # Strict mode
    # ------------------------------------------------------------------
    def test_strict_cross_ref_fails(self, tmp_path: Path) -> None:
        """strict: eval references signal_run not in index → failed."""
        exp = tmp_path / "strict_xref"
        exp.mkdir()
        _touch(exp / "manifest.json", _FIXTURE_MANIFEST)
        _write_csv(exp / "signal_run_index.csv", _RUN_COLS, [])
        _write_csv(exp / "signal_eval_index.csv", _EVAL_COLS,
                   [["ghost_sig", "ghost_run", "fr_5d", 0.05, 0.03, 0.5, "/tmp/eval1.parquet"]])
        _write_csv(exp / "backtest_index.csv", _BT_COLS, [])
        result = check_experiment_index(exp, strict=True)
        assert result["status"] == "failed"
        # strict mode should flag cross-ref + empty eval index
        errs = " ".join(result["errors"])
        assert "ghost_sig" in errs or "ghost_run" in errs

    def test_strict_empty_artifact_path_fails(self, tmp_path: Path) -> None:
        """strict: empty path in signal_run → failed."""
        exp = tmp_path / "strict_path"
        exp.mkdir()
        _touch(exp / "manifest.json", _FIXTURE_MANIFEST)
        _write_csv(exp / "signal_run_index.csv", _RUN_COLS,
                   [["sig1", "run1", "2026-05-01", "2026-05-30", 100, ""]])
        _write_csv(exp / "signal_eval_index.csv", _EVAL_COLS,
                   [["sig1", "run1", "fr_5d", 0.05, 0.03, 0.5, "/tmp/eval1.parquet"]])
        _write_csv(exp / "backtest_index.csv", _BT_COLS,
                   [["rank_weight_top20", "sig1", "run1", "2026-05-01", "2026-05-30",
                     1000000, 1050000, 0.05, 21, "/tmp/bt1.parquet"]])
        result = check_experiment_index(exp, strict=True)
        assert result["status"] == "failed"
        assert any("empty artifact path" in e for e in result["errors"])

    def test_strict_empty_eval_index_fails(self, tmp_path: Path) -> None:
        """strict: empty signal_eval_index → failed."""
        exp = tmp_path / "strict_empty_eval"
        exp.mkdir()
        _touch(exp / "manifest.json", _FIXTURE_MANIFEST)
        _write_csv(exp / "signal_run_index.csv", _RUN_COLS,
                   [["sig1", "run1", "2026-05-01", "2026-05-30", 100, "/tmp/sig1.parquet"]])
        _write_csv(exp / "signal_eval_index.csv", _EVAL_COLS, [])
        _write_csv(exp / "backtest_index.csv", _BT_COLS,
                   [["rank_weight_top20", "sig1", "run1", "2026-05-01", "2026-05-30",
                     1000000, 1050000, 0.05, 21, ""]])
        result = check_experiment_index(exp, strict=True)
        assert result["status"] == "failed"
        assert any("strict: signal_eval_index is empty" in e for e in result["errors"])

    def test_strict_empty_backtest_index_fails(self, tmp_path: Path) -> None:
        """strict: empty backtest_index → failed."""
        exp = tmp_path / "strict_empty_bt"
        exp.mkdir()
        _touch(exp / "manifest.json", _FIXTURE_MANIFEST)
        _write_csv(exp / "signal_run_index.csv", _RUN_COLS,
                   [["sig1", "run1", "2026-05-01", "2026-05-30", 100, "/tmp/sig1.parquet"]])
        _write_csv(exp / "signal_eval_index.csv", _EVAL_COLS,
                   [["sig1", "run1", "fr_5d", 0.05, 0.03, 0.5, "/tmp/eval1.parquet"]])
        _write_csv(exp / "backtest_index.csv", _BT_COLS, [])
        result = check_experiment_index(exp, strict=True)
        assert result["status"] == "failed"
        assert any("strict: backtest_index is empty" in e for e in result["errors"])

    def test_strict_manifest_missing_experiment_id_fails(self, tmp_path: Path) -> None:
        """strict: manifest missing experiment_id → failed."""
        exp = tmp_path / "strict_no_id"
        exp.mkdir()
        _touch(exp / "manifest.json", '{"created_at": "2026-05-30T00:00:00Z"}')
        _write_csv(exp / "signal_run_index.csv", _RUN_COLS,
                   [["sig1", "run1", "2026-05-01", "2026-05-30", 100, "/tmp/sig1.parquet"]])
        _write_csv(exp / "signal_eval_index.csv", _EVAL_COLS,
                   [["sig1", "run1", "fr_5d", 0.05, 0.03, 0.5, "/tmp/eval1.parquet"]])
        _write_csv(exp / "backtest_index.csv", _BT_COLS,
                   [["rank_weight_top20", "sig1", "run1", "2026-05-01", "2026-05-30",
                     1000000, 1050000, 0.05, 21, "/tmp/bt1.parquet"]])
        result = check_experiment_index(exp, strict=True)
        assert result["status"] == "failed"
        assert any("experiment_id" in e for e in result["errors"])

    # ------------------------------------------------------------------
    # Non-strict mode: warning / degraded behavior
    # ------------------------------------------------------------------
    def test_non_strict_cross_ref_is_warning(self, tmp_path: Path) -> None:
        """non-strict: cross-ref missing → warning, not failed."""
        exp = tmp_path / "nonstrict_xref"
        exp.mkdir()
        _touch(exp / "manifest.json", _FIXTURE_MANIFEST)
        _write_csv(exp / "signal_run_index.csv", _RUN_COLS, [])
        _write_csv(exp / "signal_eval_index.csv", _EVAL_COLS,
                   [["ghost_sig", "ghost_run", "fr_5d", 0.05, 0.03, 0.5, "/tmp/eval1.parquet"]])
        _write_csv(exp / "backtest_index.csv", _BT_COLS, [])
        result = check_experiment_index(exp, strict=False)
        assert result["status"] != "failed"  # cross-ref alone doesn't fail
        assert len(result["warnings"]) >= 1
        assert "ghost_sig" in result["warnings"][0]

    def test_non_strict_missing_cols_is_degraded(self, tmp_path: Path) -> None:
        """non-strict: missing required columns → degraded."""
        exp = tmp_path / "nonstrict_cols"
        exp.mkdir()
        _touch(exp / "manifest.json", _FIXTURE_MANIFEST)
        _write_csv(exp / "signal_run_index.csv",
                   ["signal_id", "signal_run_id"],  # only 2 cols
                   [["sig1", "run1"]])
        _write_csv(exp / "signal_eval_index.csv", _EVAL_COLS,
                   [["sig1", "run1", "fr_5d", 0.05, 0.03, 0.5, "/tmp/eval1.parquet"]])
        _write_csv(exp / "backtest_index.csv", _BT_COLS,
                   [["rank_weight_top20", "sig1", "run1", "2026-05-01", "2026-05-30",
                     1000000, 1050000, 0.05, 21, "/tmp/bt1.parquet"]])
        result = check_experiment_index(exp, strict=False)
        assert result["status"] == "degraded"
        assert "prediction_start" in result["signal_run_missing_cols"]
