"""Tests for qsys.signal.schema."""

from __future__ import annotations

import pytest

from qsys.signal.schema import SignalRecord, SignalSpec


class TestSignalSpec:
    def test_minimal(self) -> None:
        spec = SignalSpec(signal_id="alpha_v1_blended", kind="score")
        assert spec.signal_id == "alpha_v1_blended"

    def test_rejects_empty_signal_id(self) -> None:
        with pytest.raises(ValueError):
            SignalSpec(signal_id="", kind="score")

    def test_rejects_unknown_kind(self) -> None:
        with pytest.raises(ValueError, match="Unknown signal kind"):
            SignalSpec(signal_id="x", kind="invalid")

    def test_json_roundtrip(self) -> None:
        s1 = SignalSpec(signal_id="test", kind="zscore", description="test z-score")
        text = s1.to_json()
        s2 = SignalSpec.from_json(text)
        assert s1 == s2

    def test_from_dict_ignores_extra_fields(self) -> None:
        s = SignalSpec.from_dict({"signal_id": "x", "kind": "rank", "extra": "ignored"})
        assert s.signal_id == "x"


class TestSignalRecord:
    def test_minimal(self) -> None:
        r = SignalRecord(date="2026-05-01", instrument="000001.SZ", value=1.5)
        assert r.value == 1.5
