#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qsys.data.adapter import QlibAdapter
from qsys.ops.instrument_coverage import (
    apply_repair_plan,
    build_instrument_coverage_rows,
    build_repair_plan,
    read_calendar_summary,
    write_json,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair stale qlib instrument coverage when feature rows prove availability.")
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--universe", default="csi300")
    parser.add_argument("--output-dir", default="experiments/ops_diagnostics/qlib_instrument_coverage")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    base_dir = Path(args.base_dir).resolve()
    output_dir = (base_dir / args.output_dir).resolve()
    adapter = QlibAdapter()
    adapter.init_qlib()
    calendar = read_calendar_summary(adapter)
    last_qlib_date = str(calendar["calendar_last_date"])

    results = {}
    for universe in ["all", args.universe]:
        rows = build_instrument_coverage_rows(adapter, universe=universe, last_qlib_date=last_qlib_date)
        plan = build_repair_plan(universe=universe, last_qlib_date=last_qlib_date, coverage_rows=rows)
        if args.apply:
            applied = apply_repair_plan(adapter, universe=universe, last_qlib_date=last_qlib_date, coverage_rows=rows)
        else:
            applied = {
                "universe": universe,
                "applied": False,
                "updated_end_date_count": 0,
                "last_qlib_date": last_qlib_date,
                "repair_targets": plan["stale_but_feature_available"],
            }
        results[universe] = {
            "plan": plan,
            "result": applied,
        }

    write_json(output_dir / "repair_result.json", results)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
