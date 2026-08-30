#!/usr/bin/env python3
"""Validate a lineage-bound executable label suite."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qsys.label.validation import validate_executable_label_suite


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite-manifest", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = validate_executable_label_suite(
        suite_manifest_path=args.suite_manifest,
        config_path=args.config,
        data_root=args.data_root,
        output_path=args.output,
    )
    print(
        f"Validated {len(report['outputs'])} labels: "
        f"{report['status']} ({args.output})"
    )


if __name__ == "__main__":
    main()
