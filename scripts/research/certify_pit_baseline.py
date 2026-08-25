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
from qsys.pit_datapack import export_certified_datapack, verify_datapack


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Certify an explicit PIT data baseline")
    parser.add_argument("--request")
    parser.add_argument("--audit-db")
    parser.add_argument("--evidence-run-id", action="append", default=[])
    parser.add_argument("--mutation-run-id", action="append", default=[])
    parser.add_argument("--output-root")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--export-datapack-from", metavar="CERTIFICATION_DIR")
    modes.add_argument("--verify-datapack", metavar="DATAPACK_DIR")
    parser.add_argument("--datapack-output", metavar="DATAPACK_DIR")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.verify_datapack:
            if args.datapack_output:
                raise CertificationError("--datapack-output is invalid with --verify-datapack")
            result = verify_datapack(args.verify_datapack)
            print(json.dumps(result, sort_keys=True, ensure_ascii=False))
            return 0
        if args.export_datapack_from:
            if not args.datapack_output:
                raise CertificationError("--datapack-output is required for DataPack export")
            result = export_certified_datapack(
                certification_dir=args.export_datapack_from,
                output_dir=args.datapack_output,
                project_root=PROJECT_ROOT,
            )
            print(json.dumps(result, sort_keys=True, ensure_ascii=False))
            return 0
        missing = [
            name for name, value in (
                ("--request", args.request), ("--audit-db", args.audit_db),
                ("--output-root", args.output_root),
            ) if not value
        ]
        if missing:
            raise CertificationError(f"certification mode requires {', '.join(missing)}")
        if args.datapack_output:
            raise CertificationError("--datapack-output requires --export-datapack-from")
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
