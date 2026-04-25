from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from qsys.ops.model_registry import latest_shadow_model_is_usable, read_latest_shadow_model
from qsys.ops.state import atomic_write_json, load_json


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    return load_json(path) if path.exists() else {}


def _pointer_path(base_dir: Path, name: str) -> Path:
    return base_dir / "runs" / name


def _read_run_bundle(pointer_path: Path, summary_key: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[str]]:
    pointer = _read_json(pointer_path)
    issues: list[str] = []
    if not pointer:
        return {}, {}, pointer, [f"{pointer_path.name} missing"]

    manifest_path = Path(pointer.get("manifest_path", ""))
    manifest = _read_json(manifest_path) if manifest_path else {}
    summary_path_value = pointer.get(summary_key) or pointer.get("daily_summary_path")
    if not summary_path_value and manifest_path:
        summary_path_value = str(manifest_path.with_name("daily_summary.json"))
    summary = _read_json(Path(summary_path_value)) if summary_path_value else {}

    if not summary:
        issues.append(f"{pointer_path.name} summary missing")
    if not manifest:
        issues.append(f"{pointer_path.name} manifest missing")
    return summary, manifest, pointer, issues


def _read_shadow_account(base_dir: Path) -> tuple[dict[str, Any], list[str]]:
    account_path = base_dir / "shadow" / "account.json"
    if not account_path.exists():
        return {}, ["shadow account missing"]
    return _read_json(account_path), []


def _read_shadow_ledger(base_dir: Path) -> tuple[dict[str, Any], list[str]]:
    ledger_path = base_dir / "shadow" / "ledger.csv"
    if not ledger_path.exists():
        return {"exists": False, "row_count": 0, "last_run_id": None, "last_trade_date": None}, ["shadow ledger missing"]
    with ledger_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    last_row = rows[-1] if rows else {}
    return {
        "exists": True,
        "row_count": len(rows),
        "last_run_id": last_row.get("run_id"),
        "last_trade_date": last_row.get("trade_date"),
    }, []


def _build_daily_section(base_dir: Path) -> tuple[dict[str, Any], dict[str, Any], list[str], bool]:
    summary, manifest, pointer, issues = _read_run_bundle(_pointer_path(base_dir, "latest_shadow_daily.json"), "daily_summary_path")
    if not pointer:
        return {
            "run_id": None,
            "trade_date": None,
            "overall_status": None,
            "decision_status": None,
            "notification_status": None,
            "summary_path": None,
            "manifest_path": None,
        }, {}, issues, False

    daily = {
        "run_id": pointer.get("run_id"),
        "trade_date": pointer.get("trade_date"),
        "overall_status": pointer.get("overall_status") or summary.get("overall_status"),
        "decision_status": summary.get("decision_status"),
        "notification_status": summary.get("notification_status"),
        "summary_path": pointer.get("daily_summary_path"),
        "manifest_path": pointer.get("manifest_path"),
    }
    if daily["overall_status"] == "failed":
        issues.append("latest daily overall_status failed")
    if daily["notification_status"] == "failed":
        issues.append("daily notification failed")
    return daily, {"summary": summary, "manifest": manifest}, issues, True


def _build_weekly_section(base_dir: Path) -> tuple[dict[str, Any], dict[str, Any], list[str], bool]:
    summary, manifest, pointer, issues = _read_run_bundle(_pointer_path(base_dir, "latest_shadow_retrain.json"), "summary_path")
    if not pointer:
        return {
            "run_id": None,
            "trade_date": None,
            "overall_status": None,
            "decision_status": None,
            "notification_status": None,
            "summary_path": None,
            "manifest_path": None,
        }, {}, issues, False

    weekly = {
        "run_id": pointer.get("run_id"),
        "trade_date": pointer.get("trade_date"),
        "overall_status": pointer.get("overall_status") or summary.get("overall_status"),
        "decision_status": summary.get("decision_status"),
        "notification_status": summary.get("notification_status"),
        "summary_path": pointer.get("summary_path") or pointer.get("daily_summary_path") or (str(Path(pointer["manifest_path"]).with_name("daily_summary.json")) if pointer.get("manifest_path") else None),
        "manifest_path": pointer.get("manifest_path"),
    }
    if weekly["overall_status"] == "fallback":
        issues.append("weekly retrain fallback")
    if weekly["notification_status"] == "failed":
        issues.append("weekly notification failed")
    return weekly, {"summary": summary, "manifest": manifest}, issues, True


