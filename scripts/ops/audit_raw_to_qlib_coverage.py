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
from qsys.ops.data_coverage import (
    build_gap_rows,
    decide_root_cause,
    inspect_collector_status,
    load_instrument_sets,
    scan_qlib_coverage,
    scan_raw_coverage,
    write_csv,
    write_json,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit raw -> qlib coverage gaps without mutating data.")
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--universe", default="csi300")
    parser.add_argument("--output-dir", default="experiments/ops_diagnostics/raw_to_qlib_coverage")
    args = parser.parse_args()

    base_dir = Path(args.base_dir).resolve()
    output_dir = (base_dir / args.output_dir).resolve()
    adapter = QlibAdapter()
    adapter.init_qlib()
    last_qlib_date = adapter.get_last_qlib_date().strftime("%Y-%m-%d")

    all_symbols, csi300_symbols, all_df, csi300_df = load_instrument_sets(adapter)
    raw_rows, raw_summary = scan_raw_coverage(
        adapter.raw_dir,
        latest_date=last_qlib_date,
        csi300_symbols=csi300_symbols,
        all_symbols=all_symbols,
    )
    qlib_rows, qlib_summary = scan_qlib_coverage(
        adapter,
        latest_date=last_qlib_date,
        all_symbols=all_symbols,
        csi300_symbols=csi300_symbols,
    )
    gap_rows = build_gap_rows(
        raw_rows=raw_rows,
        qlib_rows=qlib_rows,
        instrument_rows=all_df,
        csi300_symbols=csi300_symbols,
        all_symbols=all_symbols,
        last_qlib_date=last_qlib_date,
    )
    collector_summary = inspect_collector_status(
        project_root=base_dir,
        all_instrument_count=len(all_symbols),
        csi300_instrument_count=len(csi300_symbols),
        raw_symbol_count=raw_summary["raw_symbol_count"],
        raw_latest_count=raw_summary["symbols_with_raw_on_latest"],
        qlib_latest_count=qlib_summary["symbols_with_qlib_on_latest"],
    )
    root_cause = decide_root_cause(
        raw_summary=raw_summary,
        qlib_summary=qlib_summary,
        collector_summary=collector_summary,
    )

    write_json(output_dir / "raw_coverage_summary.json", raw_summary)
    write_csv(
        output_dir / "raw_symbol_coverage.csv",
        raw_rows,
        [
            "symbol",
            "raw_row_count",
            "raw_first_date",
            "raw_last_date",
            "has_raw_on_last_qlib_date",
            "required_fields_present",
            "required_fields_non_null",
            "missing_required_fields",
            "in_all_instruments",
            "in_csi300_instruments",
        ],
    )
    write_json(output_dir / "qlib_feature_coverage_summary.json", qlib_summary)
    write_csv(
        output_dir / "qlib_symbol_coverage.csv",
        qlib_rows,
        [
            "symbol",
            "qlib_first_date",
            "qlib_last_date",
            "has_qlib_on_last_qlib_date",
            "core_fields_available",
            "core_fields_non_null",
            "in_all_instruments",
            "in_csi300_instruments",
        ],
    )
    write_csv(
        output_dir / "raw_vs_qlib_gap.csv",
        gap_rows,
        [
            "symbol",
            "in_all_instruments",
            "in_csi300_instruments",
            "raw_last_date",
            "qlib_last_date",
            "instrument_end_date",
            "gap_type",
            "reason",
        ],
    )
    write_json(output_dir / "collector_status_summary.json", collector_summary)
    write_json(output_dir / "coverage_root_cause.json", root_cause)

    print(
        json.dumps(
            {
                "raw_coverage_summary": raw_summary,
                "qlib_feature_coverage_summary": qlib_summary,
                "collector_status_summary": collector_summary,
                "coverage_root_cause": root_cause,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
