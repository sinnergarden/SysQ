#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qsys.config import cfg
from qsys.ops import (
    append_gateway_command_log,
    build_command_log_entry,
    get_telegram_updates,
    send_telegram_message,
)

DEFAULT_COMMAND_TIMEOUT = 600


def _telegram_gateway_config() -> dict[str, Any]:
    ops_cfg = cfg.get("ops", {}) or {}
    gateway_cfg = (ops_cfg.get("command_gateway", {}) or {}).get("telegram", {}) or {}
    return gateway_cfg if isinstance(gateway_cfg, dict) else {}


def _allowed_chat_ids() -> set[str]:
    values = _telegram_gateway_config().get("allowed_chat_ids") or []
    allowed = {str(item) for item in values if str(item).strip()}
    env_chat_id = os.environ.get("QSYS_TELEGRAM_ALLOWED_CHAT_ID")
    if env_chat_id:
        allowed.add(str(env_chat_id))
    return allowed


def _allowed_commands() -> set[str]:
    values = _telegram_gateway_config().get("allowed_commands") or ["status", "daily", "retrain", "help"]
    return {str(item) for item in values}


def _confirm_required() -> set[str]:
    values = _telegram_gateway_config().get("require_confirm_for") or ["daily", "retrain"]
    return {str(item) for item in values}


def _state_dir(base_dir: Path) -> Path:
    path = base_dir / "runs" / "telegram_gateway"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pending_file(base_dir: Path, chat_id: str) -> Path:
    key = hashlib.sha256(str(chat_id).encode("utf-8")).hexdigest()[:16]
    return _state_dir(base_dir) / f"pending_{key}.json"


def _load_pending(base_dir: Path, chat_id: str) -> dict[str, Any]:
    path = _pending_file(base_dir, chat_id)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_pending(base_dir: Path, chat_id: str, payload: dict[str, Any]) -> None:
    _pending_file(base_dir, chat_id).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _clear_pending(base_dir: Path, chat_id: str) -> None:
    path = _pending_file(base_dir, chat_id)
    if path.exists():
        path.unlink()


def _reply(chat_id: str, text: str) -> dict[str, Any]:
    return send_telegram_message(text, chat_id=chat_id)


