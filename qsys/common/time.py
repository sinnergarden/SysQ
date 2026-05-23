"""Generic time / datetime helpers.

Business-neutral utilities only.
"""

from __future__ import annotations

from datetime import datetime, timezone


def now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def timestamp_for_filename() -> str:
    """Return a timestamp string safe for use in filenames."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")
