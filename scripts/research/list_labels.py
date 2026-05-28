#!/usr/bin/env python3
"""List saved label artifacts under a research root.

Usage::

    python scripts/research/list_labels.py
    python scripts/research/list_labels.py --root data/research --format json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from qsys.label.store import LabelStore  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="List saved label artifacts")
    parser.add_argument("--root", default="data/research", help="Research root path")
    parser.add_argument("--format", choices=["table", "json"], default="table")
    args = parser.parse_args()

    store = LabelStore(args.root)
    df = store.list_labels()

    if df.empty:
        print("No labels found.")
        return

    if args.format == "json":
        print(df.to_json(orient="records", indent=2))
    else:
        col_w = max(df["label_id"].str.len().max() if len(df) > 0 else 8, 10)
        print(f"{'label_id':<{col_w}}  {'rows':>8}  {'start':<10}  {'end':<10}  {'created_at'}")
        print("-" * (col_w + 60))
        for _, r in df.iterrows():
            rc = str(r.get("row_count", "")) or ""
            ps = str(r.get("prediction_start", "")) or ""
            pe = str(r.get("prediction_end", "")) or ""
            ca = str(r.get("created_at", "")) or ""
            print(f"{r['label_id']:<{col_w}}  {rc:>8}  {ps:<10}  {pe:<10}  {ca}")


if __name__ == "__main__":
    main()
