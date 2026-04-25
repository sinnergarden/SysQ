from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import requests

from qsys.config import cfg
from qsys.ops.state import atomic_write_json, load_json

CHANNEL_NAME = "wecom_webhook"
DEFAULT_TIMEOUT_SECONDS = 5
MAX_RESPONSE_TEXT_LENGTH = 200
WEBHOOK_URL_RE = re.compile(r"https://qyapi\.weixin\.qq\.com/cgi-bin/webhook/send\?key=[^\s\"'&]+")
WEBHOOK_KEY_RE = re.compile(r"key=[^\s\"'&]+")


def _resolve_wecom_webhook_url(explicit_webhook_url: str | None = None) -> str | None:
    if explicit_webhook_url:
        return explicit_webhook_url

    ops_cfg = cfg.get("ops", {}) or {}
    notification_cfg = ops_cfg.get("notification", {}) or {}
    webhook_url = notification_cfg.get("wecom_webhook_url")
    if webhook_url:
        return str(webhook_url)

    notification_root_cfg = cfg.get("notification", {}) or {}
    webhook_url = notification_root_cfg.get("wecom_webhook_url")
    if webhook_url:
        return str(webhook_url)

    legacy_webhook_url = cfg.get("webhook_url")
    if legacy_webhook_url:
        return str(legacy_webhook_url)
    return None


def _sanitize_notification_text(text: str | None) -> str:
    sanitized = str(text or "")
    sanitized = WEBHOOK_URL_RE.sub("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=***", sanitized)
    sanitized = WEBHOOK_KEY_RE.sub("key=***", sanitized)
    return sanitized


def _safe_response_text(response: requests.Response) -> str:
    text = _sanitize_notification_text((response.text or "").strip())
    if len(text) > MAX_RESPONSE_TEXT_LENGTH:
        return text[:MAX_RESPONSE_TEXT_LENGTH] + "..."
    return text


def send_wecom_webhook_message(title: str, content: str, *, webhook_url: str | None = None) -> dict[str, Any]:
    resolved_webhook_url = _resolve_wecom_webhook_url(webhook_url)
    if not resolved_webhook_url:
        return {
            "status": "skipped",
            "channel": CHANNEL_NAME,
            "webhook_configured": False,
            "message": "webhook not configured",
            "error": "webhook not configured",
        }

    payload = {
        "msgtype": "markdown",
        "markdown": {
            "content": f"{title}\n\n{content}".strip(),
        },
    }
    try:
        response = requests.post(resolved_webhook_url, json=payload, timeout=DEFAULT_TIMEOUT_SECONDS)
        http_status = int(response.status_code)
        response_text = _safe_response_text(response)
        response.raise_for_status()

        response_payload = {}
        if response.text:
            try:
                response_payload = json.loads(response.text)
            except json.JSONDecodeError:
                response_payload = {}

        errcode = int(response_payload.get("errcode", 0)) if response_payload else 0
        errmsg = _sanitize_notification_text(str(response_payload.get("errmsg", ""))) if response_payload else ""
        if errcode != 0:
            return {
                "status": "failed",
                "channel": CHANNEL_NAME,
                "webhook_configured": True,
                "message": "wecom webhook returned non-zero errcode",
                "error": f"wecom errcode={errcode}, errmsg={errmsg or 'unknown'}",
                "http_status": http_status,
                "response_text": response_text,
            }

        return {
            "status": "success",
            "channel": CHANNEL_NAME,
            "webhook_configured": True,
            "message": f"notification sent ({http_status})",
            "error": None,
            "http_status": http_status,
            "response_text": response_text,
        }
    except Exception as exc:
        return {
            "status": "failed",
            "channel": CHANNEL_NAME,
            "webhook_configured": True,
            "message": "notification request failed",
            "error": _sanitize_notification_text(str(exc)),
        }


def _relative_to_runs(path: Path) -> str:
    parts = path.parts
    if "runs" in parts:
        index = parts.index("runs")
        return "/".join(parts[index:])
    return str(path)


