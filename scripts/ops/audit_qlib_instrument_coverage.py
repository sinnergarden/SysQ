#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qsys.data.adapter import QlibAdapter
from qsys.ops.instrument_coverage import (
    build_instrument_coverage_rows,
    build_repair_plan,
    read_calendar_summary,
    summarize_universe_registry,
    write_json,
)


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit qlib instrument coverage.")
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--universe", default="csi300")
    parser.add_argument("--output-dir", default="experiments/ops_diagnostics/qlib_instrument_coverage")
    args = parser.parse_args()

    base_dir = Path(args.base_dir).resolve()
    output_dir = (base_dir / args.output_dir).resolve()
    adapter = QlibAdapter()
    adapter.init_qlib()

    calendar_summary = read_calendar_summary(adapter)
    last_qlib_date = str(calendar_summary["calendar_last_date"])
    all_summary = summarize_universe_registry(adapter, universe="all", trade_date=last_qlib_date).to_dict()
    csi300_summary = summarize_universe_registry(adapter, universe=args.universe, trade_date=last_qlib_date).to_dict()
    all_rows = build_instrument_coverage_rows(adapter, universe="all", last_qlib_date=last_qlib_date)
    csi300_rows = build_instrument_coverage_rows(adapter, universe=args.universe, last_qlib_date=last_qlib_date)
    repair_plan = {
        "all": build_repair_plan(universe="all", last_qlib_date=last_qlib_date, coverage_rows=all_rows),
        args.universe: build_repair_plan(universe=args.universe, last_qlib_date=last_qlib_date, coverage_rows=csi300_rows),
        "all_universe_total": all_summary["instrument_total"],
        "warning": "all universe appears incomplete for A-share full universe" if all_summary["instrument_total"] < 1000 else None,
    }

    feature_row_coverage = []
    for universe, rows in [("all", all_rows), (args.universe, csi300_rows)]:
        for row in rows:
            feature_row_coverage.append({"universe": universe, **row})
    instrument_summary = {
        "calendar_last_date": last_qlib_date,
        "all": all_summary,
        args.universe: csi300_summary,
    }

    write_json(output_dir / "calendar_summary.json", calendar_summary)
    write_json(output_dir / "instrument_coverage_summary.json", instrument_summary)
    _write_csv(output_dir / "all_instrument_coverage.csv", all_rows, ["instrument", "instrument_file_end_date", "feature_last_date", "has_feature_on_last_qlib_date", "is_active_by_instrument_file", "coverage_mismatch_reason"])
    _write_csv(output_dir / f"{args.universe}_instrument_coverage.csv", csi300_rows, ["instrument", "instrument_file_end_date", "feature_last_date", "has_feature_on_last_qlib_date", "is_active_by_instrument_file", "coverage_mismatch_reason"])
    _write_csv(output_dir / "feature_row_coverage.csv", feature_row_coverage, ["universe", "instrument", "instrument_file_end_date", "feature_last_date", "has_feature_on_last_qlib_date", "is_active_by_instrument_file", "coverage_mismatch_reason"])
    write_json(output_dir / "repair_plan.json", repair_plan)
    print(json.dumps({"calendar": calendar_summary, "coverage": instrument_summary, "repair_plan": repair_plan}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
