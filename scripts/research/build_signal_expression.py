#!/usr/bin/env python3
"""Build a derived signal from a signal expression config file.

Usage::

    python scripts/research/build_signal_expression.py \\
        --config configs/signal_expressions/example_alpha_v1_identity.yaml \\
        --overwrite
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from qsys.signal.expression import SignalExpressionRunner, SignalExpressionSpec  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a derived signal from a signal expression config"
    )
    parser.add_argument("--config", required=True, help="Path to YAML/JSON config")
    parser.add_argument("--root", default="data/research", help="Research root path")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing output")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(json.dumps({"status": "failed", "error": f"Config not found: {config_path}"}))
        sys.exit(1)

    spec = SignalExpressionSpec.from_file(config_path)
    runner = SignalExpressionRunner(root=args.root)
    output_path = runner.run(spec, overwrite=args.overwrite)

    result = {
        "status": "passed",
        "output_signal_id": spec.output_signal_id,
        "output_signal_run_id": spec.output_signal_run_id,
        "output_path": str(output_path),
        "expression_id": spec.expression_id,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