def _first_failed_stage(stage_status: dict[str, Any]) -> str | None:
    for stage_name, payload in stage_status.items():
        if payload.get("status") == "failed":
            return stage_name
    return None


def _build_daily_message(summary: dict[str, Any], manifest: dict[str, Any], summary_path: Path) -> tuple[str, str]:
    overall_status = str(manifest.get("overall_status", summary.get("overall_status", "unknown"))).upper()
    title = f"Qsys Shadow Daily: {overall_status}"
    model_used = summary.get("model_used", {}) or {}
    stage_status = manifest.get("stage_status", {}) or {}
    failed_stage = _first_failed_stage(stage_status)
    lines = [
        f"trade_date: {summary.get('trade_date', '')}",
        f"run_id: {summary.get('run_id', '')}",
    ]
    if failed_stage:
        lines.append(f"failed_stage: {failed_stage}")
    lines.extend(
        [
            f"decision_status: {summary.get('decision_status', '')}",
            f"model: {model_used.get('model_name', '')}",
            f"orders: {summary.get('shadow_order_count', 0)}",
            f"filled: {summary.get('filled_count', 0)}",
            f"rejected: {summary.get('rejected_count', 0)}",
            f"turnover: {float(summary.get('turnover', 0.0) or 0.0):.2f}",
            f"total_value_after: {summary.get('total_value_after')}",
        ]
    )
    if summary.get("error"):
        lines.append(f"error: {summary['error']}")
    lines.append(f"summary: {_relative_to_runs(summary_path)}")
    return title, "\n".join(lines)


def _build_weekly_message(summary: dict[str, Any], manifest: dict[str, Any], summary_path: Path) -> tuple[str, str]:
    overall_status = str(manifest.get("overall_status", summary.get("decision_status", "unknown"))).upper()
    title = f"Qsys Weekly Retrain: {overall_status}"
    model_used = summary.get("model_used", {}) or {}
    pointer_status = ((manifest.get("stage_status", {}) or {}).get("update_model_pointer", {}) or {}).get("status", "")
    pointer_updated = pointer_status == "success"
    lines = [
        f"trade_date: {summary.get('trade_date', '')}",
        f"run_id: {summary.get('run_id', '')}",
        f"model: {model_used.get('model_name', '')}",
        f"mainline: {manifest.get('mainline_object_name', '')}",
        f"pointer_updated: {str(pointer_updated).lower()}",
    ]
    if summary.get("decision_status"):
        lines.append(f"decision_status: {summary['decision_status']}")
    if summary.get("notes"):
        lines.append(f"note: {summary['notes'][-1]}")
    lines.append(f"summary: {_relative_to_runs(summary_path)}")
    return title, "\n".join(lines)


def send_shadow_run_notification(summary_path: str | Path, manifest_path: str | Path) -> dict[str, Any]:
    summary_path = Path(summary_path)
    manifest_path = Path(manifest_path)
    summary = load_json(summary_path)
    manifest = load_json(manifest_path)
    run_type = str(summary.get("run_type") or manifest.get("run_type") or "")

    if run_type == "shadow_daily":
        title, content = _build_daily_message(summary, manifest, summary_path)
    elif run_type == "shadow_retrain_weekly":
        title, content = _build_weekly_message(summary, manifest, summary_path)
    else:
        title = "Qsys Shadow Run"
        content = "\n".join(
            [
                f"trade_date: {summary.get('trade_date', '')}",
                f"run_id: {summary.get('run_id', '')}",
                f"summary: {_relative_to_runs(summary_path)}",
            ]
        )

    result = send_wecom_webhook_message(title, content)
    result["title"] = title
    result["summary_path"] = _relative_to_runs(summary_path)
    result["manifest_path"] = _relative_to_runs(manifest_path)
    return result


def write_notification_result(path: str | Path, payload: dict[str, Any]) -> Path:
    return atomic_write_json(path, payload)