def _run_command(base_dir: Path, command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(base_dir)
    return subprocess.run(
        command,
        cwd=base_dir,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _handle_status(base_dir: Path, chat_id: str, timeout: int) -> tuple[str, str | None]:
    proc = _run_command(
        base_dir,
        [
            str(base_dir / ".envs" / "test" / "bin" / "python"),
            "scripts/ops/check_shadow_status.py",
            "--base-dir",
            ".",
            "--format",
            "text",
            "--write-latest",
        ],
        timeout,
    )
    output = (proc.stdout or proc.stderr or "").strip()
    _reply(chat_id, output[:3500] or "status returned no output")
    return ("success" if proc.returncode == 0 else "failed", None if proc.returncode == 0 else output[:200])


def _handle_confirm_prompt(base_dir: Path, chat_id: str, command_name: str) -> tuple[str, None]:
    _save_pending(base_dir, chat_id, {"command": command_name})
    _reply(chat_id, f"Confirm {command_name} shadow run with: /confirm {command_name}")
    return ("success", None)


def _handle_confirm(base_dir: Path, chat_id: str, command_name: str, timeout: int) -> tuple[str, str | None, str | None]:
    pending = _load_pending(base_dir, chat_id)
    if pending.get("command") != command_name:
        _reply(chat_id, f"No pending {command_name} confirmation.")
        return ("rejected", None, "no pending confirmation")
    _clear_pending(base_dir, chat_id)
    script = "scripts/ops/run_shadow_daily.py" if command_name == "daily" else "scripts/ops/run_shadow_retrain_weekly.py"
    proc = _run_command(
        base_dir,
        [str(base_dir / ".envs" / "test" / "bin" / "python"), script, "--base-dir", ".", "--triggered-by", "telegram"],
        timeout,
    )
    output = (proc.stdout or proc.stderr or "").strip()
    run_id = None
    try:
        payload = json.loads(output)
        run_id = payload.get("run_id")
    except Exception:
        pass
    _reply(chat_id, (output[:3500] or f"{command_name} finished with no output"))
    return (("success" if proc.returncode == 0 else "failed"), run_id, None if proc.returncode == 0 else output[:200])


def handle_command(base_dir: Path, chat_id: str, text: str, *, timeout: int = DEFAULT_COMMAND_TIMEOUT) -> dict[str, Any]:
    allowed_chat_ids = _allowed_chat_ids()
    if chat_id not in allowed_chat_ids:
        entry = build_command_log_entry(chat_id=chat_id, command="unknown", status="rejected", error="chat_id not allowed")
        append_gateway_command_log(base_dir, entry)
        return entry

    command_text = (text or "").strip()
    if not command_text.startswith("/"):
        entry = build_command_log_entry(chat_id=chat_id, command="unknown", status="rejected", error="unsupported message")
        append_gateway_command_log(base_dir, entry)
        return entry

    parts = command_text.split()
    root = parts[0].lstrip("/")
    args = parts[1:]
    if root == "shell" or root == "ask":
        _reply(chat_id, "Rejected: unsupported command.")
        entry = build_command_log_entry(chat_id=chat_id, command=root, status="rejected", error="unsupported command")
        append_gateway_command_log(base_dir, entry)
        return entry

    allowed_commands = _allowed_commands()
    if root not in {"help", "status", "daily", "retrain", "confirm"} or (root != "confirm" and root not in allowed_commands):
        _reply(chat_id, "Rejected: unknown command. Use /help.")
        entry = build_command_log_entry(chat_id=chat_id, command=root, status="rejected", error="unknown command")
        append_gateway_command_log(base_dir, entry)
        return entry

    if root == "help":
        _reply(chat_id, "Supported commands: /help /status /daily /retrain /confirm daily /confirm retrain")
        entry = build_command_log_entry(chat_id=chat_id, command="help", status="success")
    elif root == "status":
        status, error = _handle_status(base_dir, chat_id, timeout)
        entry = build_command_log_entry(chat_id=chat_id, command="status", status=status, error=error)
    elif root in {"daily", "retrain"}:
        if root in _confirm_required():
            status, _ = _handle_confirm_prompt(base_dir, chat_id, root)
            entry = build_command_log_entry(chat_id=chat_id, command=root, status=status)
        else:
            status, run_id, error = _handle_confirm(base_dir, chat_id, root, timeout)
            entry = build_command_log_entry(chat_id=chat_id, command=root, status=status, run_id=run_id, error=error)
    else:
        if len(args) != 1 or args[0] not in {"daily", "retrain"}:
            _reply(chat_id, "Rejected: usage is /confirm daily or /confirm retrain")
            entry = build_command_log_entry(chat_id=chat_id, command="confirm", status="rejected", error="invalid confirm usage")
        else:
            status, run_id, error = _handle_confirm(base_dir, chat_id, args[0], timeout)
            entry = build_command_log_entry(chat_id=chat_id, command=args[0], status=status, run_id=run_id, error=error)
    append_gateway_command_log(base_dir, entry)
    return entry


def poll_once(base_dir: Path, *, timeout: int = DEFAULT_COMMAND_TIMEOUT) -> list[dict[str, Any]]:
    updates = get_telegram_updates()
    entries: list[dict[str, Any]] = []
    state_path = _state_dir(base_dir) / "offset.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"last_update_id": 0}
    last_update_id = int(state.get("last_update_id", 0))
    max_seen = last_update_id
    for item in updates.get("result", []):
        update_id = int(item.get("update_id", 0))
        if update_id <= last_update_id:
            continue
        message = item.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        text = str(message.get("text", ""))
        entries.append(handle_command(base_dir, chat_id, text, timeout=timeout))
        max_seen = max(max_seen, update_id)
    state_path.write_text(json.dumps({"last_update_id": max_seen}, indent=2) + "\n", encoding="utf-8")
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Telegram polling gateway for Qsys shadow ops")
    parser.add_argument("--base-dir", default=str(PROJECT_ROOT), help="Project root")
    parser.add_argument("--once", action="store_true", help="Poll once and exit")
    parser.add_argument("--poll-interval", type=int, default=5, help="Polling interval seconds")
    parser.add_argument("--command-timeout", type=int, default=DEFAULT_COMMAND_TIMEOUT, help="Subprocess timeout seconds")
    args = parser.parse_args()
    base_dir = Path(args.base_dir)
    if args.once:
        print(json.dumps(poll_once(base_dir, timeout=args.command_timeout), indent=2, ensure_ascii=False))
        return
    while True:
        poll_once(base_dir, timeout=args.command_timeout)
        time.sleep(max(args.poll_interval, 1))


if __name__ == "__main__":
    main()
