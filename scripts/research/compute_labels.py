#!/usr/bin/env python3
"""Thin forwarder — delegates to scripts/compute_labels.py (canonical UC-3 entrypoint).

This file exists for backward compatibility.  New invocations should use::

    python scripts/compute_labels.py ...
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Forward to canonical entrypoint
from scripts.compute_labels import main  # noqa: E402

if __name__ == "__main__":
    main()
