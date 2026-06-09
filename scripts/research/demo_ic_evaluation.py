#!/usr/bin/env python3
"""Demo: run IC evaluation with decay + regime + distribution output.

Usage
-----
  python scripts/research/demo_ic_evaluation.py

Prints IC summary, decay pattern, and regime-aware IC to stdout.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qsys.label.store import LabelStore
from qsys.research.evaluation import SignalEvaluator
from qsys.signal.store import SignalStore


def _generate_signal_with_decay(n_dates: int = 60, n_inst: int = 50) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build a signal with known IC decay: high → medium → zero → negative."""
    rng = np.random.default_rng(42)
    dates = pd.bdate_range(start="2026-01-15", periods=n_dates, freq="B")

    signal_rows = []
    label_rows = []
    for i, d in enumerate(dates):
        td = d.strftime("%Y-%m-%d")
        # Deliberate decay: IC starts high, fades, goes negative
        if i < n_dates * 0.25:
            ic = 0.12
        elif i < n_dates * 0.50:
            ic = 0.06
        elif i < n_dates * 0.75:
            ic = 0.02
        else:
            ic = -0.01

        for inst_idx in range(n_inst):
            score = inst_idx / n_inst + float(rng.normal(0, 0.01))
            label_value = inst_idx / n_inst * ic + float(rng.normal(0, 0.01))
            signal_rows.append({
                "trade_date": td, "data_date": td,
                "instrument": f"{inst_idx:04d}.SZ",
                "signal_id": "demo", "signal_run_id": "ic_eval_demo",
                "score": float(score),
            })
            label_rows.append({
                "trade_date": td, "instrument": f"{inst_idx:04d}.SZ",
                "label_id": "fwd_ret_5d", "horizon": 5,
                "label_value": float(label_value),
            })

    return pd.DataFrame(signal_rows), pd.DataFrame(label_rows)


def main() -> None:
    base = Path("data/demo_ic_eval")
    signal, labels = _generate_signal_with_decay()

    store = SignalStore(str(base))
    store.save_signal_run("demo", "ic_eval_demo", signal, overwrite=True, check_no_lookahead=False)

    lstore = LabelStore(str(base))
    lstore.save_labels("fwd_ret_5d", labels, overwrite=True)

    evaluator = SignalEvaluator(str(base))
    result = evaluator.evaluate(
        signal_id="demo", signal_run_id="ic_eval_demo",
        label_id="fwd_ret_5d", overwrite=True,
    )

    print("=== IC Summary ===")
    print(f"  IC mean:           {result.ic_mean:.4f}")
    print(f"  ICIR:              {result.icir:.4f}")
    print(f"  IC > 0:            {result.ic_positive_ratio:.1%}")
    print(f"  IC extreme (2σ):   {result.ic_extreme_ratio:.1%}")
    print()

    print("=== IC Decay (5 segments, early → late) ===")
    for i, ir in enumerate(result.decay_icirs or []):
        tag = ""
        if i > 0 and ir is not None:
            prev = result.decay_icirs[i - 1]
            if prev is not None and ir < prev * 0.5:
                tag = "  <<< DECAY"
        print(f"  Segment {i+1}: ICIR = {ir:.2f}{tag}")
    print()

    print("=== Regime-aware IC ===")
    if result.regime_ic:
        for regime, info in result.regime_ic.items():
            print(f"  {regime:>8}: n={info['n_days']:3d}  IC={info['ic_mean']:.4f}  ICIR={info['icir']:.2f}  IC>0={info['positive_ratio']:.0%}")
    else:
        print("  (index CSV not available for regime classification)")
    print()

    # Read the written summary.json
    summary_path = base / "signals" / "demo" / "ic_eval_demo" / "eval" / "fwd_ret_5d" / "summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text())
        print("=== summary.json (new evaluation fields) ===")
        for key in ["ic_positive_ratio", "ic_quantiles", "ic_extreme_ratio", "decay_icirs", "regime_ic"]:
            if key in summary and summary[key] is not None:
                val = json.dumps(summary[key], indent=6) if isinstance(summary[key], dict) else summary[key]
                print(f"  {key}: {val}")

    # Cleanup
    import shutil
    shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    main()
