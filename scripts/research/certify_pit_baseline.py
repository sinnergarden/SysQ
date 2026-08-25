#!/usr/bin/env python3
"""Thin CLI for the read-only UC_PIT_DATA_CERTIFICATION entrypoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qsys.pit_certification import CertificationError, certify_pit_baseline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Certify an explicit PIT data baseline")
    parser.add_argument("--request", required=True)
    parser.add_argument("--audit-db", required=True)
    parser.add_argument("--evidence-run-id", action="append", default=[])
    parser.add_argument("--mutation-run-id", action="append", default=[])
    parser.add_argument("--output-root", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = certify_pit_baseline(
            request_path=args.request,
            audit_db=args.audit_db,
            evidence_run_ids=args.evidence_run_id,
            mutation_run_ids=args.mutation_run_id,
            output_root=args.output_root,
            project_root=PROJECT_ROOT,
        )
    except (CertificationError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"PIT baseline certification input/runtime error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0 if result["status"] == "CERTIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
