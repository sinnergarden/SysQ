#!/usr/bin/env python3
"""Independent terminal-artifact check for UC_TOP10_SIGNAL_RUN."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from qsys.signal.top10_run import Top10RunError, validate_top10_run_artifact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", required=True, help="top10_run.json path")
    args = parser.parse_args(argv)
    path = Path(args.artifact)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        payload = validate_top10_run_artifact(path)
    except (Top10RunError, OSError, ValueError, KeyError) as exc:
        print(
            json.dumps(
                {"status": "blocked", "artifact": str(path), "error": str(exc)},
                ensure_ascii=False,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "status": "pass",
                "artifact": str(path),
                "run_identity": payload["run_identity"],
                "signal_date": payload["signal_date"],
                "decision_date": payload["decision_date"],
                "model_bundle_hash": payload["model"]["bundle_hash"],
                "candidate_hash": payload["quality_gate"]["candidate_hash"],
                "top10_count": len(payload["top10"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
