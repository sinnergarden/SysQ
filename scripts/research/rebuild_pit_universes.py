#!/usr/bin/env python3
"""Rebuild corrected, hash-bound PIT universe artifacts for research."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from qsys.research.pit_universe import rebuild_pit_universes_v2


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild csi800/csi1800 PIT v2 artifacts from immutable v1 raw "
            "snapshots"
        )
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing PIT v2 targets transactionally",
    )
    args = parser.parse_args()
    result = rebuild_pit_universes_v2(PROJECT_ROOT, overwrite=args.overwrite)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
