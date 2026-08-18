#!/usr/bin/env python3
"""Portfolio backtests for the multi-seed ensemble study (Task 7).

Runs ens3 (seeds {42,7,77}) and ens5 (seeds {42,7,77,123,456}) for every phase,
all Top5, equal-weight entry + hold drift, rank_exit on, dead exit rules,
20d cadence at the phase's grid offset — exactly the run_raw_rank_phases.py
config, only the signal_run_id changes.  The single-model backtests already
exist in the index (P0 afdd7696 / P5 25f9f4cb / P10 bf2fbcd8 / P15 c91ea5ae).

Requires the ensemble compose phase to have saved the ens3/ens5 signal runs.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RESEARCH_ROOT = Path("/home/liuming/.openclaw/workspace/SysQ/data/research")
CLI = REPO / "scripts" / "research" / "backtest_from_signal.py"

S180_SIGNAL_ID = "fwd_ret_180d_raw__daily_zscore"
EXPERIMENT = "financial_rc_180d_rolling_5y_to_202607_v3"
START, END = "2021-01-04", "2026-07-31"

NEVER = {
    "score_delta_min_observations": "1000000000",
    "posterior_stop_loss": "0.999",
    "winner_activation_return": "0.9999",
    "winner_trailing_stop": "0.999",
    "stale_after_days": "10000",
    "replacement_rank_gap": "1000000",
}

BASE = {
    "signal_id": S180_SIGNAL_ID,
    "research_root": str(RESEARCH_ROOT),
    "score_column": "score",
    "top_n": "5",
    "commission": "0.0003",
    "stamp_duty": "0.001",
    "min_commission": "5.0",
    "slippage": "0.001",
    "rebalance_freq": "20d",
    "rebalance_offset": "0",
    "holding_policy": "posterior_confirmed",
    "strategy_template_id": "posterior_confirmed_top5_financial_rc_50_50_v1",
    "start_date": START,
    "end_date": END,
    "initial_capital": "10000000",
    "overwrite": True,
    **NEVER,
    "rank_exit": True,
}

PHASE_OFFSET = {"p0": "0", "p5": "5", "p10": "10", "p15": "15"}


def ens_run_id(phase: str, tag: str) -> str:
    return f"{S180_SIGNAL_ID}__rr_{phase}__{tag}__{EXPERIMENT}"


def build_cmd(name: str, signal_run_id: str, offset: str, output_dir: Path) -> list[str]:
    params = dict(BASE)
    params["signal_run_id"] = signal_run_id
    params["rebalance_offset"] = offset
    cmd = [sys.executable, str(CLI)]
    for k, v in params.items():
        flag = f"--{k.replace('_', '-')}"
        if isinstance(v, bool):
            if v:
                cmd.append(flag)
            continue
        cmd.append(flag)
        cmd.append(str(v))
    cmd.extend(["--output-dir", str(output_dir)])
    return cmd


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tags", default="ens3,ens5")
    ap.add_argument("--phases", default="p0,p5,p10,p15")
    ap.add_argument("--parallel", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    phases = [p.strip() for p in args.phases.split(",") if p.strip()]
    selected = [f"RR_{p}_{tag}" for p in phases for tag in tags]

    out_root = REPO / "data" / "research" / "ablation" / "ensemble_pf"
    out_root.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        for name in selected:
            p, tag = name.split("_")[1], name.split("_")[2]
            print(" ".join(build_cmd(name, ens_run_id(p, tag), PHASE_OFFSET[p], out_root / name)))
        return 0

    manifest = {}
    running: dict[subprocess.Popen, str] = {}
    max_par = max(1, min(args.parallel, len(selected)))
    done, idx = 0, 0
    while idx < len(selected) or running:
        while idx < len(selected) and len(running) < max_par:
            name = selected[idx]
            p, tag = name.split("_")[1], name.split("_")[2]
            out = out_root / name
            cmd = build_cmd(name, ens_run_id(p, tag), PHASE_OFFSET[p], out)
            idx += 1
            print(f"[start] {name}", flush=True)
            logf = open(out_root / f"{name}.log", "w")
            pr = subprocess.Popen(cmd, cwd=REPO, stdout=logf, stderr=subprocess.STDOUT, text=True)
            pr._logf = logf  # type: ignore[attr-defined]
            running[pr] = name
        for pr in list(running):
            if pr.poll() is not None:
                pr._logf.close()  # type: ignore[attr-defined]
                name = running.pop(pr)
                log_path = out_root / f"{name}.log"
                text = log_path.read_text(errors="replace") if log_path.exists() else ""
                tail = text.strip().splitlines()[-8:]
                print(f"=== {name} (exit {pr.returncode}) ===")
                print("\n".join(tail))
                manifest[name] = {"status": "completed" if pr.returncode == 0 else "failed"}
                done += 1
        if running:
            time.sleep(2)
    (out_root / "ens_run_manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"\nall done: {done}/{len(selected)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
