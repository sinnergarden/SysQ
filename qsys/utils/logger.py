import json
import sys
from typing import Any

from loguru import logger

from qsys.config.manager import cfg


def _normalize_log_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    if isinstance(value, (list, tuple, set)):
        return json.dumps(list(value), ensure_ascii=False)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = str(value)
    return text.replace("\n", " ").strip()


def format_kv(**fields: Any) -> str:
    items = []
    for key, value in fields.items():
        if value is None:
            continue
        items.append(f"{key}={_normalize_log_value(value)}")
    return " ".join(items)


def log_event(level: str, event: str, **fields: Any):
    message = event if not fields else f"{event} | {format_kv(**fields)}"
    getattr(log, level.lower())(message)


def log_stage(stage: str, status: str, **fields: Any):
    log_event("info", f"[{stage}] {status}", **fields)


def setup_logger():
    level = cfg.get("log_level", "INFO")
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    )
    return logger


log = setup_logger()
