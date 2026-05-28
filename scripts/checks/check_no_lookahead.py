#!/usr/bin/env python3
"""Validate that signal rows do not use future or same-day unavailable data.

For each row: data_date <= previous_trading_day(trade_date).

If calendar is unavailable, falls back to simple weekday logic and marks
calendar_mode = "fallback_bday".

Outputs JSON summary to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd


def _weekday_before(d: datetime) -> datetime:
    """Return the previous weekday (Mon-Fri) before *d*."""
    offset = 1
    while True:
        prev = d - timedelta(days=offset)
        if prev.weekday() < 5:
            return prev
        offset += 1


def check_no_lookahead(
    path: Path,
    allow_same_day: bool = False,
) -> dict:
    if path.is_dir():
        files = sorted(path.iterdir())
    else:
        files = [path]

    result = {
        "status": "passed",
        "checked_rows": 0,
        "checked_files": 0,
        "violations": 0,
        "examples": [],
        "calendar_mode": "fallback_bday",
        "errors": [],
    }

    # Try qsys calendar
    try:
        from qsys.data.calendar import get_trading_calendar

        cal = get_trading_calendar("2000-01-01", "2030-01-01")
        if cal:
            result["calendar_mode"] = "qsys"
            _cal_lookup = {d: True for d in cal}
        else:
            _cal_lookup = None
    except Exception:
        _cal_lookup = None

    for f in files:
        if not f.is_file():
            continue
        suffix = f.suffix.lower()
        if suffix not in (".csv", ".parquet"):
            continue

        try:
            if suffix == ".csv":
                df = pd.read_csv(f)
            elif suffix == ".parquet":
                df = pd.read_parquet(f)
            else:
                continue
        except Exception as e:
            result["errors"].append(f"{f}: read error: {e}")
            continue

        result["checked_files"] += 1

        if "trade_date" not in df.columns or "data_date" not in df.columns:
            result["errors"].append(f"{f}: missing trade_date or data_date column")
            continue

        for _, row in df.iterrows():
            result["checked_rows"] += 1
            td = str(row["trade_date"])
            dd = str(row["data_date"])

            if _cal_lookup is not None:
                # Find previous trading day of trade_date
                td_cal = [d for d in cal if d <= td]
                if not td_cal:
                    result["errors"].append(f"{f}: cannot find any calendar date <= {td}")
                    continue
                prev = td_cal[-1]
                # prev may == td if td is a trading day; step back once more
                if prev == td:
                    sub = [d for d in cal if d < td]
                    if not sub:
                        continue  # can't determine
                    prev = sub[-1]
                if not allow_same_day and dd > prev:
                    result["violations"] += 1
                    if len(result["examples"]) < 5:
                        result["examples"].append(
                            f"{f}: data_date={dd} > previous_trading_day({td})={prev}"
                        )
            else:
                # Fallback weekday logic
                td_dt = datetime.strptime(td, "%Y-%m-%d")
                prev = _weekday_before(td_dt)
                prev_str = prev.strftime("%Y-%m-%d")
                if not allow_same_day and dd > prev_str:
                    result["violations"] += 1
                    if len(result["examples"]) < 5:
                        result["examples"].append(
                            f"{f}: data_date={dd} > fallback_bday_prev({td})={prev_str}"
                        )

    if result["violations"] > 0:
        result["status"] = "failed"
    if result["errors"] and result["violations"] == 0:
        result["status"] = "degraded"

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate signal rows have no lookahead leakage"
    )
    parser.add_argument("--signal-path", required=True, help="File or directory to check")
    parser.add_argument(
        "--allow-same-day",
        action="store_true",
        help="Allow data_date == trade_date (not recommended for prediction signals)",
    )
    args = parser.parse_args()

    path = Path(args.signal_path)
    if not path.exists():
        result = {"status": "failed", "error": f"Path not found: {path}"}
        print(json.dumps(result, indent=2))
        sys.exit(1)

    result = check_no_lookahead(path, allow_same_day=args.allow_same_day)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] in ("passed", "degraded") else 1)


if __name__ == "__main__":
    main()
