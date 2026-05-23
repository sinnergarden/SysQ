"""Git helpers — read-only operations for commit-identification purposes.

Business-neutral utilities only.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def git_commit(short: bool = True) -> str:
    """Return the current git commit hash (short form by default).

    Returns ``"unknown"`` if the commit cannot be determined (e.g. not a
    git repository or git is not installed).
    """
    try:
        args = ["git", "rev-parse", "--short", "HEAD"] if short else ["git", "rev-parse", "HEAD"]
        result = subprocess.run(args, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return result.stdout.strip()
        return "unknown"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return "unknown"


def git_commit_full() -> str:
    """Return the full 40-character git commit hash."""
    return git_commit(short=False)
