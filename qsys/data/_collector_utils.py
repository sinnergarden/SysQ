"""Pure utility functions extracted from TushareCollector.

These functions have no dependency on ``self`` — they operate only on
their arguments and are safe to extract without semantic change.
"""

from __future__ import annotations


def _normalize_date(date_str: str | None) -> str | None:
    """Convert ``YYYY-MM-DD`` → ``YYYYMMDD`` (pass-through if already clean)."""
    if date_str is None:
        return None
    date_str = str(date_str)
    if "-" in date_str:
        return date_str.replace("-", "")
    return date_str


def _dedupe_list(items: list) -> list:
    """Return *items* with duplicates removed, preserving first-occurrence order."""
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
