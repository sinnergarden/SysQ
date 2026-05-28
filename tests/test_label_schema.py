"""Tests for qsys.label.schema."""

from __future__ import annotations

import pytest

from qsys.label.schema import LabelRecord, LabelSpec


class TestLabelSpec:
    def test_minimal(self) -> None:
        spec = LabelSpec(label_id="fr_5d", kind="forward_return", horizon=5)
        assert spec.label_id == "fr_5d"
        assert spec.horizon == 5

    def test_rejects_empty_label_id(self) -> None:
        with pytest.raises(ValueError):
            LabelSpec(label_id="", kind="forward_return", horizon=5)

    def test_rejects_unknown_kind(self) -> None:
        with pytest.raises(ValueError, match="Unknown label kind"):
            LabelSpec(label_id="x", kind="invalid")

    def test_forward_return_requires_horizon(self) -> None:
        with pytest.raises(ValueError, match="horizon"):
            LabelSpec(label_id="x", kind="forward_return")

    def test_non_forward_allows_no_horizon(self) -> None:
        spec = LabelSpec(label_id="x", kind="binary")
        assert spec.horizon is None

    def test_json_roundtrip(self) -> None:
        s1 = LabelSpec(label_id="fr_10d", kind="forward_return", horizon=10, description="10d return")
        text = s1.to_json()
        s2 = LabelSpec.from_json(text)
        assert s1 == s2

    def test_from_dict_ignores_extra_fields(self) -> None:
        s = LabelSpec.from_dict({"label_id": "x", "kind": "rank", "extra": "ignored"})
        assert s.label_id == "x"


class TestLabelRecord:
    def test_minimal(self) -> None:
        r = LabelRecord(date="2026-05-01", instrument="000001.SZ", value=0.05)
        assert r.weight == 1.0

    def test_with_weight(self) -> None:
        r = LabelRecord(date="2026-05-01", instrument="000001.SZ", value=0.05, weight=0.8)
        assert r.weight == 0.8
