#!/usr/bin/env python3
"""Signal Analytics CLI — UC-5.

Usage:
    python scripts/run_signal_analytics.py --experiment-id <id>
    python scripts/run_signal_analytics.py --signal-id <id> --label-id <id>
"""
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

def _resolve_manifest(experiment_id, research_root):
    mp = Path(research_root) / "experiments" / experiment_id / "signal_research_manifest.json"
    if not mp.exists(): print(f"Manifest not found: {mp}"); sys.exit(1)
    import json; m = json.loads(mp.read_text())
    refs = {}
    for sr in m.get("signal_runs", []):
        if sr.get("signal_id") and sr.get("signal_run_id"): refs[sr["signal_id"]] = sr["signal_run_id"]
    for csr in m.get("combined_signal_runs", []):
        if csr.get("signal_id") and csr.get("signal_run_id"): refs[csr["signal_id"]] = csr["signal_run_id"]
    lids = list(dict.fromkeys(er.get("label_id") for er in m.get("eval_refs", []) if er.get("label_id")))
    if not lids: lids = list(dict.fromkeys(l.get("label_id") for l in m.get("labels", []) if l.get("label_id")))
    return refs, lids

def main():
    p = argparse.ArgumentParser(description="Signal Analytics — UC-5")
    p.add_argument("--experiment-id", default=None)
    p.add_argument("--signal-id", default=None); p.add_argument("--signal-run-id", default=None)
    p.add_argument("--label-id", default=None)
    p.add_argument("--start-date", default=None); p.add_argument("--end-date", default=None)
    p.add_argument("--min-count", type=int, default=5)
    p.add_argument("--output-dir", default=None); p.add_argument("--research-root", default="data/research")
    args = p.parse_args()
    from qsys.research.signal_analytics import SignalAnalytics
    with SignalAnalytics(root=args.research_root) as sa:
        if args.experiment_id:
            refs, lids = _resolve_manifest(args.experiment_id, args.research_root)
            print(f"Experiment: {args.experiment_id}")
            for sid, srid in refs.items():
                print(f"\n--- {sid} / {srid} vs {lids} ---")
                ic = sa.compute_ic_matrix(signal_ids=[sid], signal_run_ids=[srid], label_ids=lids if lids else None, start_date=args.start_date, end_date=args.end_date, min_count=args.min_count)
                if ic is not None and not ic.empty: print(ic.to_string(index=False))
        elif args.signal_id and args.label_id:
            srids = [args.signal_run_id] if args.signal_run_id else None
            print(f"\n--- IC: {args.signal_id} vs {args.label_id} ---")
            ic = sa.compute_ic_matrix(signal_ids=[args.signal_id], signal_run_ids=srids, label_ids=[args.label_id], start_date=args.start_date, end_date=args.end_date, min_count=args.min_count)
            if ic is not None and not ic.empty: print(ic.to_string(index=False))
        if args.output_dir:
            Path(args.output_dir).mkdir(parents=True, exist_ok=True)

if __name__ == "__main__":
    main()
