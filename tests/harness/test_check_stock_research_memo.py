from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from harness.checks import check_stock_research_memo as checker
from qsys.signal.top10_run import validate_top10_run_artifact


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_valid_artifacts(tmp_path: Path) -> tuple[Path, Path, dict]:
    """Build the smallest complete Top10 -> candidate -> audit chain."""
    root = tmp_path
    candidate_path = (
        root
        / "outputs/2026-08-24/s180_top10/infer_s180_top10_content/candidate_run.json"
    )
    candidate_path.parent.mkdir(parents=True)
    rows = [
        {
            "rank": rank,
            "ts_code": f"{rank:06d}.SZ",
            "name": f"Example {rank}",
            "raw_prediction": float(100 - rank),
        }
        for rank in range(1, 11)
    ]
    candidate = {
        "signal_date": "2026-08-21",
        "data_date": "2026-08-20",
        "decision_date": "2026-08-24",
        "execution_date": "2026-08-25",
        "candidate_hash": "c" * 64,
        "feature_snapshot_hash": "f" * 64,
        "universe_hash": "u" * 64,
        "source": {"model_bundle_hash": "b" * 64},
        "blend": {"score_transform": "raw_model_prediction"},
        "data_quality": {"eligible_rows": 1800},
        "candidates": rows,
    }
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

    top10_path = candidate_path.with_name("top10_run.json")
    top10 = {
        "schema_version": 1,
        "artifact_type": "s180_top10_signal_run",
        "status": "complete",
        "strategy_id": "s180_top10",
        "run_identity": "r" * 64,
        "signal_date": candidate["signal_date"],
        "data_date": candidate["data_date"],
        "decision_date": candidate["decision_date"],
        "execution_date": candidate["execution_date"],
        "candidate_artifact": str(candidate_path.relative_to(root)),
        "candidate_artifact_sha256": _sha256(candidate_path),
        "model": {"bundle_hash": "b" * 64},
        "quality_gate": {
            "candidate_hash": "c" * 64,
            "score_transform": "raw_model_prediction",
        },
        "top10": rows,
    }
    top10_path.write_text(json.dumps(top10), encoding="utf-8")

    summary_path = root / "research_memos/s180_top10/2026-08-21/summary.md"
    summary_path.parent.mkdir(parents=True)
    summary_path.write_text(
        "Top10 fundamental signal audit summary\n"
        + "\n".join(row["ts_code"] for row in rows)
        + "\n",
        encoding="utf-8",
    )

    audits = []
    for row in rows:
        memo_path = root / f"research_memos/{row['ts_code']}/2026-08-21/stock_research_memo.md"
        memo_path.parent.mkdir(parents=True)
        memo = "\n".join(
            (
                f"# {row['ts_code']} signal reliability audit",
                f"ts_code: {row['ts_code']}",
                "run_identity: " + top10["run_identity"],
                "model_bundle_hash: " + top10["model"]["bundle_hash"],
                "fundamental_support: supported",
                "signal_impact: monitor",
                "## 模型逻辑的基本面验证",
                "### 收入与利润质量",
                "### 现金流、资产负债、应收与存货",
                "## 模型失效风险",
                "## 未来 180 日负面催化",
                "## 证据时间分层",
                "## 信号可靠性审计",
                "模型排序仍是主要 alpha 来源；本 memo 仅审计信号可靠性。",
            )
        )
        memo_path.write_text(memo + "\n", encoding="utf-8")
        audits.append(
            {
                **row,
                "fundamental_support": "supported",
                "signal_confidence": "medium",
                "risk_level": "low",
                "signal_impact": "monitor",
                "major_risks": ["Evidence set is limited to the supplied filing."],
                "post_signal_risks": ["No verified post-signal risk identified."],
                "financial_quality_checks": {
                    "earnings_quality": {
                        "status": "supportive",
                        "summary": "Reported earnings support the signal.",
                    },
                    "cashflow_quality": {
                        "status": "neutral",
                        "summary": "No material cash-flow contradiction found.",
                    },
                    "balance_sheet_quality": {
                        "status": "neutral",
                        "summary": "No material balance-sheet anomaly found.",
                    },
                    "accounting_or_oneoff": {
                        "status": "unknown",
                        "summary": "No separate one-off disclosure was available.",
                    },
                },
                "challenge_basis": [],
                "evidence": [
                    {
                        "source": "Example filing",
                        "claim": "Reported operating information is consistent with the model signal.",
                        "source_type": "financial_report",
                        "document_title": "Example 2026 interim report",
                        "source_url_or_path": "https://example.test/filing.pdf",
                        "published_date": "2026-08-18",
                        "availability_scope": "model_known",
                    }
                ],
                "memo_path": str(memo_path.relative_to(root)),
                "memo_sha256": _sha256(memo_path),
            }
        )
    audit_path = root / "research_memos/s180_top10/2026-08-21/audit.json"
    audit = {
        "schema_version": 1,
        "artifact_type": "s180_top10_fundamental_signal_audit",
        "status": "complete",
        "top10_artifact": str(top10_path.relative_to(root)),
        "top10_artifact_sha256": _sha256(top10_path),
        "top10_run_identity": top10["run_identity"],
        "signal_date": top10["signal_date"],
        "data_date": top10["data_date"],
        "decision_date": top10["decision_date"],
        "model_bundle_hash": top10["model"]["bundle_hash"],
        "audit_as_of_date": "2026-08-24",
        "summary_path": str(summary_path.relative_to(root)),
        "summary_sha256": _sha256(summary_path),
        "audits": audits,
    }
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    return top10_path, audit_path, audit


