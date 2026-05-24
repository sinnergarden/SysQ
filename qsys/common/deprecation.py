"""Deprecation warnings for legacy entrypoints."""

from __future__ import annotations

import sys


def print_legacy_entrypoint_warning(
    old: str,
    replacement: str,
    *,
    file=None,
) -> None:
    """Print a visible deprecation warning for a legacy entrypoint.

    Parameters
    ----------
    old
        Name or path of the legacy entrypoint (e.g. ``run_alpha_v1_daily.py``).
    replacement
        Recommended replacement command or entrypoint.
    file
        Output stream (defaults to ``sys.stderr``).
    """
    if file is None:
        file = sys.stderr

    sep = "=" * 60
    print(sep, file=file)
    print(f"  ⚠ DEPRECATED: {old}", file=file)
    print(f"  This is a legacy compatibility entrypoint.", file=file)
    print(f"  Prefer:", file=file)
    print(f"    {replacement}", file=file)
    print(sep, file=file)
