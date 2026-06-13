#!/usr/bin/env python3
"""Signal Research CLI — UC-4 / UC-6.

Train models and roll signal across a calendar window using a research
YAML config.  Produces SignalRun artifacts + signal evaluation (IC/ICIR).

Usage::

    python scripts/run_research.py --config configs/research/exp.yaml
    python scripts/run_research.py --config configs/research/exp.yaml --overwrite-signal
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qsys.research.signal_pipeline import SignalResearchPipeline
from qsys.research.matrix_job import RollingResearchConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Signal Research — UC-4 / UC-6")
    parser.add_argument("--config", required=True, help="Path to research YAML config")
    parser.add_argument("--overwrite-signal", action="store_true",
                        help="Overwrite existing signal runs")
    parser.add_argument("--overwrite-eval", action="store_true",
                        help="Overwrite existing evaluations")
    args = parser.parse_args()

    config = RollingResearchConfig.from_file(args.config)
    pipeline = SignalResearchPipeline()

    result = pipeline.run(
        config,
        overwrite_signal=args.overwrite_signal,
        overwrite_eval=args.overwrite_eval,
    )

    print(f"\nExperiment: {config.experiment_id}")
    print(f"  Signal runs: {len(result.signal_runs)}")
    for sr in result.signal_runs:
        print(f"    {sr.signal_id} / {sr.signal_run_id}")
    print(f"  Evaluation refs: {len(result.eval_refs)}")

    for eref in result.eval_refs:
        eval_dir = Path(str(eref.eval_id))
        summary_path = eval_dir / "summary.json"
        if summary_path.exists():
            data = json.loads(summary_path.read_text())
            label = eref.label_id
            ic = data.get("ic_mean", 0)
            icir = data.get("icir", 0)
            ric = data.get("rank_ic_mean", 0)
            ricir = data.get("rank_icir", 0)
            print(f"    {label}: IC={ic:.4f} ICIR={icir:.4f} "
                  f"RankIC={ric:.4f} RankICIR={ricir:.4f}")

    print(f"\nManifest: {result.manifest_path}")
    print("Done.")


if __name__ == "__main__":
    main()
