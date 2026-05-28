"""Tests for qsys.signal.expression."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from qsys.research.evaluation import SignalEvaluator
from qsys.signal.expression import (
    SignalExpressionRunner,
    SignalExpressionSpec,
    SignalInputSpec,
    _check_expression_safe,
    align_input_signals,
    apply_postprocess,
    evaluate_expression,
)
from qsys.signal.store import SignalStore


def _signal(n_dates=5, n_inst=10, signal_id="test_sig", signal_run_id="test_run", score_base=0.0):
    rows = []
    for di in range(n_dates):
        for ii in range(n_inst):
            rows.append({
                "trade_date": f"2026-06-{15 + di:02d}",
                "data_date": f"2026-06-{14 + di - 2:02d}",
                "instrument": f"000{ii:03d}.SZ",
                "signal_id": signal_id,
                "signal_run_id": signal_run_id,
                "score": score_base + float(ii) / n_inst,
            })
    return pd.DataFrame(rows)


# ── Safety ───────────────────────────────────────────────────────────────────


class TestExpressionSafety:
    def test_semicolon_rejected(self):
        with pytest.raises(ValueError, match="unsafe"):
            _check_expression_safe("a; DROP TABLE b")

    def test_drop_rejected(self):
        with pytest.raises(ValueError, match="unsafe"):
            _check_expression_safe("1.0 * DROP TABLE x")

    def test_safe_expression_passes(self):
        _check_expression_safe("0.7 * a + 0.3 * b")  # no error


# ── align_input_signals ──────────────────────────────────────────────────────


class TestAlignInputSignals:
    def test_single_input(self, tmp_path):
        store = SignalStore(str(tmp_path))
        store.save_signal_run("s", "r", _signal(signal_id="s", signal_run_id="r"),
                              check_no_lookahead=False)
        spec = [SignalInputSpec(alias="x", signal_id="s", signal_run_id="r")]
        aligned = align_input_signals(spec, store)
        assert "trade_date" in aligned.columns
        assert "instrument" in aligned.columns
        assert "x" in aligned.columns
        assert "data_date" in aligned.columns

    def test_two_inputs_align(self, tmp_path):
        store = SignalStore(str(tmp_path))
        a = _signal(n_dates=3, n_inst=5, signal_id="a", signal_run_id="r")
        b = _signal(n_dates=3, n_inst=5, signal_id="b", signal_run_id="r", score_base=10)
        store.save_signal_run("a", "r", a, check_no_lookahead=False)
        store.save_signal_run("b", "r", b, check_no_lookahead=False)

        spec = [
            SignalInputSpec(alias="x", signal_id="a", signal_run_id="r"),
            SignalInputSpec(alias="y", signal_id="b", signal_run_id="r"),
        ]
        aligned = align_input_signals(spec, store)
        assert "x" in aligned.columns
        assert "y" in aligned.columns
        assert len(aligned) > 0
        # x and y should be different values
        assert (aligned["x"] != aligned["y"]).any()

    def test_data_date_max(self, tmp_path):
        """data_date is the per-row max across inputs."""
        store = SignalStore(str(tmp_path))
        a = _signal(n_dates=2, n_inst=3, signal_id="a", signal_run_id="r")
        b = _signal(n_dates=2, n_inst=3, signal_id="b", signal_run_id="r")
        # Override data_date on b to be later
        b["data_date"] = "2026-06-19"
        store.save_signal_run("a", "r", a, check_no_lookahead=False)
        store.save_signal_run("b", "r", b, check_no_lookahead=False)

        spec = [
            SignalInputSpec(alias="x", signal_id="a", signal_run_id="r"),
            SignalInputSpec(alias="y", signal_id="b", signal_run_id="r"),
        ]
        aligned = align_input_signals(spec, store)
        assert (aligned["data_date"] == "2026-06-19").all()

    def test_missing_score_column(self, tmp_path):
        store = SignalStore(str(tmp_path))
        store.save_signal_run("s", "r", _signal(signal_id="s", signal_run_id="r"),
                              check_no_lookahead=False)
        spec = [SignalInputSpec(alias="x", signal_id="s", signal_run_id="r", score_column="nonexistent")]
        with pytest.raises(ValueError, match="nonexistent"):
            align_input_signals(spec, store)

    def test_missing_input_signal(self, tmp_path):
        store = SignalStore(str(tmp_path))
        spec = [SignalInputSpec(alias="x", signal_id="nonexistent", signal_run_id="r")]
        with pytest.raises(FileNotFoundError):
            align_input_signals(spec, store)


# ── evaluate_expression (requires duckdb) ────────────────────────────────────


class TestEvaluateExpression:
    def test_identity(self):
        pytest.importorskip("duckdb")
        df = pd.DataFrame({"alpha": [1.0, 2.0, 3.0]})
        result = evaluate_expression(df, "1.0 * alpha")
        assert result.name == "score_raw"
        assert list(result) == [1.0, 2.0, 3.0]

    def test_weighted_sum(self):
        pytest.importorskip("duckdb")
        df = pd.DataFrame({"a": [1.0, 2.0], "b": [0.5, 1.0]})
        result = evaluate_expression(df, "0.7 * a + 0.3 * b")
        expected = [0.7 * 1.0 + 0.3 * 0.5, 0.7 * 2.0 + 0.3 * 1.0]
        assert list(result) == expected

    def test_case_when(self):
        pytest.importorskip("duckdb")
        df = pd.DataFrame({"a": [1.0, -1.0, 0.0]})
        result = evaluate_expression(df, "CASE WHEN a > 0 THEN a ELSE 0.5 * a END")
        expected = [1.0, -0.5, 0.0]
        assert list(result) == expected

    def test_unsafe_drop_rejected(self):
        pytest.importorskip("duckdb")
        df = pd.DataFrame({"a": [1.0]})
        with pytest.raises(ValueError, match="unsafe"):
            evaluate_expression(df, "a; DROP TABLE b")


# ── apply_postprocess ────────────────────────────────────────────────────────


class TestApplyPostprocess:
    def test_no_postprocess(self):
        df = pd.DataFrame({
            "trade_date": ["2026-06-15", "2026-06-15", "2026-06-16", "2026-06-16"],
            "score_raw": [1.0, 2.0, 3.0, 4.0],
        })
        result = apply_postprocess(df, postprocess={})
        assert list(result["score"]) == [1.0, 2.0, 3.0, 4.0]
        assert "score_rank" in result.columns
        assert "score_z" in result.columns
        assert "is_valid" in result.columns

    def test_daily_zscore(self):
        df = pd.DataFrame({
            "trade_date": ["2026-06-15", "2026-06-15", "2026-06-16", "2026-06-16"],
            "score_raw": [1.0, 2.0, 3.0, 4.0],
        })
        result = apply_postprocess(df, postprocess={"daily_zscore": True})
        # After zscore, mean ~0 per day
        for d in result["trade_date"].unique():
            mask = result["trade_date"] == d
            assert abs(result.loc[mask, "score"].mean()) < 1e-10

    def test_winsorize_clips_score_not_raw(self):
        df = pd.DataFrame({
            "trade_date": ["2026-06-15"] * 100,
            "score_raw": [float(i) for i in range(100)],
        })
        result = apply_postprocess(df, postprocess={"winsorize": 0.05})
        # score_raw should remain unchanged
        assert result["score_raw"].min() == 0.0
        assert result["score_raw"].max() == 99.0
        # score should be clipped
        assert result["score"].min() > 0.0
        assert result["score"].max() < 99.0

    def test_daily_zscore_preserves_score_raw(self):
        df = pd.DataFrame({
            "trade_date": ["2026-06-15", "2026-06-15", "2026-06-16", "2026-06-16"],
            "score_raw": [1.0, 2.0, 3.0, 4.0],
        })
        original = list(df["score_raw"])
        result = apply_postprocess(df, postprocess={"daily_zscore": True})
        # score_raw unchanged
        assert list(result["score_raw"]) == original
        # z-scored score has mean ~0 per day
        for d in result["trade_date"].unique():
            mask = result["trade_date"] == d
            assert abs(result.loc[mask, "score"].mean()) < 1e-10

    def test_no_postprocess_preserves_exact_score(self):
        df = pd.DataFrame({
            "trade_date": ["2026-06-15", "2026-06-15"],
            "score_raw": [1.234, 5.678],
        })
        result = apply_postprocess(df, postprocess=None)
        assert result["score"].iloc[0] == 1.234
        assert result["score"].iloc[1] == 5.678
        assert result["score_raw"].iloc[0] == 1.234

    def test_winsorize_invalid_q(self):
        df = pd.DataFrame({"trade_date": ["d"], "score_raw": [1.0]})
        with pytest.raises(ValueError, match="winsorize"):
            apply_postprocess(df, postprocess={"winsorize": 0.5})

    def test_invalid_rows_marked(self):
        df = pd.DataFrame({
            "trade_date": ["2026-06-15", "2026-06-15"],
            "score_raw": [1.0, float("inf")],
        })
        result = apply_postprocess(df)
        assert result["is_valid"].tolist() == [True, False]


# ── SignalExpressionRunner ──────────────────────────────────────────────────


class TestSignalExpressionRunner:
    def test_identity_run(self, tmp_path):
        pytest.importorskip("duckdb")
        store = SignalStore(str(tmp_path))
        store.save_signal_run("s", "r", _signal(signal_id="s", signal_run_id="r"),
                              check_no_lookahead=False)

        spec = SignalExpressionSpec(
            expression_id="expr_id",
            output_signal_id="out_sig",
            output_signal_run_id="out_run",
            inputs=[SignalInputSpec(alias="x", signal_id="s", signal_run_id="r")],
            expression="1.0 * x",
        )

        runner = SignalExpressionRunner(str(tmp_path))
        out_path = runner.run(spec, overwrite=True)
        assert out_path.exists()

        # Load and verify
        loaded = store.load_signal_run("out_sig", "out_run")
        assert len(loaded) == 50  # 5 dates x 10 inst
        assert list(loaded.columns[:6]) == ["trade_date", "data_date", "instrument", "signal_id", "signal_run_id", "score"]
        assert loaded["signal_id"].iloc[0] == "out_sig"
        assert loaded["signal_run_id"].iloc[0] == "out_run"
        assert "score_raw" in loaded.columns
        assert "source_expression_id" in loaded.columns

    def test_two_input_weighted(self, tmp_path):
        pytest.importorskip("duckdb")
        store = SignalStore(str(tmp_path))
        store.save_signal_run("a", "r", _signal(n_dates=2, n_inst=5, signal_id="a", signal_run_id="r"),
                              check_no_lookahead=False)
        store.save_signal_run("b", "r", _signal(n_dates=2, n_inst=5, signal_id="b", signal_run_id="r", score_base=10),
                              check_no_lookahead=False)

        spec = SignalExpressionSpec(
            expression_id="e2",
            output_signal_id="out",
            output_signal_run_id="r",
            inputs=[
                SignalInputSpec(alias="x", signal_id="a", signal_run_id="r"),
                SignalInputSpec(alias="y", signal_id="b", signal_run_id="r"),
            ],
            expression="0.7 * x + 0.3 * y",
        )
        runner = SignalExpressionRunner(str(tmp_path))
        runner.run(spec, overwrite=True)

        loaded = store.load_signal_run("out", "r")
        assert len(loaded) > 0

    def test_manifest_records(self, tmp_path):
        pytest.importorskip("duckdb")
        store = SignalStore(str(tmp_path))
        store.save_signal_run("a", "r", _signal(signal_id="a", signal_run_id="r"),
                              check_no_lookahead=False)

        spec = SignalExpressionSpec(
            expression_id="my_expr",
            output_signal_id="out",
            output_signal_run_id="r",
            inputs=[SignalInputSpec(alias="x", signal_id="a", signal_run_id="r")],
            expression="1.0 * x",
            label_id="fr_5d",
            universe="csi300",
        )
        runner = SignalExpressionRunner(str(tmp_path))
        runner.run(spec, overwrite=True)

        mf = store.load_manifest("out", "r")
        assert mf["expression_id"] == "my_expr"
        assert mf["output_signal_id"] == "out"
        assert mf["label_id"] == "fr_5d"
        assert mf["signal_kind"] == "derived"
        assert mf["source_expression_id"] == "my_expr"
        assert len(mf["inputs"]) == 1
        assert mf["expression"] == "1.0 * x"

    def test_from_dict_does_not_mutate(self):
        payload = {
            "expression_id": "e1",
            "output_signal_id": "out",
            "output_signal_run_id": "r",
            "inputs": [{"alias": "x", "signal_id": "s", "signal_run_id": "r"}],
            "expression": "1.0 * x",
        }
        original = dict(payload)
        spec = SignalExpressionSpec.from_dict(payload)
        assert payload == original  # unchanged
        assert spec.expression_id == "e1"

    def test_overwrite_false_protects(self, tmp_path):
        pytest.importorskip("duckdb")
        store = SignalStore(str(tmp_path))
        store.save_signal_run("a", "r", _signal(signal_id="a", signal_run_id="r"),
                              check_no_lookahead=False)
        spec = SignalExpressionSpec(
            expression_id="e", output_signal_id="out", output_signal_run_id="r",
            inputs=[SignalInputSpec(alias="x", signal_id="a", signal_run_id="r")],
            expression="1.0 * x",
        )
        runner = SignalExpressionRunner(str(tmp_path))
        runner.run(spec, overwrite=True)
        with pytest.raises(FileExistsError):
            runner.run(spec)

    def test_overwrite_true_succeeds(self, tmp_path):
        pytest.importorskip("duckdb")
        store = SignalStore(str(tmp_path))
        store.save_signal_run("a", "r", _signal(signal_id="a", signal_run_id="r"),
                              check_no_lookahead=False)
        spec = SignalExpressionSpec(
            expression_id="e", output_signal_id="out", output_signal_run_id="r",
            inputs=[SignalInputSpec(alias="x", signal_id="a", signal_run_id="r")],
            expression="1.0 * x",
        )
        runner = SignalExpressionRunner(str(tmp_path))
        runner.run(spec, overwrite=True)
        runner.run(spec, overwrite=True)  # should not raise

    def test_evaluable_by_signal_evaluator(self, tmp_path):
        pytest.importorskip("duckdb")
        from qsys.label.store import LabelStore

        # Save source signal
        sstore = SignalStore(str(tmp_path))
        sstore.save_signal_run("src", "r", _signal(n_dates=3, n_inst=10, signal_id="src", signal_run_id="r"),
                              check_no_lookahead=False)

        # Save small label
        lstore = LabelStore(str(tmp_path))
        import random
        rng = random.Random(42)
        lbl_rows = []
        for di in range(3):
            for ii in range(10):
                lbl_rows.append({
                    "trade_date": f"2026-06-{15 + di:02d}",
                    "instrument": f"000{ii:03d}.SZ",
                    "label_id": "lbl",
                    "horizon": 5,
                    "label_value": float(ii) / 10 * 0.1 + rng.uniform(-0.005, 0.005),
                })
        lstore.save_labels("lbl", pd.DataFrame(lbl_rows))

        # Build identity expression
        spec = SignalExpressionSpec(
            expression_id="e", output_signal_id="out", output_signal_run_id="r",
            inputs=[SignalInputSpec(alias="x", signal_id="src", signal_run_id="r")],
            expression="1.0 * x",
        )
        runner = SignalExpressionRunner(str(tmp_path))
        runner.run(spec, overwrite=True)

        # Evaluate
        evaluator = SignalEvaluator(str(tmp_path))
        result = evaluator.evaluate(
            signal_id="out", signal_run_id="r", label_id="lbl", overwrite=True,
        )
        assert result.n_obs > 0
        assert result.ic_mean is not None

    def test_spec_from_file(self, tmp_path):
        spec_path = tmp_path / "spec.yaml"
        spec_path.write_text("""
