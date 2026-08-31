from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from qsys.backtest.accounting import write_corporate_action_artifact
from qsys.backtest.strategy_runner import BacktestRunner
from qsys.research.backtest_validation import validate_complete_accounting_backtest
from qsys.signal.store import SignalStore


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_universe(root: Path, day: str) -> str:
    name = "fixture_pit"
    artifact = root / "universes" / name
    artifact.mkdir(parents=True)
    membership = pd.DataFrame([{
        "index_code": "TEST",
        "instrument": "A.SZ",
        "effective_from": "20200101",
        "effective_to": "20261231",
        "source": "fixture",
        "source_date": day.replace("-", ""),
        "source_version": "v1",
    }])
    membership.to_parquet(artifact / "membership.parquet", index=False)
    (artifact / "manifest.json").write_text(
        json.dumps({
            "universe_id": name,
            "membership_sha256": _sha256(artifact / "membership.parquet"),
            "raw_source_hash": "fixture",
            "source": "fixture",
            "source_date": day,
            "n_snapshots": 1,
            "snapshot_date_range": ["20200101", "20261231"],
            "n_unique_instruments": 1,
            "n_membership_spans": 1,
            "description": "fixture PIT universe",
        }, sort_keys=True),
        encoding="utf-8",
    )
    return name


def _build_complete_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    terminal: bool = False,
) -> tuple[Path, Path, Path]:
    day = "2026-06-15" if terminal else "2024-12-31"
    data_date = "2026-06-12" if terminal else "2024-12-30"
    holdout = "2025-01-02"
    authorization = "fixture-terminal-authorization" if terminal else None
    SignalStore(tmp_path).save_signal_run(
        "sig", "run",
        pd.DataFrame([{
            "trade_date": day,
            "data_date": data_date,
            "instrument": "A.SZ",
            "signal_id": "sig",
            "signal_run_id": "run",
            "score": 1.0,
        }]),
        overwrite=True,
    )
    raw = tmp_path / "actions.raw"
    raw.write_bytes(b"immutable fixture source")
    write_corporate_action_artifact(
        pd.DataFrame(), tmp_path, artifact_name="actions",
        source_raw_path=str(raw),
        source_raw_artifact_sha256=_sha256(raw),
    )
    universe_name = _write_universe(tmp_path, day)
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    market_dates = pd.bdate_range(end=day, periods=6).strftime("%Y-%m-%d")
    pd.DataFrame({
        "trade_date": market_dates,
        "open": [10.0] * 6,
        "close": [10.0] * 6,
        "paused": [0] * 6,
        "high_limit": [20.0] * 6,
        "low_limit": [1.0] * 6,
        "amount": [100_000_000.0] * 6,
        "factor": [1.0] * 6,
    }).to_feather(canonical / "A.SZ.feather")
    frozen = tmp_path / "frozen"
    output = tmp_path / "backtest"
    monkeypatch.setattr(
        "qsys.backtest.strategy_runner._resolve_trading_dates",
        lambda start, end: [day],
    )
    BacktestRunner().run_from_signal_cache(
        signal_id="sig",
        signal_run_id="run",
        start_date=day,
        end_date=day,
        research_root=tmp_path,
        output_dir=output,
        top_n=1,
        commission=0.0,
        stamp_duty=0.0,
        min_commission=0.0,
        slippage=0.0,
        corporate_action_artifact="actions",
        canonical_data_root=canonical,
        freeze_canonical_data_to=frozen,
        pit_universe_artifact=universe_name,
        holdout_start=holdout,
        terminal_authorization_ref=authorization,
        max_participation_rate=0.10,
        liquidity_gate_mode="reject",
        require_complete_accounting=True,
    )
    config = tmp_path / "validation.yaml"
    config.write_text(yaml.safe_dump({
        "schema_version": "complete_accounting_backtest_validation_config_v1",
        "backtest_dir": str(output),
        "research_root": str(tmp_path),
        "expected_manifest_sha256": _sha256(output / "manifest.json"),
        "holdout_start": holdout,
        "terminal_authorization_ref": authorization,
        "accounting_identity_tolerance": 1e-6,
    }, sort_keys=False), encoding="utf-8")
    return config, output, frozen


def test_validates_complete_accounting_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _, _ = _build_complete_run(tmp_path, monkeypatch)

    receipt = validate_complete_accounting_backtest(config)

    assert receipt["status"] == "pass"
    assert receipt["holdout_consumed"] is False
    assert receipt["trading_day_count"] == 1
    assert receipt["signal_row_count"] == 1
    assert receipt["market_file_count"] == 1


def test_rejects_tampered_accounting_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, output, _ = _build_complete_run(tmp_path, monkeypatch)
    with (output / "daily_summary.csv").open("ab") as handle:
        handle.write(b"\n")

    with pytest.raises(ValueError, match="daily_summary SHA256 mismatch"):
        validate_complete_accounting_backtest(config)


def test_rejects_consumed_holdout_without_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, output, _ = _build_complete_run(tmp_path, monkeypatch, terminal=True)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["terminal_holdout"]["terminal_authorization_ref"] = None
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    payload = yaml.safe_load(config.read_text(encoding="utf-8"))
    payload["expected_manifest_sha256"] = _sha256(manifest_path)
    config.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="consumed without authorization"):
        validate_complete_accounting_backtest(config)


def test_rejects_tampered_market_slice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _, frozen = _build_complete_run(tmp_path, monkeypatch)
    with (frozen / "A.SZ.feather").open("ab") as handle:
        handle.write(b"tamper")

    with pytest.raises(ValueError, match="market-data slice file lineage mismatch"):
        validate_complete_accounting_backtest(config)
