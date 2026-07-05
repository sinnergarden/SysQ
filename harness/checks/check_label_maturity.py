#!/usr/bin/env python3
"""Check label maturity: given trade_date and horizon, determine latest mature label date.

Usage:
    python harness/checks/check_label_maturity.py \
        --trade-date YYYY-MM-DD \
        --horizon 5 \
        --train-end YYYY-MM-DD

Output:
    PASS if train_end <= latest_mature_label_date, FAIL otherwise.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta


def _weekday_calendar_date(date_str: str, offset_days: int) -> str:
    """Simple weekday calendar: move offset_days business days forward from date_str.

    TODO: Replace with qlib trading calendar when available.
      Use: from qsys.data.calendar import get_trading_calendar
           cal = get_trading_calendar(start_date, end_date)
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    count = 0
    while count < offset_days:
        dt += timedelta(days=1)
        if dt.weekday() < 5:
            count += 1
    return dt.strftime("%Y-%m-%d")


def check_label_maturity(trade_date: str, horizon: int, train_end: str) -> tuple[str, str, str, str, str]:
    """Returns (trade_date, horizon, latest_mature_label_date, train_end, verdict)."""
    latest_mature = _weekday_calendar_date(trade_date, horizon)
    verdict = "PASS" if train_end <= latest_mature else "FAIL"
    return (trade_date, str(horizon), latest_mature, train_end, verdict)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check label maturity for training")
    parser.add_argument("--trade-date", required=True, help="Target trade date (YYYY-MM-DD)")
    parser.add_argument("--horizon", required=True, type=int, help="Label horizon in trading days")
    parser.add_argument("--train-end", required=True, help="Requested train end date (YYYY-MM-DD)")
    args = parser.parse_args()

    trade_date, horizon, latest_mature, train_end, verdict = check_label_maturity(
        args.trade_date, args.horizon, args.train_end,
    )

    print(f"trade_date:                  {trade_date}")
    print(f"horizon:                     {horizon}")
    print(f"latest_mature_label_date:    {latest_mature}")
    print(f"train_end:                   {train_end}")
    print(f"verdict:                     {verdict}")
    print()
    print("NOTE: Uses weekday calendar. TODO: replace with qlib trading calendar.")

    if verdict == "FAIL":
        print("FAIL: train_end is after latest mature label date.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
