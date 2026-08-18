#!/usr/bin/env python3
"""Compose CORRECT single (seed-42, from the seed bank) for p5/p10/p15 and
save as signal runs under NEW ids (rr_{phase}__rawrank_correct__{EXPERIMENT})
so stored rawrank runs are left untouched.

This is the verified-implementation single baseline: seed bank = the same
correct logic that reproduces stored P0 exactly (rho 1.0).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path("/home/liuming/.openclaw/workspace/SysQ")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scratch/ablation"))

from build_ensemble_seeds import (  # noqa: E402
    SIG_ID, EXPERIMENT, PHASES, LABEL_ID, SEED_RAW_DIR, compose_phase,
)
from qsys.signal.store import SignalStore  # noqa: E402


def correct_single_run_id(phase: str) -> str:
    return f"{SIG_ID}__rr_{phase}__rawrank_correct__{EXPERIMENT}"


def main() -> None:
    phases = sys.argv[1:] or ["p5", "p10", "p15"]
    store = SignalStore(str(ROOT / "data/research"))
    for phase in phases:
        df = compose_phase(phase, [42], "single")
        # re-stamp run id with the correct-single id
        rid = correct_single_run_id(phase)
        df = df.assign(signal_run_id=rid)
        p = store.save_signal_run(
            SIG_ID, rid, df,
            manifest={
                "artifact_type": "rawrank_single_correct",
                "rawrank_of": f"{SIG_ID}__rr_{phase}__rawrank__{EXPERIMENT}",
                "shift_trading_days": PHASES[phase],
                "ranking_score": "daily_zscore(raw_prediction)  # no cap, order-preserving",
                "display_score": "clip(ranking_score, +/-3)",
                "experiment": EXPERIMENT,
                "train_window_trading_days": 504,
                "label_id": LABEL_ID,
                "seeds": [42],
                "ensemble": "single_model",
                "lgb_params": "dict(_DEFAULT_LGB_PARAMS); seed=42  # FIXED correct path",
                "description": "correct-implementation single baseline from seed bank "
                               "(train+shift logic == stored P0's, verified rho 1.0)",
            },
            overwrite=True,
        )
        print(f"  wrote {p}  ({len(df)} rows / {df['trade_date'].nunique()} days)",
              flush=True)


if __name__ == "__main__":
    main()
