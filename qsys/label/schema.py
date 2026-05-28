"""Label schema definitions for research artifacts.

A *label* is a ground-truth observation computed from future data, used as
the target variable for training and evaluation.  Labels are identified by
a unique ``label_id`` and described by a typed schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
import json
from typing import Any, Literal

LABEL_KINDS: set[str] = {
    "forward_return",
    "binary",
    "regression",
    "rank",
    "custom",
}


@dataclass(frozen=True)
class LabelSpec:
    """Immutable specification for a label definition.

    A LabelSpec describes *how* a label is computed and what it represents.
    It does **not** contain the computed label values — those live in a
    ``LabelStore`` (see ``qsys.label.store``).

    Parameters
    ----------
    label_id:
        Unique identifier (e.g. ``forward_return_5d``).
    kind:
        One of ``forward_return``, ``binary``, ``regression``, ``rank``,
        ``custom``.
    description:
        Human-readable explanation of what this label measures.
    horizon:
        Forward-looking horizon for ``forward_return`` labels (trading
        days).  ``None`` for other kinds.
    metadata:
        Optional extensible metadata dict.
    """

    label_id: str
    kind: str  # one of LABEL_KINDS
    description: str = ""
    horizon: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty_string(self.label_id, "label_id")
        if self.kind not in LABEL_KINDS:
            raise ValueError(
                f"Unknown label kind {self.kind!r}; expected one of {sorted(LABEL_KINDS)}"
            )
        if self.kind == "forward_return" and self.horizon is None:
            raise ValueError("forward_return labels must specify a horizon")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> LabelSpec:
        return cls(**{k: v for k, v in payload.items() if k in _LABEL_SPEC_FIELDS})

    @classmethod
    def from_json(cls, text: str) -> LabelSpec:
        return cls.from_dict(json.loads(text))

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


_LABEL_SPEC_FIELDS = frozenset(LabelSpec.__dataclass_fields__)


@dataclass
class LabelRecord:
    """A single label value for one instrument on one date.

    Parameters
    ----------
    date:
        Trading date (YYYY-MM-DD).
    instrument:
        Instrument code (e.g. ``000001.SZ``).
    value:
        Numeric label value.
    weight:
        Optional sample weight (default 1.0).
    """

    date: str
    instrument: str
    value: float
    weight: float = 1.0


def _require_non_empty_string(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string, got {value!r}")
