from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import Any

from qsys.ops.state import load_json

TOP_N_PREDICTIONS = 5
TOP_N_REASONS = 3


def _load_json_if_exists(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return load_json(path)


def _project_root_from_manifest(manifest_path: Path) -> Path:
    parts = manifest_path.parts
    if "runs" in parts:
        return Path(*parts[: parts.index("runs")])
    return manifest_path.parent


def _artifact_path(manifest_path: Path, value: Any) -> Path | None:
    if not value:
        return None
    path = Path(str(value))
    if path.is_absolute():
        return path
    return _project_root_from_manifest(manifest_path) / path


def _read_csv_rows(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _fmt_number(value: Any, digits: int = 2) -> str:
    if value in (None, ""):
        return "N/A"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_bool(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return "N/A" if value in (None, "") else str(value)


def _status_icon(status: str) -> str:
    return "✅" if status == "success" else "⚠️" if status in {"fallback", "skipped"} else "❌"


def _safe_get(payload: dict[str, Any] | None, key: str, default: str = "N/A") -> Any:
    if not payload:
        return default
    return payload.get(key, default)


def _top_predictions(predictions_rows: list[dict[str, str]]) -> list[str]:
    scored: list[tuple[float, str, str]] = []
    for row in predictions_rows:
        instrument = row.get("instrument") or "N/A"
        score_text = row.get("score") or ""
        try:
            score_value = float(score_text)
        except ValueError:
            continue
        scored.append((score_value, instrument, score_text))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [f" {instrument} score={_fmt_number(score, 4)}" for score, instrument, _ in scored[:TOP_N_PREDICTIONS]]


def _top_reasons(order_rows: list[dict[str, str]], execution_summary: dict[str, Any] | None, audit_rows: list[dict[str, str]] | None = None) -> list[str]:
    counter: Counter[str] = Counter()
    for row in order_rows:
        reason = (row.get("reason") or "").strip()
        if reason:
            counter[reason] += 1
    if not counter and audit_rows:
        for row in audit_rows:
            reason = (row.get("reason") or "").strip()
            if reason:
                counter[reason] += 1
    if not counter and execution_summary:
        error = str(execution_summary.get("error") or "").strip()
        if error:
            counter[error] += 1
        for note in execution_summary.get("notes") or []:
            text = str(note).strip()
            if text.startswith("blocked_") or text.startswith("rejected_"):
                counter[text] += 1
    return [f" {reason} x{count}" for reason, count in counter.most_common(TOP_N_REASONS)]


def build_shadow_daily_digest(summary_path: str | Path, manifest_path: str | Path) -> str:
    summary_path = Path(summary_path)
    manifest_path = Path(manifest_path)
    summary = load_json(summary_path)
    manifest = load_json(manifest_path)
    stage_status = manifest.get("stage_status") or {}
    archive_artifacts = ((stage_status.get("archive_report") or {}).get("artifact_pointers") or {})

    data_status = _load_json_if_exists(_artifact_path(manifest_path, archive_artifacts.get("data_status_path")))
    feature_status = _load_json_if_exists(_artifact_path(manifest_path, archive_artifacts.get("feature_status_path")))
    selected_model = _load_json_if_exists(_artifact_path(manifest_path, archive_artifacts.get("selected_model_path")))
    inference_summary = _load_json_if_exists(_artifact_path(manifest_path, archive_artifacts.get("inference_summary_path")))
    execution_summary = _load_json_if_exists(_artifact_path(manifest_path, archive_artifacts.get("execution_summary_path")))
    predictions_rows = _read_csv_rows(_artifact_path(manifest_path, archive_artifacts.get("predictions_path")))
    order_rows = _read_csv_rows(summary_path.parent / "05_shadow" / "order_intents.csv")
    audit_rows = _read_csv_rows(summary_path.parent / "05_shadow" / "rebalance_audit.csv")

    requested = summary.get("requested_date") or _safe_get(summary.get("date_resolution"), "requested_date")
    resolved = summary.get("trade_date") or _safe_get(summary.get("date_resolution"), "resolved_trade_date")
    model_payload = selected_model or summary.get("model_used") or {}
    model_name = _safe_get(model_payload, "model_name")
    lines = [
        f"Qsys Daily Shadow {_status_icon(str(summary.get('overall_status') or ''))}",
        "",
        f"trade_date: {resolved or 'N/A'}",
        f"date: requested {requested or 'N/A'} -> resolved {resolved or 'N/A'}",
        f"mainline: {_safe_get(model_payload, 'mainline_object_name', summary.get('mainline_object_name') or 'N/A')}",
        f"bundle: {_safe_get(model_payload, 'bundle_id', summary.get('bundle_id') or 'N/A')}",
        f"model: {model_name}",
        f"decision: {summary.get('decision_status') or 'N/A'}",
        "",
        "Data",
        f"- qlib_last_date: {_safe_get(data_status, 'last_qlib_date')}",
        f"- data_status: {summary.get('data_status') or 'N/A'}",
        f"- feature_status: {summary.get('feature_status') or 'N/A'}",
        f"- fields: {_safe_get(feature_status, 'field_count')} total / {_safe_get(feature_status, 'usable_field_count')} usable",
        f"- degradation: {_safe_get(feature_status, 'degradation_level', summary.get('degradation_level') or 'N/A')}",
        f"- readiness_status: {summary.get('readiness_status') or ('blocked' if _safe_get(feature_status, 'degradation_level') in {'blocked', 'extended_blocked'} else 'ok')}",
        "",
        "Inference",
        f"- predictions: {_safe_get(inference_summary, 'prediction_count', summary.get('prediction_count') or 'N/A')} / min_required: {summary.get('min_prediction_count', 'N/A')}",
        f"- coverage_status: {summary.get('prediction_coverage_status') or _safe_get(execution_summary, 'coverage_status')}",
        "- top picks:",
    ]
    top_picks = _top_predictions(predictions_rows)
    lines.extend(top_picks or [" none"])
    lines.extend(
        [
            "",
            "Rebalance",
            f"- orders: {_safe_get(execution_summary, 'order_count')}, filled: {_safe_get(execution_summary, 'filled_count')}, rejected: {_safe_get(execution_summary, 'rejected_count')}",
            f"- turnover: {_fmt_number(_safe_get(execution_summary, 'turnover'))}",
            f"- total_value_after: {_fmt_number(_safe_get(execution_summary, 'total_value_after'))}",
            "- no-trade reasons:",
        ]
    )
    lines.extend(_top_reasons(order_rows, execution_summary, audit_rows) or [" none"])
    lines.extend(["", "Artifacts", _relative_path_text(_artifact_path(manifest_path, archive_artifacts.get("daily_summary_path")) or summary_path)])
    return "\n".join(lines)


def _relative_path_text(path: Path) -> str:
    parts = path.parts
    if "runs" in parts:
        return "/".join(parts[parts.index("runs"):])
    return path.as_posix()


def build_shadow_retrain_digest(summary_path: str | Path, manifest_path: str | Path) -> str:
    summary_path = Path(summary_path)
    manifest_path = Path(manifest_path)
    summary = load_json(summary_path)
    manifest = load_json(manifest_path)
    stage_status = manifest.get("stage_status") or {}
    archive_artifacts = ((stage_status.get("archive_report") or {}).get("artifact_pointers") or {})
    run_training = _load_json_if_exists(_artifact_path(manifest_path, (stage_status.get("run_training") or {}).get("artifact_pointers", {}).get("stage_output")))
    update_pointer = _load_json_if_exists(_artifact_path(manifest_path, (stage_status.get("update_model_pointer") or {}).get("artifact_pointers", {}).get("stage_output")))
    latest_model = _load_json_if_exists(_artifact_path(manifest_path, "models/latest_shadow_model.json"))

    training_summary_path = None
    if run_training:
        training_summary_path = _artifact_path(manifest_path, ((run_training.get("artifact_pointers") or {}).get("training_summary_path")))
    training_summary = _load_json_if_exists(training_summary_path)

    date_resolution = summary.get("date_resolution") or {}
    model_used = summary.get("model_used") or {}
    lines = [
        f"Qsys Weekly Retrain {_status_icon(str(manifest.get('overall_status') or ''))}",
        "",
        f"train_end: requested {date_resolution.get('requested_date', 'N/A')} -> resolved {summary.get('trade_date') or date_resolution.get('resolved_trade_date', 'N/A')}",
        f"mainline: {manifest.get('mainline_object_name') or 'N/A'}",
        f"bundle: {manifest.get('bundle_id') or 'N/A'}",
        f"model: {model_used.get('model_name') or manifest.get('model_name') or 'N/A'}",
        f"pointer: {'updated' if _safe_get(update_pointer, 'status') == 'success' else _safe_get(update_pointer, 'status')}",
        "",
        "Training",
        f"- train_status: {summary.get('train_status') or 'N/A'}",
        f"- latest_model_usable: {_fmt_bool(_safe_get(latest_model, 'status') == 'success' and bool(_safe_get(latest_model, 'model_path', None) not in ('N/A', None, '')))}",
        f"- fallback: {_fmt_bool((summary.get('model_used') or {}).get('fallback', False))}",
        f"- metrics: IC={_fmt_number(_safe_get(training_summary, 'ic'))}, RankIC={_fmt_number(_safe_get(training_summary, 'rank_ic'))}, loss={_fmt_number(_safe_get(training_summary, 'loss', _safe_get(training_summary, 'mse')))}",
        "",
        "Artifacts",
        _relative_path_text(_artifact_path(manifest_path, archive_artifacts.get('daily_summary_path')) or summary_path),
    ]
    return "\n".join(lines)


def build_shadow_run_digest(summary_path: str | Path, manifest_path: str | Path) -> str:
    summary = load_json(summary_path)
    run_type = str(summary.get("run_type") or "")
    if run_type == "shadow_retrain_weekly":
        return build_shadow_retrain_digest(summary_path, manifest_path)
    return build_shadow_daily_digest(summary_path, manifest_path)
