"""Signal schema definitions for research artifacts.

A *signal* is a numeric prediction produced by a model for a set of
instruments on a given date.  Signals are identified by a ``model_id`` +
``feature_set_id`` lineage and described by a typed schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
import json
from typing import Any

SIGNAL_KINDS: set[str] = {
    "score",
    "zscore",
    "rank",
    "probability",
    "raw",
    "custom",
}


@dataclass(frozen=True)
class SignalSpec:
    """Immutable specification for a signal definition.

    Parameters
    ----------
    signal_id:
        Unique identifier (e.g. ``alpha_v1_blended``).
    kind:
        One of ``score``, ``zscore``, ``rank``, ``probability``, ``raw``,
        ``custom``.
    description:
        Human-readable explanation.
    metadata:
        Optional extensible metadata dict.
    """

    signal_id: str
    kind: str  # one of SIGNAL_KINDS
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty_string(self.signal_id, "signal_id")
        if self.kind not in SIGNAL_KINDS:
            raise ValueError(
                f"Unknown signal kind {self.kind!r}; expected one of {sorted(SIGNAL_KINDS)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SignalSpec:
        return cls(**{k: v for k, v in payload.items() if k in _SIGNAL_SPEC_FIELDS})

    @classmethod
    def from_json(cls, text: str) -> SignalSpec:
        return cls.from_dict(json.loads(text))

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


_SIGNAL_SPEC_FIELDS = frozenset(SignalSpec.__dataclass_fields__)


@dataclass
class SignalRecord:
    """A single signal value for one instrument on one date.

    Parameters
    ----------
    date:
        Trading date (YYYY-MM-DD).
    instrument:
        Instrument code (e.g. ``000001.SZ``).
    value:
        Numeric signal value.
    """

    date: str
    instrument: str
    value: float


def _require_non_empty_string(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string, got {value!r}")
