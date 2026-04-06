from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from qsys.live.ops_manifest import build_manifest_path, load_manifest
from qsys.live.ops_paths import DEFAULT_DAILY_ROOT, build_stage_paths

if TYPE_CHECKING:
    from qsys.live.account import RealAccount


ARTIFACT_CATEGORIES = {
    "report": "reports",
    "manifest": "manifests",
    "shadow_plan": "plans",
    "real_plan": "plans",
    "signal_basket": "signals",
    "shadow_order_intents": "order_intents",
    "real_order_intents": "order_intents",
    "shadow_real_sync_template": "plans",
    "real_real_sync_template": "plans",
    "summary": "reconciliation",
    "positions": "reconciliation",
    "real_trades": "reconciliation",
    "real_sync_snapshot": "snapshots",
}


@dataclass
class AccountSnapshot:
    account_name: str
    as_of_date: str
    cash: float
    total_assets: float
    position_count: int
    positions: list[dict[str, Any]]


@dataclass
class DailySummaryBundle:
    execution_date: str
    signal_date: str | None
    report_text: str
    report_markdown_path: str
    report_json_path: str
    snapshot_index_path: str


def _json_default(value: Any):
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _artifact_category(name: str) -> str:
    if name.startswith("signal_quality"):
        return "reports"
    return ARTIFACT_CATEGORIES.get(name, "misc")


def _copy_if_exists(source: str | Path, destination: Path) -> str | None:
    source_path = Path(source)
    if not source_path.exists() or source_path.is_dir():
        return None
    if source_path.resolve() == destination.resolve():
        destination.parent.mkdir(parents=True, exist_ok=True)
        return str(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination)
    return str(destination)


