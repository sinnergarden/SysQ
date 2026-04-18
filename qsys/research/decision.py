from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
import json
from pathlib import Path
from typing import Any

import yaml

from qsys.research.manifest import REPO_ROOT
from qsys.research.schemas import ManifestValidationError


class PromotionStatus(str, Enum):
    REJECT = "reject"
    RESEARCH_ONLY = "research_only"
    CANDIDATE = "candidate"
    SHADOW_READY = "shadow_ready"
    PARK = "park"


SUPPORTED_SUBJECT_TYPES = {"mainline_object", "experiment_run"}
DEFAULT_DECISIONS_DIR = REPO_ROOT / "research" / "decisions"


@dataclass(frozen=True)
class DecisionRecord:
    decision_id: str
    subject_type: str
    subject_id: str
    status: str
    reason: str
    evidence: dict[str, Any]
    created_at: str
    updated_at: str
    author: str
    notes: list[str]

    def __post_init__(self) -> None:
        _require_non_empty_string(self.decision_id, "decision_id")
        _require_non_empty_string(self.subject_type, "subject_type")
        if self.subject_type not in SUPPORTED_SUBJECT_TYPES:
            raise ManifestValidationError(f"Unsupported subject_type: {self.subject_type}")
        _require_non_empty_string(self.subject_id, "subject_id")
        _require_non_empty_string(self.status, "status")
        if self.status not in {status.value for status in PromotionStatus}:
            raise ManifestValidationError(f"Unsupported decision status: {self.status}")
        _require_non_empty_string(self.reason, "reason")
        if not isinstance(self.evidence, dict) or not self.evidence:
            raise ManifestValidationError("evidence must be a non-empty mapping")
        _require_non_empty_string(self.created_at, "created_at")
        _require_non_empty_string(self.updated_at, "updated_at")
        _require_non_empty_string(self.author, "author")
        _require_string_list(self.notes, "notes")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DecisionRecord":
        return cls(
            decision_id=_required(payload, "decision_id"),
            subject_type=_required(payload, "subject_type"),
            subject_id=_required(payload, "subject_id"),
            status=_required(payload, "status"),
            reason=_required(payload, "reason"),
            evidence=_required(payload, "evidence"),
            created_at=_required(payload, "created_at"),
            updated_at=_required(payload, "updated_at"),
            author=_required(payload, "author"),
            notes=_required(payload, "notes"),
        )

    @classmethod
    def from_json(cls, text: str) -> "DecisionRecord":
        return cls.from_dict(json.loads(text))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)



def parse_decision_record(path: str | Path) -> DecisionRecord:
    payload = _load_payload(path)
    return DecisionRecord.from_dict(payload)



def load_decision_record(path: str | Path) -> DecisionRecord:
    return parse_decision_record(path)



def load_decision_records(decisions_dir: str | Path = DEFAULT_DECISIONS_DIR) -> dict[str, DecisionRecord]:
    records: dict[str, DecisionRecord] = {}
    base_dir = Path(decisions_dir)
    if not base_dir.exists():
        return records
    for path in sorted(base_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".yaml", ".yml", ".json"}:
            continue
        record = parse_decision_record(path)
        if record.decision_id in records:
            raise ManifestValidationError(f"Duplicate decision_id: {record.decision_id}")
        records[record.decision_id] = record
    return records



def find_latest_decision(subject_type: str, subject_id: str, decisions_dir: str | Path = DEFAULT_DECISIONS_DIR) -> DecisionRecord | None:
    matches = [
        record
        for record in load_decision_records(decisions_dir).values()
        if record.subject_type == subject_type and record.subject_id == subject_id
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda record: (record.updated_at, record.created_at, record.decision_id))[-1]



def resolve_subject_decision(
    *,
    subject_type: str,
    subject_ids: list[str] | tuple[str, ...],
    decisions_dir: str | Path = DEFAULT_DECISIONS_DIR,
) -> DecisionRecord | None:
    candidate_ids = [item for item in subject_ids if isinstance(item, str) and item.strip()]
    if not candidate_ids:
        return None
    records = load_decision_records(decisions_dir)
    matches = [
        record
        for record in records.values()
        if record.subject_type == subject_type and record.subject_id in candidate_ids
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda record: (record.updated_at, record.created_at, record.decision_id))[-1]



def decision_payload(record: DecisionRecord | None) -> dict[str, Any]:
    if record is None:
        return {
            "status": "not_decided",
            "reason": "not_decided",
            "decision_id": None,
            "subject_type": None,
            "subject_id": None,
            "evidence": {},
            "author": None,
            "updated_at": None,
            "notes": [],
        }
    payload = record.to_dict()
    payload["status"] = record.status
    return payload



def _load_payload(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Decision file not found: {source}")
    raw = source.read_text(encoding="utf-8")
    if source.suffix.lower() == ".json":
        payload = json.loads(raw)
    elif source.suffix.lower() in {".yaml", ".yml"}:
        payload = yaml.safe_load(raw)
    else:
        raise ManifestValidationError(f"Unsupported decision format: {source}")
    if not isinstance(payload, dict):
        raise ManifestValidationError(f"Decision file must decode to a mapping: {source}")
    return payload



def _required(payload: dict[str, Any], field_name: str) -> Any:
    if field_name not in payload:
        raise ManifestValidationError(f"Missing required field: {field_name}")
    return payload[field_name]



def _require_non_empty_string(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ManifestValidationError(f"{field_name} must be a non-empty string")



def _require_string_list(value: Any, field_name: str) -> None:
    if not isinstance(value, list):
        raise ManifestValidationError(f"{field_name} must be a list")
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ManifestValidationError(f"{field_name}[{index}] must be a non-empty string")
