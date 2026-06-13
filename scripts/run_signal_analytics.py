#!/usr/bin/env python3
"""Signal Analytics CLI — UC-5.

Query IC, RankIC, ICIR for existing SignalRun artifacts.  Supports
query by experiment_id (auto-discovers SignalRunRefs) or explicit
signal/signal-run/label IDs.

Usage::

    python scripts/run_signal_analytics.py --experiment-id lightgbm_csi800_10d
    python scripts/run_signal_analytics.py \\
        --signal-id lgbm_csi800_10d --signal-run-id sig__cs_zscore \\
        --label-id fwd_ret_10d_xsz_clip3
    python scripts/run_signal_analytics.py --experiment-id my_exp --output-dir .
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _discover_from_experiment(
    experiment_id: str,
    research_root: str,
) -> tuple[list[str], list[str], list[str]]:
    """Discover signal_ids, signal_run_ids, label_ids from an experiment manifest."""
    manifest_path = (
        Path(research_root) / "experiments" / experiment_id / "signal_research_manifest.json"
    )
    if not manifest_path.exists():
        print(f"Experiment manifest not found: {manifest_path}", file=sys.stderr)
        sys.exit(1)
    manifest = json.loads(manifest_path.read_text())
    signal_ids = []
    signal_run_ids = []
    label_ids = []

    for sr in manifest.get("signal_runs", []):
        sid = sr.get("signal_id")
        srid = sr.get("signal_run_id")
        if sid and sid not in signal_ids:
            signal_ids.append(sid)
        if srid and srid not in signal_run_ids:
            signal_run_ids.append(srid)

    # Also scan eval refs
    for er in manifest.get("eval_refs", []):
        lid = er.get("label_id")
        if lid and lid not in label_ids:
            label_ids.append(lid)

    return signal_ids, signal_run_ids, label_ids


def main() -> None:
    parser = argparse.ArgumentParser(description="Signal Analytics — UC-5")
    parser.add_argument("--experiment-id", default=None,
                        help="Auto-discover SignalRunRefs from experiment manifest")
    parser.add_argument("--signal-id", default=None,
                        help="Signal ID (required without --experiment-id)")
    parser.add_argument("--signal-run-id", default=None,
                        help="Signal run ID (optional, uses latest if omitted)")
    parser.add_argument("--label-id", default=None,
                        help="Label ID (required without --experiment-id)")
    parser.add_argument("--start-date", default=None, help="Filter start date")
    parser.add_argument("--end-date", default=None, help="Filter end date")
    parser.add_argument("--min-count", type=int, default=5,
                        help="Min observations for IC (default 5)")
    parser.add_argument("--output-dir", default=None,
                        help="Persist analytics CSVs to this directory")
    parser.add_argument("--research-root", default="data/research",
                        help="Research root path (default data/research)")
    args = parser.parse_args()

    from qsys.research.signal_analytics import SignalAnalytics

    with SignalAnalytics(root=args.research_root) as sa:
        if args.experiment_id:
            signal_ids, signal_run_ids, label_ids = _discover_from_experiment(
                args.experiment_id, args.research_root,
            )
            print(f"Experiment: {args.experiment_id}")
            print(f"  Signal IDs: {signal_ids}")
            print(f"  Signal run IDs: {signal_run_ids}")
            print(f"  Label IDs: {label_ids}")
        else:
            signal_ids = [args.signal_id] if args.signal_id else []
            signal_run_ids = [args.signal_run_id] if args.signal_run_id else []
            label_ids = [args.label_id] if args.label_id else []

        if not signal_ids or not label_ids:
            print("No signals or labels to analyse. Use --experiment-id or --signal-id + --label-id.",
                  file=sys.stderr)
            sys.exit(1)

        # IC matrix
        print(f"\n--- IC Matrix ---")
        ic_df = sa.compute_ic_matrix(
            signal_ids=signal_ids if len(signal_ids) > 1 else None,
            signal_run_ids=signal_run_ids if len(signal_run_ids) > 1 else None,
            label_ids=label_ids if len(label_ids) > 1 else None,
            start_date=args.start_date,
            end_date=args.end_date,
            min_count=args.min_count,
        )
        if ic_df is not None and not ic_df.empty:
            print(ic_df.to_string(index=False))

        # Rank IC matrix
        print(f"\n--- Rank IC Matrix ---")
        ric_df = sa.compute_rank_ic_matrix(
            signal_ids=signal_ids if len(signal_ids) > 1 else None,
            signal_run_ids=signal_run_ids if len(signal_run_ids) > 1 else None,
            label_ids=label_ids if len(label_ids) > 1 else None,
            start_date=args.start_date,
            end_date=args.end_date,
            min_count=args.min_count,
        )
        if ric_df is not None and not ric_df.empty:
            print(ric_df.to_string(index=False))

        # Daily IC for first signal
        if signal_ids and label_ids:
            sid = signal_ids[0]
            srid = signal_run_ids[0] if signal_run_ids else None
            lid = label_ids[0]
            print(f"\n--- Daily IC: {sid} / {srid or 'latest'} vs {lid} ---")
            daily = sa.daily_ic(
                signal_id=sid,
                signal_run_id=srid,
                label_id=lid,
                start_date=args.start_date,
                end_date=args.end_date,
            )
            if daily is not None and not daily.empty:
                print(daily.head(10).to_string(index=False))
                print(f"  ... {len(daily)} days total")

        # Persist output
        if args.output_dir:
            out = Path(args.output_dir)
            out.mkdir(parents=True, exist_ok=True)
            if ic_df is not None and not ic_df.empty:
                ic_df.to_csv(out / "ic_matrix.csv", index=False)
            if ric_df is not None and not ric_df.empty:
                ric_df.to_csv(out / "rank_ic_matrix.csv", index=False)

    print("\nDone.")


if __name__ == "__main__":
    main()
