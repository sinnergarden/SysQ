#!/usr/bin/env python3
"""Signal Research CLI — UC-4 / UC-6.

Usage:
    python scripts/run_research.py --config configs/research/exp.yaml
"""
import argparse, json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from qsys.research.signal_pipeline import SignalResearchPipeline
from qsys.research.matrix_job import RollingResearchConfig

def main():
    p = argparse.ArgumentParser(description="Signal Research — UC-4 / UC-6")
    p.add_argument("--config", required=True)
    p.add_argument("--overwrite-signal", action="store_true")
    p.add_argument("--overwrite-eval", action="store_true")
    args = p.parse_args()
    config = RollingResearchConfig.from_file(Path(args.config))
    result = SignalResearchPipeline().run(config, overwrite_signal=args.overwrite_signal, overwrite_eval=args.overwrite_eval)
    print(f"\nExperiment: {config.experiment_id}")
    for sr in result.signal_runs:
        print(f"  Signal: {sr.signal_id} / {sr.signal_run_id}")
    for er in result.eval_refs:
        sp = Path(str(er.eval_id)) / "summary.json"
        if sp.exists():
            d = json.loads(sp.read_text())
            print(f"  {er.label_id}: IC={d.get('ic_mean',0):.4f} ICIR={d.get('icir',0):.4f} RankICIR={d.get('rank_icir',0):.4f}")
    print(f"Manifest: {result.manifest_path}")

if __name__ == "__main__":
    main()