def _build_latest_model_section(base_dir: Path) -> tuple[dict[str, Any], list[str], bool]:
    pointer = read_latest_shadow_model(base_dir)
    if not pointer:
        return {
            "status": "missing",
            "model_name": None,
            "model_path": None,
            "mainline_object_name": None,
            "bundle_id": None,
            "train_run_id": None,
            "usable": False,
        }, ["latest model missing"], False

    usable = latest_shadow_model_is_usable(base_dir, pointer)
    latest_model = {
        "status": pointer.get("status"),
        "model_name": pointer.get("model_name"),
        "model_path": pointer.get("model_path"),
        "mainline_object_name": pointer.get("mainline_object_name"),
        "bundle_id": pointer.get("bundle_id"),
        "train_run_id": pointer.get("train_run_id"),
        "usable": usable,
    }
    issues = [] if usable else ["latest model unusable"]
    return latest_model, issues, True


def _resolve_overall_status(
    daily_seen: bool,
    weekly_seen: bool,
    model_seen: bool,
    daily_summary: dict[str, Any],
    weekly_summary: dict[str, Any],
    latest_model: dict[str, Any],
    account_seen: bool,
    ledger_seen: bool,
    issues: list[str],
) -> str:
    if not daily_seen and not weekly_seen and not model_seen and not account_seen and not ledger_seen:
        return "unknown"

    if model_seen and not latest_model.get("usable", False):
        return "failed"
    if daily_summary.get("overall_status") == "failed":
        return "failed"
    if weekly_summary.get("overall_status") == "failed":
        return "failed"

    degraded_markers = (
        "missing",
        "fallback",
        "failed",
        "unusable",
    )
    if daily_summary.get("notification_status") == "failed":
        return "degraded"
    if weekly_summary.get("notification_status") == "failed":
        return "degraded"
    if weekly_summary.get("overall_status") == "fallback":
        return "degraded"
    if not daily_seen or not weekly_seen or not account_seen or not ledger_seen:
        return "degraded"
    if any(any(marker in item for marker in degraded_markers) for item in issues):
        return "degraded"
    return "success"


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def build_latest_ops_status(base_dir: str | Path) -> dict[str, Any]:
    base_dir = Path(base_dir)
    issues: list[str] = []

    daily, daily_refs, daily_issues, daily_seen = _build_daily_section(base_dir)
    weekly, weekly_refs, weekly_issues, weekly_seen = _build_weekly_section(base_dir)
    latest_model, model_issues, model_seen = _build_latest_model_section(base_dir)
    account, account_issues = _read_shadow_account(base_dir)
    ledger, ledger_issues = _read_shadow_ledger(base_dir)

    issues.extend(daily_issues)
    issues.extend(weekly_issues)
    issues.extend(model_issues)
    issues.extend(account_issues)
    issues.extend(ledger_issues)

    overall_status = _resolve_overall_status(
        daily_seen,
        weekly_seen,
        model_seen,
        daily_refs.get("summary", {}),
        weekly_refs.get("summary", {}),
        latest_model,
        bool(account),
        bool(ledger.get("exists")),
        issues,
    )

    return {
        "checked_at": _utc_now_text(),
        "overall_status": overall_status,
        "daily": daily,
        "weekly_retrain": weekly,
        "latest_model": latest_model,
        "shadow_account": {
            "cash": account.get("cash"),
            "market_value": account.get("market_value"),
            "total_value": account.get("total_value"),
            "last_run_id": account.get("last_run_id"),
        } if account else {"cash": None, "market_value": None, "total_value": None, "last_run_id": None},
        "shadow_ledger": ledger,
        "issues": _dedupe(issues),
    }


def _format_text(payload: dict[str, Any]) -> str:
    lines = [f"checked_at: {payload['checked_at']}", f"overall_status: {payload['overall_status']}"]
    for key in ["daily", "weekly_retrain", "latest_model", "shadow_account", "shadow_ledger"]:
        lines.append(f"{key}: {json.dumps(payload[key], ensure_ascii=False, sort_keys=True)}")
    if payload.get("issues"):
        lines.append(f"issues: {json.dumps(payload['issues'], ensure_ascii=False)}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Check shadow ops status")
    parser.add_argument("--base-dir", default=".", help="Project base directory")
    parser.add_argument("--format", choices=["json", "text"], default="json", help="Output format")
    parser.add_argument("--write-latest", action="store_true", help="Write runs/latest_ops_status.json")
    args = parser.parse_args()

    payload = build_latest_ops_status(args.base_dir)
    if args.write_latest:
        atomic_write_json(Path(args.base_dir) / "runs" / "latest_ops_status.json", payload)

    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(_format_text(payload))


if __name__ == "__main__":
    main()