@pytest.fixture
def valid_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(checker, "PROJECT_ROOT", tmp_path)
    top10_path, audit_path, audit = _write_valid_artifacts(tmp_path)
    return top10_path, audit_path, audit


def test_valid_top10_fundamental_audit_passes(valid_chain):
    top10_path, audit_path, _ = valid_chain
    assert validate_top10_run_artifact(top10_path)["run_identity"] == "r" * 64
    assert checker.validate_stock_research_audit(top10_path, audit_path)["status"] == "complete"


@pytest.mark.parametrize("field", ["rank", "raw_prediction"])
def test_rank_or_raw_prediction_mismatch_fails(valid_chain, field: str):
    top10_path, audit_path, audit = valid_chain
    audit["audits"][0][field] = 99 if field == "rank" else -999.0
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    with pytest.raises(checker.StockResearchAuditError, match=f"audit {field}|raw_prediction"):
        checker.validate_stock_research_audit(top10_path, audit_path)


@pytest.mark.parametrize(
    ("published_date", "availability_scope"),
    [("2026-08-25", "audit_only"), ("2026-08-18", "audit_only")],
)
def test_future_or_wrong_availability_scope_fails(
    valid_chain, published_date: str, availability_scope: str
):
    top10_path, audit_path, audit = valid_chain
    audit["audits"][0]["evidence"][0]["published_date"] = published_date
    audit["audits"][0]["evidence"][0]["availability_scope"] = availability_scope
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    with pytest.raises(checker.StockResearchAuditError, match="evidence|future"):
        checker.validate_stock_research_audit(top10_path, audit_path)


@pytest.mark.parametrize("conclusion", ["watch", "pass", "candidate"])
def test_legacy_investment_conclusions_are_rejected(valid_chain, conclusion: str):
    top10_path, audit_path, audit = valid_chain
    memo_path = Path(audit["audits"][0]["memo_path"])
    memo_path = checker.PROJECT_ROOT / memo_path
    memo_path.write_text(
        memo_path.read_text(encoding="utf-8") + f"\n结论: {conclusion}\n",
        encoding="utf-8",
    )
    audit["audits"][0]["memo_sha256"] = _sha256(memo_path)
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    with pytest.raises(checker.StockResearchAuditError, match="forbidden"):
        checker.validate_stock_research_audit(top10_path, audit_path)


def test_audit_as_of_after_execution_date_fails(valid_chain):
    top10_path, audit_path, audit = valid_chain
    audit["audit_as_of_date"] = "2026-08-26"
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    with pytest.raises(checker.StockResearchAuditError, match="audit_as_of_date"):
        checker.validate_stock_research_audit(top10_path, audit_path)


def test_audit_as_of_after_actual_current_date_fails(valid_chain):
    top10_path, audit_path, audit = valid_chain
    top10 = json.loads(top10_path.read_text(encoding="utf-8"))
    top10["execution_date"] = (date.today() + timedelta(days=5)).isoformat()
    top10_path.write_text(json.dumps(top10), encoding="utf-8")
    audit["top10_artifact_sha256"] = _sha256(top10_path)
    audit["audit_as_of_date"] = (date.today() + timedelta(days=1)).isoformat()
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    with pytest.raises(checker.StockResearchAuditError, match="audit_as_of_date"):
        checker.validate_stock_research_audit(top10_path, audit_path)


def test_conflicted_signal_requires_challenge_basis(valid_chain):
    top10_path, audit_path, audit = valid_chain
    audit["audits"][0]["fundamental_support"] = "conflicted"
    audit["audits"][0]["challenge_basis"] = []
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    with pytest.raises(checker.StockResearchAuditError, match="challenge_basis"):
        checker.validate_stock_research_audit(top10_path, audit_path)


def test_missing_financial_quality_check_fails(valid_chain):
    top10_path, audit_path, audit = valid_chain
    audit["audits"][0]["financial_quality_checks"].pop("cashflow_quality")
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    with pytest.raises(checker.StockResearchAuditError, match="financial_quality_checks"):
        checker.validate_stock_research_audit(top10_path, audit_path)


@pytest.mark.parametrize("field", ["source_type", "document_title", "source_url_or_path"])
def test_unverifiable_evidence_source_fails(valid_chain, field: str):
    top10_path, audit_path, audit = valid_chain
    audit["audits"][0]["evidence"][0].pop(field)
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    with pytest.raises(checker.StockResearchAuditError, match="evidence"):
        checker.validate_stock_research_audit(top10_path, audit_path)
