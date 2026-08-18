#!/usr/bin/env python3
"""Compose single-seed signal runs for P0 (seeds 7/77/123/456) so we can backtest
each single model realization and quantify the portfolio-level realization lottery
vs the ensemble.  Seed-42 single == stored rr_p0 rawrank (already backtested).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path("/home/liuming/.openclaw/workspace/SysQ")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scratch/ablation"))

from build_ensemble_seeds import EXPERIMENT, SIG_ID, compose_phase  # noqa: E402
from qsys.signal.store import SignalStore  # noqa: E402


def seed_run_id(sd: int) -> str:
    return f"{SIG_ID}__rr_p0__seed{sd}__{EXPERIMENT}"


def main() -> None:
    seeds = [int(s) for s in sys.argv[1:]] or [7, 77, 123, 456]
    store = SignalStore(str(ROOT / "data/research"))
    for sd in seeds:
        df = compose_phase("p0", [sd], f"seed{sd}")
        rid = seed_run_id(sd)
        df = df.assign(signal_run_id=rid)
        p = store.save_signal_run(
            SIG_ID, rid, df,
            manifest={
                "artifact_type": "rawrank_single_seed",
                "rawrank_of": f"{SIG_ID}__rr_p0__rawrank__{EXPERIMENT}",
                "shift_trading_days": 0,
                "ranking_score": "daily_zscore(raw_prediction)  # no cap",
                "display_score": "clip(ranking_score, +/-3)",
                "experiment": EXPERIMENT,
                "seeds": [sd],
                "ensemble": "single_model",
                "description": f"single-seed P0 baseline (seed {sd}) for realization-lottery spread",
            },
            overwrite=True,
        )
        print(f"  wrote {p}  ({len(df)} rows / {df['trade_date'].nunique()} days)", flush=True)


if __name__ == "__main__":
    main()
