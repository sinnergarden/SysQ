"""Tests for qsys.artifacts.writer — write_artifact and write_artifacts."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from qsys.artifacts.contracts import SignalArtifact, PortfolioSnapshot, RunManifest
from qsys.artifacts.writer import write_artifact, write_artifacts, sidecar_path


class TestWriteArtifact:
    """Single-object artifact writes a JSON dict."""

    def setup_method(self) -> None:
        self.tmp = tempfile.mktemp(suffix=".adr7.json")

    def teardown_method(self) -> None:
        if Path(self.tmp).exists():
            os.unlink(self.tmp)

    def test_writes_dict(self) -> None:
        snap = PortfolioSnapshot(
            trade_date="2026-05-18", account_id="test", strategy_id="test",
            cash=100.0, market_value=900.0, total_asset=1000.0,
        )
        write_artifact(snap, self.tmp)
        data = json.loads(Path(self.tmp).read_text())
        assert isinstance(data, dict), f"Expected dict, got {type(data)}"
        assert data["total_asset"] == 1000.0
        assert data["account_id"] == "test"

    def test_none_preserved(self) -> None:
        """None values become JSON null rather than being omitted."""
        snap = PortfolioSnapshot(
            trade_date="2026-05-18", account_id="test", strategy_id="test",
            cash=100.0, market_value=900.0, total_asset=1000.0,
        )
        write_artifact(snap, self.tmp)
        data = json.loads(Path(self.tmp).read_text())
        assert data["daily_pnl"] is None
        assert data["daily_return"] is None

    def test_not_available_preserved(self) -> None:
        """not_available sentinel appears as string in JSON."""
        sig = SignalArtifact(
            trade_date="2026-05-18", strategy_id="test",
            instrument="000001.SZ", score=1.0, rank=1,
        )
        write_artifact(sig, self.tmp)
        data = json.loads(Path(self.tmp).read_text())
        assert data["candidate_id"] == "not_available"
        assert data["model_version"] == "not_available"
        assert data["signal_version"] == "not_available"


class TestWriteArtifacts:
    """Multi-row artifact writes a JSON array."""

    def setup_method(self) -> None:
        self.tmp = tempfile.mktemp(suffix=".adr7.json")

    def teardown_method(self) -> None:
        if Path(self.tmp).exists():
            os.unlink(self.tmp)

    def test_writes_array(self) -> None:
        arts = [
            SignalArtifact(trade_date="2026-05-18", strategy_id="test", instrument="000001.SZ", score=1.5, rank=1),
            SignalArtifact(trade_date="2026-05-18", strategy_id="test", instrument="000002.SZ", score=0.5, rank=2),
            SignalArtifact(trade_date="2026-05-18", strategy_id="test", instrument="000003.SZ", score=-0.2, rank=3),
        ]
        write_artifacts(arts, self.tmp)
        data = json.loads(Path(self.tmp).read_text())
        assert isinstance(data, list), f"Expected list, got {type(data)}"
        assert len(data) == 3
        assert data[0]["instrument"] == "000001.SZ"
        assert data[0]["score"] == 1.5
        assert data[2]["instrument"] == "000003.SZ"

    def test_no_overwrite(self) -> None:
        """Calling write_artifacts once writes ALL rows, not just the last."""
        arts = [
            SignalArtifact(trade_date="2026-05-18", strategy_id="test", instrument="000001.SZ", score=1.0, rank=1),
            SignalArtifact(trade_date="2026-05-18", strategy_id="test", instrument="000002.SZ", score=0.5, rank=2),
        ]
        write_artifacts(arts, self.tmp)
        data = json.loads(Path(self.tmp).read_text())
        assert len(data) == 2
        assert data[0]["instrument"] == "000001.SZ"
        assert data[1]["instrument"] == "000002.SZ"

    def test_empty_list(self) -> None:
        write_artifacts([], self.tmp)
        data = json.loads(Path(self.tmp).read_text())
        assert isinstance(data, list)
        assert len(data) == 0

    def test_single_element(self) -> None:
        arts = [SignalArtifact(trade_date="2026-05-18", strategy_id="test", instrument="000001.SZ", score=1.0, rank=1)]
        write_artifacts(arts, self.tmp)
        data = json.loads(Path(self.tmp).read_text())
        assert len(data) == 1
        assert data[0]["rank"] == 1

    def test_array_none_preserved(self) -> None:
        """None fields are null in JSON array elements."""
        arts = [SignalArtifact(trade_date="2026-05-18", strategy_id="test", instrument="000001.SZ", score=1.0, rank=1)]
        write_artifacts(arts, self.tmp)
        data = json.loads(Path(self.tmp).read_text())
        assert data[0]["raw_prediction"] is None
        assert data[0]["normalized_score"] is None


class TestSidecarPath:
    def test_csv_to_adr7(self) -> None:
        result = sidecar_path("predictions_2026-05-18.csv")
        assert str(result) == "predictions_2026-05-18.adr7.json"

    def test_with_path(self) -> None:
        result = sidecar_path(Path("/tmp/data/predictions_2026-05-18.csv"))
        assert str(result) == "/tmp/data/predictions_2026-05-18.adr7.json"

    def test_no_extension(self) -> None:
        result = sidecar_path("predictions_2026-05-18")
        assert str(result) == "predictions_2026-05-18.adr7.json"