def _merge_payload(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_payload(merged[key], value)
            continue
        if isinstance(value, list) and isinstance(merged.get(key), list):
            merged_list = list(merged[key])
            for item in value:
                if item not in merged_list:
                    merged_list.append(item)
            merged[key] = merged_list
            continue
        merged[key] = value
    return merged


def _artifact_entry(*, category: str, path: str | None, exists: bool, copied_from: str | None = None) -> dict[str, Any]:
    payload = {
        "category": category,
        "path": path,
        "exists": exists,
    }
    if copied_from and path:
        copied_path = Path(copied_from)
        stored_path = Path(path)
        same_location = copied_path == stored_path
        if not same_location and copied_path.exists() and stored_path.exists():
            same_location = copied_path.resolve() == stored_path.resolve()
        if not same_location:
            payload["copied_from"] = copied_from
    return payload


def _artifact_display_path(payload: dict[str, Any]) -> str | None:
    return payload.get("path") or payload.get("archived_path") or payload.get("source_path")


def _compact_path(path: str | Path | None, *, archive_root: Path) -> str | None:
    if path is None:
        return None
    path_obj = Path(path)
    if not path_obj.is_absolute():
        return str(path_obj)
    for base in (archive_root.parent, archive_root):
        try:
            return str(path_obj.relative_to(base))
        except ValueError:
            continue
    return str(path_obj)


def extract_account_snapshot(account: RealAccount, *, date: str, account_name: str) -> dict[str, Any]:
    state = account.get_state(date, account_name=account_name)
    if not state:
        return {
            "account_name": account_name,
            "as_of_date": date,
            "status": "missing",
            "cash": None,
            "total_assets": None,
            "position_count": 0,
            "positions": [],
        }

    positions = []
    for symbol, payload in sorted((state.get("positions") or {}).items()):
        positions.append(
            {
                "symbol": symbol,
                "amount": int(payload.get("amount", payload.get("total_amount", 0)) or 0),
                "price": float(payload.get("price", 0.0) or 0.0),
                "cost_basis": float(payload.get("cost_basis", 0.0) or 0.0),
            }
        )

    snapshot = AccountSnapshot(
        account_name=account_name,
        as_of_date=str(state.get("date") or date),
        cash=float(state.get("cash", 0.0) or 0.0),
        total_assets=float(state.get("total_assets", 0.0) or 0.0),
        position_count=len(positions),
        positions=positions,
    )
    payload = asdict(snapshot)
    payload["status"] = "available"
    return payload


def archive_daily_artifacts(
    *,
    execution_date: str,
    signal_date: str | None,
    stage: str,
    artifacts: dict[str, str] | None,
    archive_root: str | Path = DEFAULT_DAILY_ROOT,
) -> dict[str, Any]:
    archive_root = Path(archive_root)
    day_root = archive_root / execution_date
    stage_root = day_root / stage
    index_path = day_root / "snapshot_index.json"

    existing = {}
    if index_path.exists():
        with open(index_path, "r", encoding="utf-8") as handle:
            existing = json.load(handle) or {}

    archived_artifacts: dict[str, Any] = {}
    stage_record: dict[str, Any] = {
        "stage_root": _compact_path(stage_root, archive_root=archive_root),
        "artifacts": archived_artifacts,
    }
    for name, original_path in (artifacts or {}).items():
        if name == "account_db":
            source_path = Path(original_path)
            archived_artifacts[name] = _artifact_entry(
                category="external_reference",
                path=_compact_path(source_path, archive_root=archive_root),
                exists=source_path.exists(),
            )
            continue

        category = _artifact_category(name)
        source_path = Path(original_path)
        archived_path = _copy_if_exists(source_path, stage_root / category / source_path.name)
        if name == "report":
            stage_record["report_path"] = _compact_path(archived_path or source_path, archive_root=archive_root)
            continue
        if name == "manifest":
            stage_record["manifest_path"] = _compact_path(archived_path or source_path, archive_root=archive_root)
            continue
        archived_artifacts[name] = _artifact_entry(
            category=category,
            path=_compact_path(archived_path or source_path, archive_root=archive_root),
            exists=archived_path is not None,
            copied_from=_compact_path(source_path, archive_root=archive_root),
        )

    existing.setdefault("execution_date", execution_date)
    existing["signal_date"] = signal_date
    existing.setdefault("archive_root", str(day_root))
    existing.setdefault("stages", {})
    existing["stages"][stage] = stage_record

    index_path.parent.mkdir(parents=True, exist_ok=True)
    with open(index_path, "w", encoding="utf-8") as handle:
        json.dump(existing, handle, indent=2, ensure_ascii=False, default=_json_default)

    return {
        "archive_root": str(day_root),
        "index_path": str(index_path),
        "stage_root": str(stage_root),
        "archived_artifacts": archived_artifacts,
    }


def _load_index(day_root: Path) -> dict[str, Any]:
    index_path = day_root / "snapshot_index.json"
    if not index_path.exists():
        return {}
    with open(index_path, "r", encoding="utf-8") as handle:
        return json.load(handle) or {}


def _load_day_manifest(day_root: Path) -> dict[str, Any]:
    index = _load_index(day_root)
    merged: dict[str, Any] = {}
    manifest_dirs = [
        day_root / "pre_open" / "manifests",
        day_root / "post_close" / "manifests",
    ]
    for candidate_dir in manifest_dirs:
        if candidate_dir.exists():
            files = sorted(candidate_dir.glob("daily_ops_manifest_*.json"))
            if files:
                merged = _merge_payload(merged, load_manifest(files[-1]))
    if merged:
        return merged
    execution_date = index.get("execution_date") or day_root.name
    candidate = build_manifest_path(day_root, execution_date)
    if candidate.exists():
        return load_manifest(candidate)
    return {}


def _build_digest_stage_payload(*, manifest: dict[str, Any], index_payload: dict[str, Any]) -> dict[str, Any]:
    manifest_stages = manifest.get("stages") or {}
    index_stages = index_payload.get("stages") or {}
    stage_names = sorted(set(manifest_stages) | set(index_stages))
    digest_stages: dict[str, Any] = {}
    for stage_name in stage_names:
        manifest_stage = manifest_stages.get(stage_name) or {}
        index_stage = index_stages.get(stage_name) or {}
        artifact_paths = {
            artifact_name: _artifact_display_path(artifact_payload) or ""
            for artifact_name, artifact_payload in sorted((index_stage.get("artifacts") or {}).items())
        }
        digest_stages[stage_name] = {
            "status": manifest_stage.get("status"),
            "blockers": list(manifest_stage.get("blockers") or []),
            "notes": list(manifest_stage.get("notes") or []),
            "summary": manifest_stage.get("summary") or {},
            "report_path": index_stage.get("report_path") or manifest_stage.get("report_path"),
            "manifest_path": index_stage.get("manifest_path"),
            "artifacts": artifact_paths,
        }
    return digest_stages


def _compact_prediction_line(label: str, summary: dict[str, Any]) -> str:
    if not summary:
        return f"- {label}: unavailable"
    symbols = ", ".join((summary.get("symbols") or [])[:5]) or "-"
    return (
        f"- {label}: status={summary.get('status', 'unknown')}, trades={summary.get('trades', 0)}, "
        f"buys={summary.get('buy_trades', 0)}, sells={summary.get('sell_trades', 0)}, "
        f"value={summary.get('total_value', 0.0):,.0f}, symbols={symbols}"
    )


def _compact_review_line(execution_date: str, manifest: dict[str, Any]) -> str:
    stage = ((manifest.get("stages") or {}).get("post_close") or {})
    summary = stage.get("summary") or {}
    reconciliation = summary.get("reconciliation") or {}
    signal_quality = summary.get("signal_quality") or {}
    cash_diff = ((reconciliation.get("cash") or {}).get("diff"))
    assets_diff = ((reconciliation.get("total_assets") or {}).get("diff"))
    quality_status = signal_quality.get("status") or signal_quality.get("data_quality_status") or "unknown"
    return (
        f"- {execution_date}: cash_diff={cash_diff}, total_assets_diff={assets_diff}, "
        f"signal_quality={quality_status}"
    )


def _build_report_text(
    *,
    execution_date: str,
    signal_date: str | None,
    manifest: dict[str, Any],
    index_payload: dict[str, Any],
    recent_reviews: list[str],
) -> str:
    stages = manifest.get("stages") or {}
    pre_open = (stages.get("pre_open") or {}).get("summary") or {}
    account_snapshots = pre_open.get("account_snapshots") or {}
    shadow_snapshot = account_snapshots.get("shadow") or {}

    lines = [
        f"# Daily Ops Digest {execution_date}",
        "",
        f"- signal_date: {signal_date or manifest.get('signal_date') or 'unknown'}",
        f"- execution_date: {execution_date}",
        f"- blockers: {len(manifest.get('blockers') or [])}",
        "",
        "## Current Day Predictions",
        _compact_prediction_line("shadow", pre_open.get("shadow_plan") or {}),
        _compact_prediction_line("real", pre_open.get("real_plan") or {}),
        "",
        "## Recent Review",
    ]
    lines.extend(recent_reviews or ["- No archived post-close reviews yet."])
    lines.extend(
        [
            "",
            "## Shadow Account",
            (
                f"- status={shadow_snapshot.get('status', 'missing')}, as_of={shadow_snapshot.get('as_of_date')}, "
                f"cash={shadow_snapshot.get('cash')}, total_assets={shadow_snapshot.get('total_assets')}, "
                f"positions={shadow_snapshot.get('position_count', 0)}"
            ),
            "",
            "## Detail Paths",
        ]
    )

    for stage_name, stage_payload in sorted((index_payload.get("stages") or {}).items()):
        if stage_payload.get("report_path"):
            lines.append(f"- {stage_name}.report: {stage_payload.get('report_path')}")
        if stage_payload.get("manifest_path"):
            lines.append(f"- {stage_name}.manifest: {stage_payload.get('manifest_path')}")
        for artifact_name, artifact_payload in sorted((stage_payload.get("artifacts") or {}).items()):
            artifact_path = _artifact_display_path(artifact_payload)
            lines.append(f"- {stage_name}.{artifact_name}: {artifact_path}")
    return "\n".join(lines) + "\n"


def _select_digest_dir(day_root: Path) -> Path:
    execution_date = day_root.name
    if (day_root / "post_close" / "manifests").exists() or (day_root / "post_close" / "reports").exists():
        stage_paths = build_stage_paths(execution_date, stage="post_close", daily_root=day_root.parent)
        return stage_paths.reports_dir
    stage_paths = build_stage_paths(execution_date, stage="pre_open", daily_root=day_root.parent)
    return stage_paths.reports_dir


def build_daily_summary_bundle(
    *,
    execution_date: str,
    archive_root: str | Path = DEFAULT_DAILY_ROOT,
    lookback_days: int = 3,
) -> DailySummaryBundle:
    archive_root = Path(archive_root)
    day_root = archive_root / execution_date
    index_payload = _load_index(day_root)
    manifest = _load_day_manifest(day_root)
    signal_date = index_payload.get("signal_date") or manifest.get("signal_date")

    recent_reviews = []
    reviewed = 0
    for candidate in sorted(archive_root.iterdir(), reverse=True) if archive_root.exists() else []:
        if not candidate.is_dir() or candidate.name > execution_date:
            continue
        candidate_manifest = _load_day_manifest(candidate)
        if not ((candidate_manifest.get("stages") or {}).get("post_close")):
            continue
        recent_reviews.append(_compact_review_line(candidate.name, candidate_manifest))
        reviewed += 1
        if reviewed >= lookback_days:
            break

    report_text = _build_report_text(
        execution_date=execution_date,
        signal_date=signal_date,
        manifest=manifest,
        index_payload=index_payload,
        recent_reviews=recent_reviews,
    )
    summary_dir = _select_digest_dir(day_root)
    summary_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = summary_dir / f"daily_ops_digest_{execution_date}.md"
    json_path = summary_dir / f"daily_ops_digest_{execution_date}.json"
    markdown_path.write_text(report_text, encoding="utf-8")

    json_payload = {
        "execution_date": execution_date,
        "signal_date": signal_date,
        "blockers": list(manifest.get("blockers") or []),
        "recent_reviews": recent_reviews,
        "snapshot_index_path": _compact_path(day_root / "snapshot_index.json", archive_root=archive_root),
        "stages": _build_digest_stage_payload(manifest=manifest, index_payload=index_payload),
        "report_text": report_text,
    }
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(json_payload, handle, indent=2, ensure_ascii=False, default=_json_default)

    return DailySummaryBundle(
        execution_date=execution_date,
        signal_date=signal_date,
        report_text=report_text,
        report_markdown_path=str(markdown_path),
        report_json_path=str(json_path),
        snapshot_index_path=str(day_root / "snapshot_index.json"),
    )