expression_id: expr1
output_signal_id: out
output_signal_run_id: r
inputs:
  - alias: x
    signal_id: src
    signal_run_id: r
    score_column: score
expression: "1.0 * x"
""")
        spec = SignalExpressionSpec.from_file(spec_path)
        assert spec.expression_id == "expr1"
        assert len(spec.inputs) == 1
        assert spec.inputs[0].alias == "x"

    def test_data_date_reflects_inputs(self, tmp_path):
        """Derived signal data_date should be max of input data_dates."""
        pytest.importorskip("duckdb")
        store = SignalStore(str(tmp_path))
        a = _signal(n_dates=2, n_inst=3, signal_id="a", signal_run_id="r")
        b = _signal(n_dates=2, n_inst=3, signal_id="b", signal_run_id="r")
        # Override data_date on b to be later than a's but still < prev_trading_day
        b["data_date"] = "2026-06-12"  # a has "2026-06-12" for day 1, "2026-06-13" for day 2
        a["data_date"] = "2026-06-11"
        store.save_signal_run("a", "r", a, check_no_lookahead=False)
        store.save_signal_run("b", "r", b, check_no_lookahead=False)

        spec = SignalExpressionSpec(
            expression_id="e", output_signal_id="out", output_signal_run_id="r",
            inputs=[
                SignalInputSpec(alias="x", signal_id="a", signal_run_id="r"),
                SignalInputSpec(alias="y", signal_id="b", signal_run_id="r"),
            ],
            expression="0.5 * x + 0.5 * y",
        )
        runner = SignalExpressionRunner(str(tmp_path))
        runner.run(spec, overwrite=True)
        loaded = store.load_signal_run("out", "r")
        assert (loaded["data_date"] == "2026-06-12").all()
