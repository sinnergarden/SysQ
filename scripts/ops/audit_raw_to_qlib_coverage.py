#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Audit raw -> qlib coverage gaps without mutating data.")
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--universe", default="csi300")
    parser.add_argument("--output-dir", default="experiments/ops_diagnostics/raw_to_qlib_coverage")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument(
        "--suspension-evidence",
        help="Explicit trusted SourceAudit terminal receipt JSON (historical mode only).",
    )
    parser.add_argument(
        "--max-gap-details",
        type=int,
        default=100_000,
        help="Fail closed if retained historical raw+Qlib gap rows exceed this cap.",
    )
    args = parser.parse_args(argv)

    if bool(args.start_date) != bool(args.end_date):
        parser.error("--start-date and --end-date must be provided together")
    if args.start_date and not args.suspension_evidence:
        parser.error("historical mode requires --suspension-evidence PATH")
    if args.max_gap_details < 0:
        parser.error("--max-gap-details must be non-negative")

    from qsys.data.adapter import QlibAdapter
    from qsys.ops.data_coverage import (
        HISTORICAL_BACKFILL_PLAN_FIELDS,
        HISTORICAL_QLIB_GAP_FIELDS,
        HISTORICAL_RAW_GAP_FIELDS,
        LATEST_GAP_FIELDS,
        LATEST_QLIB_SYMBOL_FIELDS,
        LATEST_RAW_SYMBOL_FIELDS,
        HistoricalGapDetailLimitExceeded,
        apply_suspension_overrides,
        build_gap_rows,
        build_historical_backfill_plan,
        build_historical_gap_summary,
        decide_root_cause,
        inspect_collector_status,
        load_instrument_sets,
        load_local_suspension_evidence,
        load_universe_instruments,
        read_calendar_dates,
        scan_historical_qlib_gaps,
        scan_historical_raw_gaps,
        scan_qlib_coverage,
        scan_raw_coverage,
    )
    from qsys.utils.json_io import write_csv, write_json

    base_dir = Path(args.base_dir).resolve()
    output_dir = (base_dir / args.output_dir).resolve()
    adapter = QlibAdapter()
    adapter.init_qlib()
    last_qlib_date = adapter.get_last_qlib_date().strftime("%Y-%m-%d")

    all_symbols, csi300_symbols, all_df, _ = load_instrument_sets(adapter)
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
    write_csv(output_dir / "raw_symbol_coverage.csv", raw_rows, LATEST_RAW_SYMBOL_FIELDS)
    write_json(output_dir / "qlib_feature_coverage_summary.json", qlib_summary)
    write_csv(output_dir / "qlib_symbol_coverage.csv", qlib_rows, LATEST_QLIB_SYMBOL_FIELDS)
    write_csv(output_dir / "raw_vs_qlib_gap.csv", gap_rows, LATEST_GAP_FIELDS)
    write_json(output_dir / "collector_status_summary.json", collector_summary)
    write_json(output_dir / "coverage_root_cause.json", root_cause)

    result: dict[str, object] = {
        "raw_coverage_summary": raw_summary,
        "qlib_feature_coverage_summary": qlib_summary,
        "collector_status_summary": collector_summary,
        "coverage_root_cause": root_cause,
    }

    if args.start_date and args.end_date:
        universe_symbols, universe_df = load_universe_instruments(adapter, args.universe)
        calendar_dates = read_calendar_dates(adapter, start_date=args.start_date, end_date=args.end_date)
        suspension_evidence = load_local_suspension_evidence(
            args.suspension_evidence,
            symbols=universe_symbols,
            start_date=args.start_date,
            end_date=args.end_date,
            universe=args.universe,
        )
        try:
            historical_raw_rows, historical_raw_summary = scan_historical_raw_gaps(
                adapter.raw_dir,
                symbols=universe_symbols,
                instrument_rows=universe_df,
                calendar_dates=calendar_dates,
                start_date=args.start_date,
                end_date=args.end_date,
                max_gap_details=args.max_gap_details,
            )
            remaining_gap_details = args.max_gap_details - len(historical_raw_rows)
            historical_qlib_rows, historical_qlib_summary = scan_historical_qlib_gaps(
                adapter,
                symbols=universe_symbols,
                instrument_rows=universe_df,
                calendar_dates=calendar_dates,
                start_date=args.start_date,
                end_date=args.end_date,
                max_gap_details=remaining_gap_details,
            )
        except HistoricalGapDetailLimitExceeded as exc:
            blocked = {
                "status": "BLOCKED",
                "reason": "historical_gap_detail_limit_exceeded",
                "max_gap_details": args.max_gap_details,
                "error": str(exc),
            }
            print(json.dumps(blocked, ensure_ascii=False, indent=2), file=sys.stderr)
            raise SystemExit(2) from exc
        historical_raw_rows, historical_qlib_rows = apply_suspension_overrides(
            raw_gap_rows=historical_raw_rows,
            qlib_gap_rows=historical_qlib_rows,
            raw_summary=historical_raw_summary,
            qlib_summary=historical_qlib_summary,
            suspended_dates_by_symbol=suspension_evidence[
                "suspended_dates_by_symbol"
            ],
        )
        backfill_plan_rows = build_historical_backfill_plan(
            symbols=universe_symbols,
            calendar_dates=calendar_dates,
            raw_gap_rows=historical_raw_rows,
            qlib_gap_rows=historical_qlib_rows,
        )
        historical_summary = build_historical_gap_summary(
            universe=args.universe,
            start_date=args.start_date,
            end_date=args.end_date,
            symbols=universe_symbols,
            calendar_dates=calendar_dates,
            raw_gap_rows=historical_raw_rows,
            qlib_gap_rows=historical_qlib_rows,
            raw_scan_summary=historical_raw_summary,
            qlib_scan_summary=historical_qlib_summary,
            backfill_plan_rows=backfill_plan_rows,
            qlib_audit_mode=historical_qlib_summary["qlib_audit_mode"],
        )
        historical_summary["gap_detail_limit"] = args.max_gap_details
        historical_summary["suspension_evidence"] = {
            key: suspension_evidence[key]
            for key in (
                "status",
                "path",
                "sha256",
                "run_id",
                "scope_key",
                "universe",
                "shard_count",
                "payload_count",
                "row_count",
            )
        }

        write_csv(output_dir / "historical_raw_gap.csv", historical_raw_rows, HISTORICAL_RAW_GAP_FIELDS)
        write_csv(output_dir / "historical_qlib_gap.csv", historical_qlib_rows, HISTORICAL_QLIB_GAP_FIELDS)
        write_json(output_dir / "historical_gap_summary.json", historical_summary)
        write_csv(output_dir / "historical_backfill_plan.csv", backfill_plan_rows, HISTORICAL_BACKFILL_PLAN_FIELDS)

        result["historical_gap_summary"] = historical_summary

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
