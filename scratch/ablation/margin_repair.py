#!/usr/bin/env python3
"""U2 margin repair — merge margin_detail back into canonical feathers.

WHY: the 4-parallel-worker U2 full backfill hit Tushare margin_detail's 200/min
rate limit, so the ~1797 CSI1000-only symbols came out with their 7 margin
columns 100% empty (margin_cols silently added to ignore_columns).  The
v3a_plus_liquidity_financial_rc feature set has 11 margin-derived features, so a
zeroed margin column set would confound the U0 vs U2 diagnostic (model would
learn "csi1000-only <=> margin all-zero" as a proxy).

FIX: this pass re-fetches margin for the backfilled symbols SERIALIZED with
per-call pacing (~0.45s => <=~130 calls/min, safely under the 200/min cap) and
merges the 7 renamed margin columns into the existing canonical feathers.
Must run AFTER the main backfill has written the final daily/basic/financial
base (otherwise --full-backfill would overwrite the merged margin again).

Rate-limit handling: on "频率超限" we backoff 2/4/6/8/10s and retry up to 5x.
Distinguishes "genuinely empty" (not margin-eligible -> OK) from
"rate-limit-exhausted" (needs a re-run) and reports both.

Usage (from MAIN SysQ cwd):
    python scratch/ablation/margin_repair.py --symbols-file /tmp/u2_missing_symbols.txt [--dry-run] [--limit 100]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/home/liuming/.openclaw/workspace/SysQ")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qsys.data.collector import TushareCollector  # noqa: E402

START_DATE = "20100101"
END_DATE = "20260821"
PACING_SEC = 0.45  # ~130 calls/min, safely under margin_detail 200/min cap
MAX_ATTEMPTS = 5
MARGIN_COLS = [
    "margin_balance", "margin_buy_amount", "margin_repay_amount",
    "margin_total_balance", "lend_volume", "lend_sell_volume", "lend_repay_volume",
]


def fetch_margin(collector, code: str) -> tuple[pd.DataFrame | None, str]:
    """Fetch margin_detail for one code with rate-limit backoff.

    Returns (df, status): status in {"ok", "empty", "error", "rate-limit-exhausted"}.
    """
    api = collector._get_interface_api("margin")
    fields = collector._get_interface_fields("margin")
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            df = api(ts_code=code, start_date=START_DATE, end_date=END_DATE, fields=fields)
        except Exception as e:  # noqa: BLE001 — Tushare raises generic Exception on API error
            msg = str(e)
            if "频率超限" in msg or "每分钟" in msg:
                time.sleep(2 * attempt)  # 2/4/6/8/10s backoff
                continue
            return None, f"error: {msg[:120]}"
        if df is None or df.empty:
            return pd.DataFrame(), "empty"
        return df, "ok"
    return None, "rate-limit-exhausted"


def repair_symbol(collector, code: str, dry_run: bool) -> str:
    """Merge margin into one symbol's feather. Returns a status tag."""
    existing = collector.store.load_daily(code)
    if existing is None or existing.empty:
        return "no-feather"

    df, status = fetch_margin(collector, code)
    if status != "ok":
        return status

    # Rename raw margin fields (rzye -> margin_balance etc.) and keep margin cols.
    rename = collector._get_interface_rename("margin")
    df = df.rename(columns=rename)
    keep = ["ts_code", "trade_date"] + [c for c in MARGIN_COLS if c in df.columns]
    df = df[keep].copy()

    # Normalize trade_date to YYYYMMDD string like the feather.
    ex = existing.copy()
    ex["trade_date"] = ex["trade_date"].astype(str)
    df["trade_date"] = df["trade_date"].astype(str)

    # Merge margin columns onto the existing frame (left join on trade_date).
    ex = ex.merge(df, on=["trade_date"], how="left", suffixes=("", "_m"))
    # Drop duplicated ts_code from the margin side if present.
    if "ts_code_m" in ex.columns:
        ex = ex.drop(columns=["ts_code_m"])
    for c in MARGIN_COLS:
        if c in ex.columns:
            ex[c] = pd.to_numeric(ex[c], errors="coerce")

    if dry_run:
        return f"dry-run-{len(df)}"
    collector.store.save_daily(ex, code, existing_df=existing)
    return "ok"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols-file", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="only repair first N symbols (debug)")
    args = ap.parse_args()

    symbols = [
        line.strip() for line in Path(args.symbols_file).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.limit:
        symbols = symbols[: args.limit]
    print(f"repairing {len(symbols)} symbols (dry_run={args.dry_run})", flush=True)

    collector = TushareCollector()
    counts: dict[str, int] = {}
    failed: list[str] = []
    for i, code in enumerate(symbols, 1):
        tag = repair_symbol(collector, code, args.dry_run)
        counts[tag] = counts.get(tag, 0) + 1
        if tag.startswith("error") or tag == "rate-limit-exhausted":
            failed.append(code)
        if i % 100 == 0 or i == len(symbols):
            print(f"  [{i}/{len(symbols)}] {tag}  counts={counts}", flush=True)
        time.sleep(PACING_SEC)

    print(f"\n=== margin repair done: {json.dumps(counts, ensure_ascii=False)} ===")
    if failed:
        print(f"WARNING {len(failed)} symbols NOT repaired (re-run needed):")
        print("\n".join(failed[:20]) + (" ..." if len(failed) > 20 else ""))
    else:
        print("no failed symbols")
    return 0


if __name__ == "__main__":
    sys.exit(main())
