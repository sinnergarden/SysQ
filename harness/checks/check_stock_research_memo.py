#!/usr/bin/env python3
"""Validate a Top10 fundamental signal-audit batch and its provenance."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from qsys.signal.top10_run import Top10RunError, validate_top10_run_artifact


class StockResearchAuditError(RuntimeError):
    """Raised when a stock-research audit is incomplete or inconsistent."""


SUPPORT = {"supported", "mixed", "conflicted", "insufficient_evidence"}
CONFIDENCE = {"high", "medium", "low", "unknown"}
RISK = {"low", "medium", "high", "critical", "unknown"}
IMPACT = {"none", "monitor", "reduce_confidence", "strongly_challenge"}
EVIDENCE_SCOPE = {"model_known", "audit_only"}
QUALITY_STATUS = {"supportive", "neutral", "warning", "unknown"}
QUALITY_CHECKS = {
    "earnings_quality",
    "cashflow_quality",
    "balance_sheet_quality",
    "accounting_or_oneoff",
}
CHALLENGE_BASIS = {
    "financial_conflict",
    "cashflow_quality",
    "balance_sheet",
    "accounting_governance",
    "negative_catalyst",
    "theme_without_earnings",
}
SOURCE_TYPES = {
    "financial_report",
    "exchange_announcement",
    "company_announcement",
    "regulatory_filing",
    "official_statistics",
    "reputable_news",
}
REQUIRED_MEMO_HEADINGS = (
    "## 模型逻辑的基本面验证",
    "### 收入与利润质量",
    "### 现金流、资产负债、应收与存货",
    "## 模型失效风险",
    "## 未来 180 日负面催化",
    "## 证据时间分层",
    "## 信号可靠性审计",
)
FORBIDDEN_MEMO_PATTERNS = (
    re.compile(
        r"^\s*(?:[-*]\s*)?(?:结论|recommendation)\s*[:：]\s*"
        r"(?:watch|pass|candidate|buy|sell)\b",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(
        r"^#{1,6}\s*(?:买卖建议|目标价|合理价格|fair value|recommendation)\s*$",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(r"^\s*[-*]\s*(?:目标价|合理价格)\s*[:：]", re.MULTILINE),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_date(value: Any, *, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise StockResearchAuditError(f"{field} must be YYYY-MM-DD") from exc


def _resolve_project_file(value: Any, *, field: str) -> Path:
    raw = str(value or "").strip()
    if not raw or "latest" in raw.lower():
        raise StockResearchAuditError(f"{field} requires an explicit non-latest path")
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path = path.resolve(strict=False)
    if path != PROJECT_ROOT and PROJECT_ROOT not in path.parents:
        raise StockResearchAuditError(f"{field} resolves outside project root")
    if path.is_symlink() or not path.is_file():
        raise StockResearchAuditError(f"{field} must be an existing regular file: {path}")
    return path


def _require_enum(row: dict[str, Any], field: str, values: set[str]) -> None:
    if row.get(field) not in values:
        raise StockResearchAuditError(
            f"{row.get('ts_code', '?')} {field} must be one of {sorted(values)}"
        )


def validate_stock_research_audit(
    top10_path: str | Path,
    audit_path: str | Path,
) -> dict[str, Any]:
    top10_file = Path(top10_path)
    if not top10_file.is_absolute():
        top10_file = PROJECT_ROOT / top10_file
    top10 = validate_top10_run_artifact(top10_file)

    audit_file = Path(audit_path)
    if not audit_file.is_absolute():
        audit_file = PROJECT_ROOT / audit_file
    if audit_file.is_symlink() or not audit_file.is_file():
        raise StockResearchAuditError(f"audit artifact must be a regular file: {audit_file}")
    try:
        payload = json.loads(audit_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StockResearchAuditError(f"cannot read audit artifact: {exc}") from exc

    if (
        payload.get("schema_version") != 1
        or payload.get("artifact_type") != "s180_top10_fundamental_signal_audit"
        or payload.get("status") != "complete"
    ):
        raise StockResearchAuditError("audit schema/artifact_type/status mismatch")

    declared_top10 = _resolve_project_file(
        payload.get("top10_artifact"), field="top10_artifact"
    )
    if declared_top10 != top10_file.resolve():
        raise StockResearchAuditError("declared top10_artifact differs from supplied artifact")
    if payload.get("top10_artifact_sha256") != _sha256(declared_top10):
        raise StockResearchAuditError("top10 artifact SHA-256 mismatch")

    expected = {
        "top10_run_identity": top10["run_identity"],
        "signal_date": top10["signal_date"],
        "data_date": top10["data_date"],
        "decision_date": top10["decision_date"],
        "model_bundle_hash": top10["model"]["bundle_hash"],
    }
    for field, value in expected.items():
        if payload.get(field) != value:
            raise StockResearchAuditError(f"{field} differs from Top10 artifact")

    data_date = _parse_date(payload.get("data_date"), field="data_date")
    audit_as_of = _parse_date(
        payload.get("audit_as_of_date"), field="audit_as_of_date"
    )
    if audit_as_of < data_date:
        raise StockResearchAuditError("audit_as_of_date cannot precede data_date")
    execution_date = _parse_date(top10["execution_date"], field="execution_date")
    if audit_as_of > execution_date or audit_as_of > date.today():
        raise StockResearchAuditError(
            "audit_as_of_date cannot exceed execution_date or the actual current date"
        )

    summary_path = _resolve_project_file(
        payload.get("summary_path"), field="summary_path"
    )
    if payload.get("summary_sha256") != _sha256(summary_path):
        raise StockResearchAuditError("summary SHA-256 mismatch")
    summary = summary_path.read_text(encoding="utf-8")

    rows = payload.get("audits")
    if not isinstance(rows, list) or len(rows) != 10:
        raise StockResearchAuditError("audit artifact must contain exactly ten audits")
    top_rows = top10["top10"]

    for source, row in zip(top_rows, rows):
        if not isinstance(row, dict):
            raise StockResearchAuditError("each audit row must be an object")
        for field in ("rank", "ts_code", "name"):
            if row.get(field) != source.get(field):
                raise StockResearchAuditError(
                    f"audit {field} differs from Top10 at rank {source['rank']}"
                )
        raw = row.get("raw_prediction")
        if (
            isinstance(raw, bool)
            or not isinstance(raw, (int, float))
            or not math.isfinite(raw)
            or float(raw) != float(source["raw_prediction"])
        ):
            raise StockResearchAuditError(
                f"raw_prediction differs from Top10 for {source['ts_code']}"
            )
        _require_enum(row, "fundamental_support", SUPPORT)
        _require_enum(row, "signal_confidence", CONFIDENCE)
        _require_enum(row, "risk_level", RISK)
        _require_enum(row, "signal_impact", IMPACT)

        risks = row.get("major_risks")
        if not isinstance(risks, list) or any(not str(item).strip() for item in risks):
            raise StockResearchAuditError(
                f"major_risks must be a list of non-empty strings for {source['ts_code']}"
            )
        post_signal_risks = row.get("post_signal_risks")
        if not isinstance(post_signal_risks, list) or any(
            not str(item).strip() for item in post_signal_risks
        ):
            raise StockResearchAuditError(
                f"post_signal_risks must be a list for {source['ts_code']}"
            )

        quality = row.get("financial_quality_checks")
        if not isinstance(quality, dict) or set(quality) != QUALITY_CHECKS:
            raise StockResearchAuditError(
                f"financial_quality_checks must cover {sorted(QUALITY_CHECKS)}"
            )
        for check_name, check in quality.items():
            if (
                not isinstance(check, dict)
                or check.get("status") not in QUALITY_STATUS
                or not str(check.get("summary") or "").strip()
            ):
                raise StockResearchAuditError(
                    f"invalid financial quality check {check_name} for {source['ts_code']}"
                )

        challenge_basis = row.get("challenge_basis")
        if (
            not isinstance(challenge_basis, list)
            or len(challenge_basis) != len(set(challenge_basis))
            or any(item not in CHALLENGE_BASIS for item in challenge_basis)
        ):
            raise StockResearchAuditError(
                f"challenge_basis must use non-valuation evidence for {source['ts_code']}"
            )
        if (
            row["fundamental_support"] == "conflicted"
            or row["signal_impact"] == "strongly_challenge"
        ) and not challenge_basis:
            raise StockResearchAuditError(
                f"conflicted/strongly_challenge requires challenge_basis for {source['ts_code']}"
            )

        evidence = row.get("evidence")
        if not isinstance(evidence, list):
            raise StockResearchAuditError(f"evidence must be a list for {source['ts_code']}")
        if not evidence and row["fundamental_support"] != "insufficient_evidence":
            raise StockResearchAuditError(
                f"non-insufficient audit needs evidence for {source['ts_code']}"
            )
        for item in evidence:
            if not isinstance(item, dict):
                raise StockResearchAuditError("evidence entries must be objects")
            if not str(item.get("source") or "").strip() or not str(
                item.get("claim") or ""
            ).strip():
                raise StockResearchAuditError("evidence requires source and claim")
            if item.get("source_type") not in SOURCE_TYPES:
                raise StockResearchAuditError("evidence requires a supported source_type")
            if not str(item.get("document_title") or "").strip() or not str(
                item.get("source_url_or_path") or ""
            ).strip():
                raise StockResearchAuditError(
                    "evidence requires document_title and source_url_or_path"
                )
            published = _parse_date(
                item.get("published_date"), field="evidence.published_date"
            )
            if published > audit_as_of:
                raise StockResearchAuditError(
                    f"future evidence used for {source['ts_code']}: {published}"
                )
            expected_scope = "model_known" if published <= data_date else "audit_only"
            scope = item.get("availability_scope")
            if scope not in EVIDENCE_SCOPE or scope != expected_scope:
                raise StockResearchAuditError(
                    f"wrong evidence scope for {source['ts_code']} on {published}"
                )

        memo_path = _resolve_project_file(row.get("memo_path"), field="memo_path")
        if row.get("memo_sha256") != _sha256(memo_path):
            raise StockResearchAuditError(f"memo SHA-256 mismatch for {source['ts_code']}")
        memo = memo_path.read_text(encoding="utf-8")
        for heading in REQUIRED_MEMO_HEADINGS:
            if heading not in memo:
                raise StockResearchAuditError(
                    f"memo lacks required section {heading!r} for {source['ts_code']}"
                )
        for pattern in FORBIDDEN_MEMO_PATTERNS:
            if pattern.search(memo):
                raise StockResearchAuditError(
                    f"memo contains forbidden investment-decision section for {source['ts_code']}"
                )
        for token in (
            source["ts_code"],
            str(top10["run_identity"]),
            str(top10["model"]["bundle_hash"]),
            str(row["fundamental_support"]),
            str(row["signal_impact"]),
        ):
            if token not in memo:
                raise StockResearchAuditError(
                    f"memo lacks required provenance/audit token {token!r}"
                )
        if source["ts_code"] not in summary:
            raise StockResearchAuditError(
                f"summary does not cover {source['ts_code']}"
            )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top10-artifact", required=True)
    parser.add_argument("--audit-artifact", required=True)
    args = parser.parse_args(argv)
    try:
        payload = validate_stock_research_audit(
            args.top10_artifact, args.audit_artifact
        )
    except (
        StockResearchAuditError,
        Top10RunError,
        OSError,
        ValueError,
        KeyError,
    ) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(
        json.dumps(
            {
                "status": "pass",
                "run_identity": payload["top10_run_identity"],
                "audit_as_of_date": payload["audit_as_of_date"],
                "audit_count": len(payload["audits"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
