"""Contract tests for cached signal materialization in the backtest CLI."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import pandas as pd

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
    signal_manifest = store.load_manifest(signal_id, run_id)
    assert signal_manifest["predictions_sha256"] == store.signal_data_sha256(
        signal_id, run_id
    )


def test_default_blend_run_id_pins_both_source_run_ids() -> None:
    module = _load_cli_module()
    first = module._default_blend_ids("s60", "r60", "s180", "r180_a", 0.5)
    second = module._default_blend_ids("s60", "r60", "s180", "r180_b", 0.5)
    assert first != second
