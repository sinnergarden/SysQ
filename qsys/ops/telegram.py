from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from qsys.config import cfg
from qsys.ops.digest import build_shadow_run_digest
from qsys.ops.state import atomic_write_json, ensure_directory, load_json

CHANNEL_NAME = "telegram"
DEFAULT_TIMEOUT_SECONDS = 10
MAX_RESPONSE_TEXT_LENGTH = 200
TOKEN_RE = re.compile(r"\b\d{3,}:[A-Za-z0-9_-]{3,}\b")
API_URL_RE = re.compile(r"https://api\.telegram\.org/bot[^\s/]+")


def _sanitize_telegram_text(text: str | None) -> str:
    sanitized = str(text or "")
    sanitized = TOKEN_RE.sub("***", sanitized)
    sanitized = API_URL_RE.sub("https://api.telegram.org/bot***", sanitized)
    return sanitized


def _safe_response_text(response: requests.Response) -> str:
    text = _sanitize_telegram_text((response.text or "").strip())
    if len(text) > MAX_RESPONSE_TEXT_LENGTH:
        return text[:MAX_RESPONSE_TEXT_LENGTH] + "..."
    return text


def _resolve_telegram_config() -> dict[str, Any]:
    ops_cfg = cfg.get("ops", {}) or {}
    notification_cfg = ops_cfg.get("notification", {}) or {}
    telegram_cfg = notification_cfg.get("telegram", {}) or {}
    command_gateway_cfg = ops_cfg.get("command_gateway", {}) or {}
    gateway_telegram_cfg = command_gateway_cfg.get("telegram", {}) or {}
    return {
        "notification": telegram_cfg if isinstance(telegram_cfg, dict) else {},
        "gateway": gateway_telegram_cfg if isinstance(gateway_telegram_cfg, dict) else {},
    }


def _resolve_bot_token(explicit_bot_token: str | None = None) -> str | None:
    if explicit_bot_token:
        return explicit_bot_token
    env_token = os.environ.get("QSYS_TELEGRAM_BOT_TOKEN")
    if env_token:
        return env_token
    config = _resolve_telegram_config()
    for source in (config["notification"], config["gateway"]):
        token = source.get("bot_token")
        if token:
            return str(token)
    token = cfg.get("telegram_bot_token")
    return str(token) if token else None


def _resolve_allowed_chat_ids() -> list[str]:
    config = _resolve_telegram_config()
    chat_ids: list[str] = []
    env_chat_id = os.environ.get("QSYS_TELEGRAM_ALLOWED_CHAT_ID")
    if env_chat_id:
        chat_ids.append(str(env_chat_id))
    for source in (config["notification"], config["gateway"]):
        values = source.get("allowed_chat_ids") or []
        if isinstance(values, list):
            chat_ids.extend(str(item) for item in values if str(item).strip())
    return list(dict.fromkeys(chat_ids))


def _resolve_chat_id(explicit_chat_id: str | None = None) -> str | None:
    if explicit_chat_id:
        return str(explicit_chat_id)
    chat_ids = _resolve_allowed_chat_ids()
    return chat_ids[0] if chat_ids else None


def _resolve_parse_mode() -> str | None:
    config = _resolve_telegram_config()["notification"]
    parse_mode = config.get("parse_mode")
    if parse_mode in (None, ""):
        return None
    return str(parse_mode)


