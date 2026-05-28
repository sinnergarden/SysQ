#!/usr/bin/env python3
"""List saved signal runs under a research root.

Usage::

    python scripts/research/list_signals.py
    python scripts/research/list_signals.py --root data/research --signal-id alpha_v1_score
    python scripts/research/list_signals.py --root data/research --format json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from qsys.signal.store import SignalStore  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="List saved signal runs")
    parser.add_argument("--root", default="data/research", help="Research root path")
    parser.add_argument("--signal-id", default=None, help="Filter by signal_id")
    parser.add_argument("--format", choices=["table", "json"], default="table")
    args = parser.parse_args()

    store = SignalStore(args.root)
    df = store.list_signal_runs(signal_id=args.signal_id)

    if df.empty:
        print("No signal runs found.")
        return

    if args.format == "json":
        print(df.to_json(orient="records", indent=2))
    else:
        sid_w = max(df["signal_id"].str.len().max() if len(df) > 0 else 10, 10)
        rid_w = max(df["signal_run_id"].str.len().max() if len(df) > 0 else 14, 14)
        print(f"{'signal_id':<{sid_w}}  {'signal_run_id':<{rid_w}}  {'rows':>8}  {'start':<10}  {'end':<10}  {'created_at'}")
        print("-" * (sid_w + rid_w + 60))
        for _, r in df.iterrows():
            rc = str(r.get("row_count", "")) or ""
            ps = str(r.get("prediction_start", "")) or ""
            pe = str(r.get("prediction_end", "")) or ""
            ca = str(r.get("created_at", "")) or ""
            print(f"{r['signal_id']:<{sid_w}}  {r['signal_run_id']:<{rid_w}}  {rc:>8}  {ps:<10}  {pe:<10}  {ca}")


if __name__ == "__main__":
    main()
