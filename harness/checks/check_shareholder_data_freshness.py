#!/usr/bin/env python3
"""Audit PIT shareholder sidecars and inventory downstream affected artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qsys.common.config import load_strategy_config
from qsys.feature.freshness import normalise_shareholder_freshness
from qsys.ops.shareholder_sync import (
    audit_shareholder_impact,
    inspect_shareholder_sidecar_health,
)
from qsys.signal.model_blend_inference import (
    load_open_dates,
    load_universe_snapshot_members,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--strategy-id", default="financial_rc")
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument(
        "--config-path",
        type=Path,
        help="Explicit strategy config (useful while validating a migration)",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--quiet", action="store_true", help="Write report without stdout")
    args = parser.parse_args()

    root = args.project_root.resolve()
    config = load_strategy_config(
        args.strategy_id, root, config_path=args.config_path
    )
    contract = normalise_shareholder_freshness(
        config.get("feature_freshness", {}).get("shareholder")
    )
    universe = str(config.get("universe") or "csi800")
    symbols = load_universe_snapshot_members(root, universe, args.as_of_date)
    open_dates = load_open_dates(root)
    health = inspect_shareholder_sidecar_health(
        project_root=root,
        symbols=symbols,
        as_of_date=args.as_of_date,
        contract=contract,
    )
    impact = audit_shareholder_impact(
        project_root=root,
        symbols=symbols,
        open_dates=open_dates,
        as_of_date=args.as_of_date,
        contract=contract,
    )
    report = {"health": health, "impact": impact}
    rendered = json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    if not args.quiet:
        print(rendered)
    return 0 if health["status"] == "pass" and impact["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