def _resolve_timeout_seconds() -> int:
    config = _resolve_telegram_config()["notification"]
    timeout = config.get("timeout_seconds")
    try:
        return int(timeout or DEFAULT_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS


def send_telegram_message(text: str, *, bot_token: str | None = None, chat_id: str | None = None) -> dict[str, Any]:
    resolved_bot_token = _resolve_bot_token(bot_token)
    resolved_chat_id = _resolve_chat_id(chat_id)
    if not resolved_bot_token:
        return {
            "status": "skipped",
            "channel": CHANNEL_NAME,
            "configured": False,
            "chat_id_configured": bool(resolved_chat_id),
            "message": "telegram bot token not configured",
            "error": "telegram bot token not configured",
        }
    if not resolved_chat_id:
        return {
            "status": "skipped",
            "channel": CHANNEL_NAME,
            "configured": True,
            "chat_id_configured": False,
            "message": "telegram chat_id not configured",
            "error": "telegram chat_id not configured",
        }

    url = f"https://api.telegram.org/bot{resolved_bot_token}/sendMessage"
    payload = {
        "chat_id": resolved_chat_id,
        "text": text,
    }
    parse_mode = _resolve_parse_mode()
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        response = requests.post(url, json=payload, timeout=_resolve_timeout_seconds())
        http_status = int(response.status_code)
        response_text = _safe_response_text(response)
        response.raise_for_status()
        body = response.json() if response.text else {}
        if not bool(body.get("ok", False)):
            return {
                "status": "failed",
                "channel": CHANNEL_NAME,
                "configured": True,
                "chat_id_configured": True,
                "message": "telegram api returned ok=false",
                "error": _sanitize_telegram_text(str(body.get("description") or "telegram api returned ok=false")),
                "http_status": http_status,
                "response_text": response_text,
            }
        return {
            "status": "success",
            "channel": CHANNEL_NAME,
            "configured": True,
            "chat_id_configured": True,
            "message": f"notification sent ({http_status})",
            "error": None,
            "http_status": http_status,
            "response_text": response_text,
        }
    except Exception as exc:
        return {
            "status": "failed",
            "channel": CHANNEL_NAME,
            "configured": True,
            "chat_id_configured": True,
            "message": "telegram request failed",
            "error": _sanitize_telegram_text(str(exc)),
        }


def _relative_to_runs(path: Path) -> str:
    parts = path.parts
    if "runs" in parts:
        index = parts.index("runs")
        return "/".join(parts[index:])
    return str(path)


def send_shadow_run_telegram_notification(summary_path: str | Path, manifest_path: str | Path) -> dict[str, Any]:
    summary_path = Path(summary_path)
    manifest_path = Path(manifest_path)
    summary = load_json(summary_path)
    manifest = load_json(manifest_path)
    digest_status = "success"
    digest_error = None
    try:
        message_text = build_shadow_run_digest(summary_path, manifest_path)
    except Exception as exc:
        digest_status = "failed"
        digest_error = _sanitize_telegram_text(str(exc))
        run_type = str(summary.get("run_type") or manifest.get("run_type") or "")
        lines = [
            f"Qsys {run_type or 'shadow_run'}",
            f"trade_date: {summary.get('trade_date', '')}",
            f"run_id: {summary.get('run_id', '')}",
            f"status: {manifest.get('overall_status', summary.get('overall_status', 'unknown'))}",
            f"summary: {_relative_to_runs(summary_path)}",
        ]
        if summary.get("decision_status"):
            lines.append(f"decision_status: {summary['decision_status']}")
        message_text = "\n".join(lines)
    result = send_telegram_message(message_text)
    result["summary_path"] = _relative_to_runs(summary_path)
    result["manifest_path"] = _relative_to_runs(manifest_path)
    result["digest_status"] = digest_status
    if digest_error:
        result["digest_error"] = digest_error
    return result


def get_telegram_updates(*, bot_token: str | None = None, timeout_seconds: int = 10) -> dict[str, Any]:
    resolved_bot_token = _resolve_bot_token(bot_token)
    if not resolved_bot_token:
        return {"ok": False, "result": [], "error": "telegram bot token not configured"}
    url = f"https://api.telegram.org/bot{resolved_bot_token}/getUpdates"
    response = requests.get(url, timeout=timeout_seconds)
    response.raise_for_status()
    return response.json()


def hash_chat_id(chat_id: str) -> str:
    return hashlib.sha256(str(chat_id).encode("utf-8")).hexdigest()[:16]


def append_gateway_command_log(base_dir: str | Path, payload: dict[str, Any]) -> Path:
    base_dir = Path(base_dir)
    log_path = base_dir / "runs" / "telegram_gateway" / "command_log.jsonl"
    ensure_directory(log_path.parent)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
    return log_path


def build_command_log_entry(*, chat_id: str, command: str, status: str, run_id: str | None = None, error: str | None = None) -> dict[str, Any]:
    return {
        "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "chat_id_hash": hash_chat_id(chat_id),
        "command": command,
        "status": status,
        "run_id": run_id,
        "error": _sanitize_telegram_text(error),
    }


def write_telegram_notification_result(path: str | Path, payload: dict[str, Any]) -> Path:
    return atomic_write_json(path, payload)
