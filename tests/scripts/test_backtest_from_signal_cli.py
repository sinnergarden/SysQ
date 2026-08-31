"""Contract tests for cached signal materialization in the backtest CLI."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from qsys.signal.store import SignalStore


def _load_cli_module():
    script = Path(__file__).resolve().parents[2] / "scripts/research/backtest_from_signal.py"
    spec = importlib.util.spec_from_file_location("backtest_from_signal_cli", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _save_signal(
    store: SignalStore,
    signal_id: str,
    signal_run_id: str,
    scores: list[float],
) -> None:
    frame = pd.DataFrame(
        {
            "trade_date": ["2026-06-15"] * len(scores),
            "data_date": ["2026-06-12"] * len(scores),
            "instrument": [f"00000{i + 1}.SZ" for i in range(len(scores))],
            "signal_id": [signal_id] * len(scores),
            "signal_run_id": [signal_run_id] * len(scores),
            "score": scores,
        }
    )
    store.save_signal_run(signal_id, signal_run_id, frame, overwrite=True)


def test_materialize_blend_persists_equal_weight_signal_and_lineage(tmp_path: Path) -> None:
    module = _load_cli_module()
    store = SignalStore(tmp_path)
    _save_signal(store, "signal_60d", "run_60d", [1.0, -1.0])
    _save_signal(store, "signal_180d", "run_180d", [0.0, 2.0])
    args = argparse.Namespace(
        signal_id="signal_60d",
        signal_run_id="run_60d",
        signal_id_2="signal_180d",
        signal_run_id_2="run_180d",
        blend_weight=0.5,
        blend_output_signal_id="financial_rc_equal",
        blend_output_signal_run_id="historical_equal_v1",
        research_root=str(tmp_path),
        overwrite=False,
        start_date="2026-06-15",
        end_date="2026-06-15",
    )

    signal_id, run_id, manifest_path = module._materialize_blend(args)

    assert (signal_id, run_id) == ("financial_rc_equal", "historical_equal_v1")
    combined = store.load_signal_run(signal_id, run_id).sort_values("instrument")
    assert combined["score"].tolist() == [0.5, 0.5]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["combine_type"] == "linear_blend"
    assert [
        (row["signal_id"], row["signal_run_id"], row["weight"])
        for row in manifest["inputs"]
    ] == [
        ("signal_60d", "run_60d", 0.5),
        ("signal_180d", "run_180d", 0.5),
    ]
    assert all(len(row["predictions_sha256"]) == 64 for row in manifest["inputs"])
    assert all(len(row["manifest_sha256"]) == 64 for row in manifest["inputs"])
    signal_manifest = store.load_manifest(signal_id, run_id)
    assert signal_manifest["predictions_sha256"] == store.signal_data_sha256(
        signal_id, run_id
    )
    assert signal_manifest["required_date_range"] == {
        "start": "2026-06-15",
        "end": "2026-06-15",
    }


def test_default_blend_run_id_pins_both_source_run_ids() -> None:
    module = _load_cli_module()
    first = module._default_blend_ids("s60", "r60", "s180", "r180_a", 0.5)
    second = module._default_blend_ids("s60", "r60", "s180", "r180_b", 0.5)
    assert first != second


def test_default_blend_run_id_pins_source_hashes_and_range() -> None:
    module = _load_cli_module()
    first = module._default_blend_ids(
        "s60",
        "r60",
        "s180",
        "r180",
        0.5,
        primary_sha256="a" * 64,
        secondary_sha256="b" * 64,
        start_date="2021-01-01",
        end_date="2025-12-31",
    )
    changed_hash = module._default_blend_ids(
        "s60",
        "r60",
        "s180",
        "r180",
        0.5,
        primary_sha256="c" * 64,
        secondary_sha256="b" * 64,
        start_date="2021-01-01",
        end_date="2025-12-31",
    )
    changed_range = module._default_blend_ids(
        "s60",
        "r60",
        "s180",
        "r180",
        0.5,
        primary_sha256="a" * 64,
        secondary_sha256="b" * 64,
        start_date="2022-01-01",
        end_date="2025-12-31",
    )
    assert first != changed_hash
    assert first != changed_range


def test_materialize_blend_rejects_missing_secondary_date(tmp_path: Path) -> None:
    module = _load_cli_module()
    store = SignalStore(tmp_path)
    _save_signal(store, "signal_60d", "run_60d", [1.0, -1.0])
    frame = store.load_signal_run("signal_60d", "run_60d").copy()
    frame["trade_date"] = "2026-06-16"
    frame["data_date"] = "2026-06-15"
    frame["signal_id"] = "signal_60d"
    frame["signal_run_id"] = "run_60d"
    store.save_signal_run(
        "signal_60d",
        "run_60d",
        pd.concat(
            [store.load_signal_run("signal_60d", "run_60d"), frame],
            ignore_index=True,
        ),
        overwrite=True,
    )
    _save_signal(store, "signal_180d", "run_180d", [0.0, 2.0])
    args = argparse.Namespace(
        signal_id="signal_60d",
        signal_run_id="run_60d",
        signal_id_2="signal_180d",
        signal_run_id_2="run_180d",
        blend_weight=0.5,
        blend_output_signal_id=None,
        blend_output_signal_run_id=None,
        research_root=str(tmp_path),
        overwrite=False,
        start_date="2026-06-15",
        end_date="2026-06-16",
    )

    with pytest.raises(ValueError, match="trade-date coverage mismatch"):
        module._materialize_blend(args)


def test_terminal_portfolio_analytics_fails_before_backtest_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_cli_module()

    class MustNotStartRunner:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("backtest runner started before terminal authorization")

    monkeypatch.setattr(module, "BacktestRunner", MustNotStartRunner)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "backtest_from_signal.py",
            "--signal-id", "terminal_signal",
            "--signal-run-id", "terminal_run",
            "--start-date", "2025-01-02",
            "--end-date", "2026-07-31",
            "--require-complete-accounting",
            "--corporate-action-artifact", "actions",
            "--canonical-data-root", str(tmp_path / "canonical"),
            "--liquidity-gate-mode", "reject",
            "--portfolio-analytics",
            "--benchmark-id", "csi800",
            "--benchmark-csv", str(tmp_path / "benchmark.csv"),
            "--holdout-start", "2025-01-02",
        ],
    )

    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2
    assert "--terminal-authorization-ref is required" in capsys.readouterr().err


def test_market_slice_requires_declared_holdout_before_runner_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_cli_module()

    class MustNotStartRunner:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("backtest runner started without a holdout boundary")

    monkeypatch.setattr(module, "BacktestRunner", MustNotStartRunner)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "backtest_from_signal.py",
            "--signal-id", "safe_signal",
            "--signal-run-id", "safe_run",
            "--start-date", "2021-01-04",
            "--end-date", "2024-12-31",
            "--require-complete-accounting",
            "--corporate-action-artifact", "actions",
            "--canonical-data-root", str(tmp_path / "canonical"),
            "--freeze-canonical-data-to", str(tmp_path / "frozen"),
            "--liquidity-gate-mode", "reject",
        ],
    )

    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2
    assert "requires --holdout-start" in capsys.readouterr().err
