#!/usr/bin/env python3
"""Compute and persist label artifacts — UC-3.

Usage:
    python scripts/compute_labels.py --config configs/labels/<id>.yaml
    python scripts/compute_labels.py --universe csi800 --horizons 5 20 --overwrite
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qsys.data.calendar import get_trading_calendar
from qsys.data.adapter import QlibAdapter
from qsys.label.compute import compute_forward_return, compute_raw_forward_return, coverage
from qsys.label.store import LabelStore


def main() -> None:
    p = argparse.ArgumentParser(description="Compute and persist label artifacts to LabelStore")
    p.add_argument("--config", default=None, help="Path to label YAML config")
    p.add_argument("--universe", default="csi300", help="Qlib universe")
    p.add_argument("--start", default="2023-06-01", help="Start date (YYYY-MM-DD)")
    p.add_argument("--end", default="2026-06-01", help="End date (YYYY-MM-DD)")
    p.add_argument("--horizons", type=int, nargs="+", default=[5, 20], help="Forward return horizons")
    p.add_argument("--overwrite", action="store_true", help="Overwrite existing label data")
    args = p.parse_args()

    if args.config:
        import yaml
        config = yaml.safe_load(Path(args.config).read_text())
        config.setdefault("date_range", {"start_date": args.start, "end_date": args.end})
        LabelStore().compute_and_save_from_config(config, overwrite=args.overwrite)
        print(f"Done: {config['label_id']}")
        return

    store = LabelStore()
    QlibAdapter().init_qlib()
    cal = get_trading_calendar(args.start, args.end)
    n_dates = len(cal) if cal else 0

    from qlib.data import D
    insts = D.instruments(args.universe)
    n_insts = len(insts) if insts else 0

    for h in args.horizons:
        effective_dates = max(n_dates - h, 0)
        expected_rows = effective_dates * n_insts if n_dates and n_insts else 0

        label_id = f"fwd_ret_{h}d_cs_zscore_clip3"
        print(f"Computing {label_id} ...")
        df = compute_forward_return(args.universe, h, args.start, args.end)
        cov = coverage(len(df), expected_rows)
        print(f"  {len(df)} rows, effective_dates={effective_dates}, coverage={cov:.1%}")
        store.save_labels(label_id, df, manifest={
            "horizon": h, "universe": args.universe,
            "prediction_start": args.start, "prediction_end": args.end,
            "formula": f"shift(-{h}) / close - 1, then per-date cs_zscore",
            "normalization": "cross-sectional zscore", "clip": 3.0,
            "n_dates": n_dates, "effective_dates": effective_dates, "coverage": round(cov, 4),
        }, overwrite=args.overwrite)
        print(f"  Saved {label_id}")

        raw_id = f"fwd_ret_{h}d_raw"
        print(f"Computing {raw_id} ...")
        df_raw = compute_raw_forward_return(args.universe, h, args.start, args.end)
        store.save_labels(raw_id, df_raw, manifest={
            "horizon": h, "universe": args.universe,
            "prediction_start": args.start, "prediction_end": args.end,
            "formula": f"shift(-{h}) / close - 1", "normalization": "none",
            "n_dates": n_dates, "effective_dates": effective_dates,
            "coverage": round(coverage(len(df_raw), expected_rows), 4),
        }, overwrite=args.overwrite)
        print(f"  Saved {raw_id} ({len(df_raw)} rows)")

    print("Done.")


if __name__ == "__main__":
    main()
